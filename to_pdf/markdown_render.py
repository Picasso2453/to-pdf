"""Markdown -> paginated rich-text document -> PDF.

Markdown is converted to HTML with markdown-it-py (CommonMark + GFM tables,
strikethrough, task lists, autolinks, footnotes), rendered by Qt's rich-text
engine (``QTextDocument``) and paginated by the document layout itself.

Everything is measured in 96-dpi "layout pixels" (QTextDocument's unit when no
paint device is attached; 1 pt = 96/72 px). :func:`paint_page` draws one page
in those units and is shared by the on-screen preview and the PDF exporter, so
the preview is exactly the PDF.

Qt only supports a subset of HTML/CSS
(https://doc.qt.io/qt-6/richtext-html-subset.html), so the markdown-it render
rules are overridden to emit constructs Qt understands: code blocks and block
quotes become tables, column alignment becomes ``align`` attributes, task-list
checkboxes become glyphs and images get explicit pixel sizes.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QImageIOHandler,
    QImageReader,
    QPageLayout,
    QPageSize,
    QPainter,
    QPalette,
    QPdfWriter,
    QTextDocument,
)

from .model import MM, PAGE_SIZES

PX_PER_PT = 96 / 72

# Palette ---------------------------------------------------------------
TEXT = "#1d232b"
MUTED = "#57606a"
ACCENT = "#0e7c66"
BORDER = "#d0d7de"
RULE = "#d8dee4"
CODE_BG = "#f6f8fa"
INLINE_CODE_BG = "#eff1f3"
FOOTER = "#8c959f"

BODY_FONTS = ["Segoe UI", "Helvetica Neue", "Arial", "sans-serif"]
MONO_FONTS = ["Cascadia Mono", "Consolas", "Courier New", "monospace"]

CHECKBOX_OPEN = "☐"
CHECKBOX_DONE = "☑"


@dataclass
class MarkdownSettings:
    size: str = "A4"  # key of model.PAGE_SIZES
    orientation: str = "portrait"  # "portrait" | "landscape"
    margin_mm: float = 20.0
    font_pt: float = 11.0
    page_numbers: bool = True


# Geometry --------------------------------------------------------------

def _page_size_pt(settings: MarkdownSettings) -> tuple[float, float]:
    w, h = PAGE_SIZES.get(settings.size, PAGE_SIZES["A4"])
    if settings.orientation == "landscape":
        w, h = max(w, h), min(w, h)
    else:
        w, h = min(w, h), max(w, h)
    return w, h


def page_size_px(settings: MarkdownSettings) -> tuple[float, float]:
    w, h = _page_size_pt(settings)
    return w * PX_PER_PT, h * PX_PER_PT


def margin_px(settings: MarkdownSettings) -> float:
    return max(0.0, settings.margin_mm) * MM * PX_PER_PT


def _content_size_px(settings: MarkdownSettings) -> tuple[float, float]:
    w, h = page_size_px(settings)
    m = margin_px(settings)
    # Keep a usable content box even for absurd margins.
    return max(w - 2 * m, 50.0), max(h - 2 * m, 50.0)


# Styling ---------------------------------------------------------------

def _css_fonts(families: list[str]) -> str:
    return ", ".join(f"'{f}'" if " " in f else f for f in families)


def _stylesheet(settings: MarkdownSettings) -> str:
    base = settings.font_pt
    px = base * PX_PER_PT  # 1em in layout px
    body = _css_fonts(BODY_FONTS)
    mono = _css_fonts(MONO_FONTS)

    def em(x: float) -> str:
        return f"{x * px:.1f}px"

    heads = []
    # (scale, margin-top em, margin-bottom em, weight)
    spec = {
        1: (2.0, 1.2, 0.3, 600),
        2: (1.55, 1.35, 0.3, 600),
        3: (1.3, 1.3, 0.45, 600),
        4: (1.1, 1.2, 0.4, 600),
        5: (1.0, 1.1, 0.35, 700),
        6: (0.95, 1.1, 0.35, 700),
    }
    for level, (scale, top, bottom, weight) in spec.items():
        extra = f" color: {MUTED};" if level == 6 else ""
        heads.append(
            f"p.h{level} {{ font-size: {base * scale:.2f}pt; font-weight: {weight};"
            f" margin-top: {em(top)}; margin-bottom: {em(bottom)};"
            f" line-height: 120%;{extra} }}"
        )
    return "\n".join([
        f"body {{ font-family: {body}; font-size: {base:.2f}pt; }}",
        f"p {{ margin-top: 0px; margin-bottom: {em(0.75)}; line-height: 140%; }}",
        f"p.img {{ line-height: 100%; }}",
        f"li {{ margin-top: 0px; margin-bottom: {em(0.12)}; line-height: 135%; }}",
        f"li p {{ margin-bottom: {em(0.25)}; }}",
        f"ul, ol {{ margin-top: 0px; margin-bottom: {em(0.75)}; }}",
        f"ul.tasks {{ list-style-type: none; }}",
        "li ul, li ol { margin-bottom: 0px; }",
        *heads,
        "p.first { margin-top: 0px; }",
        f"hr {{ margin-top: {em(0.9)}; margin-bottom: {em(0.9)}; }}",
        f"hr.hrule {{ margin-top: 0px; margin-bottom: {em(0.8)}; }}",
        f"a {{ color: {ACCENT}; text-decoration: underline; }}",
        f"code {{ font-family: {mono}; font-size: {base * 0.88:.2f}pt;"
        f" background-color: {INLINE_CODE_BG}; }}",
        f"pre {{ font-family: {mono}; font-size: {base * 0.86:.2f}pt;"
        f" margin-top: 0px; margin-bottom: 0px; line-height: 135%; }}",
        f"td.bqbody {{ color: {MUTED}; }}",
        f"td.cell, th.cell {{ line-height: 115%; }}",
        f"span.missing {{ color: {MUTED}; font-style: italic; }}",
        f"ol.footnotes {{ font-size: {base * 0.88:.2f}pt; color: {MUTED}; }}",
        f"sup {{ font-size: {base * 0.8:.2f}pt; }}",
    ])


# Markdown -> HTML ------------------------------------------------------

def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def _resolve_local_image(src: str, base_dir: str | None) -> str | None:
    """Return an existing local file path for an image reference, else None."""
    candidates: list[str] = []
    if src.lower().startswith("file:"):
        local = QUrl(src).toLocalFile()
        if local:
            candidates.append(local)
    else:
        for s in dict.fromkeys([src, unquote(src)]):
            if os.path.isabs(s):
                candidates.append(s)
            elif base_dir:
                candidates.append(os.path.join(base_dir, s))
    for c in candidates:
        c = os.path.normpath(c)
        if os.path.isfile(c):
            return c
    return None


def _image_natural_size(path: str) -> tuple[int, int] | None:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid():
        if not reader.canRead():
            return None
        img = reader.read()
        if img.isNull():
            return None
        return img.width(), img.height()
    w, h = size.width(), size.height()
    try:
        t = reader.transformation()
        if t & QImageIOHandler.Transformation.TransformationRotate90:
            w, h = h, w
    except Exception:  # pragma: no cover - older bindings
        pass
    return w, h


class _Ctx:
    """Per-render state passed to the markdown-it rules through ``env``."""

    def __init__(self, settings: MarkdownSettings, base_dir: str | None):
        self.settings = settings
        self.base_dir = base_dir
        self.content_w, _ = _content_size_px(settings)
        self.images: list[tuple[str, str]] = []  # (file url, local path)


def _code_table(code: str) -> str:
    code = code.expandtabs(4).rstrip("\n")
    return (
        f'<table width="100%" cellspacing="0" cellpadding="11" border="1"'
        f' bgcolor="{CODE_BG}" style="border-collapse: collapse;'
        f' border-color: {RULE}; border-style: solid; margin-bottom: 12px;">'
        f"<tr><td><pre>{_esc(code)}</pre></td></tr></table>\n"
    )


def _rule_fence(self, tokens, idx, options, env):
    return _code_table(tokens[idx].content)


def _rule_code_inline(self, tokens, idx, options, env):
    # Thin non-breaking spaces give the grey background a little padding.
    return f"<code> {_esc(tokens[idx].content)} </code>"


def _rule_heading_open(self, tokens, idx, options, env):
    # <hN> is avoided on purpose: Qt applies a relative size adjustment to
    # headings on top of any CSS font-size, so h5/h6 would shrink.
    tag = tokens[idx].tag
    first = " first" if idx == 0 else ""
    return f'<p class="{tag}{first}">'


def _rule_heading_close(self, tokens, idx, options, env):
    tag = tokens[idx].tag
    if tag in ("h1", "h2"):
        return '</p>\n<hr class="hrule" />\n'
    return "</p>\n"


def _rule_hr(self, tokens, idx, options, env):
    return "<hr />\n"


def _close_index(tokens, idx) -> int:
    """Index of the token closing the block opened at ``idx``."""
    open_tok = tokens[idx]
    close_type = open_tok.type.replace("_open", "_close")
    for j in range(idx + 1, len(tokens)):
        if tokens[j].type == close_type and tokens[j].level == open_tok.level:
            return j
    return len(tokens) - 1


def _ends_container(tokens, close_idx) -> bool:
    """True when the block closed at ``close_idx`` is the last thing in a quote."""
    nxt = tokens[close_idx + 1] if close_idx + 1 < len(tokens) else None
    return nxt is not None and nxt.type in ("blockquote_close", "footnote_close")


def _rule_paragraph_open(self, tokens, idx, options, env):
    if tokens[idx].hidden:
        return ""
    # A trailing margin inside a quote would stretch the bar below the text.
    style = ' style="margin-bottom: 0px;"' if _ends_container(tokens, _close_index(tokens, idx)) else ""
    nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
    if nxt is not None and nxt.type == "inline" and nxt.children:
        kinds = {c.type for c in nxt.children if not (c.type == "text" and not c.content.strip())}
        if kinds and kinds <= {"image", "softbreak", "hardbreak"}:
            return f'<p class="img"{style}>'
    return f"<p{style}>"


def _rule_blockquote_open(self, tokens, idx, options, env):
    last = _ends_container(tokens, _close_index(tokens, idx))
    bottom = 0 if last else 14
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="margin-top: 6px; margin-bottom: {bottom}px;"><tr>'
        f'<td width="4" bgcolor="{BORDER}"></td>'
        '<td class="bqbody" style="padding-left: 14px; padding-top: 1px; padding-bottom: 1px;">\n'
    )


def _rule_blockquote_close(self, tokens, idx, options, env):
    return "</td></tr></table>\n"


def _rule_table_open(self, tokens, idx, options, env):
    return (
        '<table cellspacing="0" cellpadding="6" border="1"'
        f' style="border-collapse: collapse; border-color: {BORDER};'
        ' border-style: solid; margin-bottom: 14px;">\n'
    )


def _cell_align(token: Token) -> str:
    style = token.attrGet("style") or ""
    for part in str(style).split(";"):
        key, _, value = part.partition(":")
        if key.strip() == "text-align" and value.strip() in ("left", "center", "right"):
            return f' align="{value.strip()}"'
    return ""


def _rule_th_open(self, tokens, idx, options, env):
    return f'<th class="cell" bgcolor="{CODE_BG}"{_cell_align(tokens[idx])}>'


def _rule_td_open(self, tokens, idx, options, env):
    return f'<td class="cell"{_cell_align(tokens[idx])} valign="top">'


def _rule_bullet_list_open(self, tokens, idx, options, env):
    if "contains-task-list" in str(tokens[idx].attrGet("class") or ""):
        return '<ul class="tasks">\n'
    return "<ul>\n"


def _rule_ordered_list_open(self, tokens, idx, options, env):
    start = tokens[idx].attrGet("start")
    return f'<ol start="{int(start)}">\n' if start not in (None, "") else "<ol>\n"


def _rule_list_item_open(self, tokens, idx, options, env):
    return "<li>"


def _rule_image(self, tokens, idx, options, env):
    token = tokens[idx]
    ctx: _Ctx = env["_ctx"]
    src = str(token.attrGet("src") or "")
    alt = self.renderInlineAsText(token.children or [], options, env).strip()
    if src.lower().startswith(("http://", "https://")):
        label = alt or src
        return f'<a href="{_attr(src)}">{_esc(label)}</a>'
    path = _resolve_local_image(src, ctx.base_dir) if src else None
    size = _image_natural_size(path) if path else None
    if not path or not size or size[0] <= 0 or size[1] <= 0:
        label = alt or os.path.basename(unquote(src)) or "image"
        return f'<span class="missing">[{_esc(label)}]</span>'
    w, h = size
    max_w = ctx.content_w
    if w > max_w:
        h = h * max_w / w
        w = max_w
    url = QUrl.fromLocalFile(path).toString()
    ctx.images.append((url, path))
    return (
        f'<img src="{_attr(url)}" width="{int(round(w))}" height="{int(round(h))}"'
        f' alt="{_attr(alt)}" />'
    )


def _rule_footnote_block_open(self, tokens, idx, options, env):
    return '<hr />\n<ol class="footnotes">\n'


def _rule_footnote_block_close(self, tokens, idx, options, env):
    return "</ol>\n"


def _rule_footnote_open(self, tokens, idx, options, env):
    return "<li>"


def _rule_footnote_caption(self, tokens, idx, options, env):
    return str(tokens[idx].meta["id"] + 1)


def _rule_empty(self, tokens, idx, options, env):
    return ""


def _rule_footnote_anchor(self, tokens, idx, options, env):
    return ""


def _validate_link(url: str) -> bool:
    return not url.strip().lower().startswith(("javascript:", "vbscript:", "data:text"))


def _make_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": False})
    md.enable(["table", "strikethrough", "linkify"])
    md.use(tasklists_plugin)
    md.use(footnote_plugin)
    # markdown-it rejects file: URLs by default; local images need them.
    md.validateLink = _validate_link  # type: ignore[method-assign]
    rules = {
        "fence": _rule_fence,
        "code_block": _rule_fence,
        "code_inline": _rule_code_inline,
        "heading_open": _rule_heading_open,
        "heading_close": _rule_heading_close,
        "hr": _rule_hr,
        "paragraph_open": _rule_paragraph_open,
        "blockquote_open": _rule_blockquote_open,
        "blockquote_close": _rule_blockquote_close,
        "table_open": _rule_table_open,
        # No <thead>: Qt repeats header rows on every page a table touches,
        # including a lone header when only the table's margin spills over.
        "thead_open": _rule_empty,
        "thead_close": _rule_empty,
        "tbody_open": _rule_empty,
        "tbody_close": _rule_empty,
        "th_open": _rule_th_open,
        "td_open": _rule_td_open,
        "bullet_list_open": _rule_bullet_list_open,
        "ordered_list_open": _rule_ordered_list_open,
        "list_item_open": _rule_list_item_open,
        "image": _rule_image,
        "footnote_block_open": _rule_footnote_block_open,
        "footnote_block_close": _rule_footnote_block_close,
        "footnote_open": _rule_footnote_open,
        "footnote_anchor": _rule_footnote_anchor,
        "footnote_caption": _rule_footnote_caption,
    }
    for name, fn in rules.items():
        md.add_render_rule(name, fn)
    return md


_PARSER: MarkdownIt | None = None


def _parser() -> MarkdownIt:
    global _PARSER
    if _PARSER is None:
        _PARSER = _make_parser()
    return _PARSER


def _replace_checkboxes(tokens: list[Token]) -> None:
    """Turn the task-list plugin's ``<input>`` tokens into glyph text."""
    for tok in tokens:
        if tok.type != "inline" or not tok.children:
            continue
        for i, child in enumerate(tok.children):
            if child.type == "html_inline" and "task-list-item-checkbox" in child.content:
                glyph = CHECKBOX_DONE if 'checked="checked"' in child.content else CHECKBOX_OPEN
                text = Token("text", "", 0)
                text.content = glyph
                tok.children[i] = text
            elif child.type == "html_inline":  # never pass raw HTML through
                text = Token("text", "", 0)
                text.content = child.content
                tok.children[i] = text


def _render(text: str, settings: MarkdownSettings, base_dir: str | None) -> tuple[str, _Ctx]:
    md = _parser()
    ctx = _Ctx(settings, base_dir)
    env: dict = {"_ctx": ctx}
    tokens = md.parse(text or "", env)
    _replace_checkboxes(tokens)
    body = md.renderer.render(tokens, md.options, env)
    return f"<html><body>\n{body}</body></html>", ctx


def markdown_to_html(text: str, settings: MarkdownSettings, base_dir: str | None = None) -> str:
    """Convert markdown to the Qt-flavoured HTML fed to :func:`build_document`."""
    return _render(text, settings, base_dir)[0]


# Document --------------------------------------------------------------

def build_document(text: str, settings: MarkdownSettings, base_dir: str | None = None) -> QTextDocument:
    """Build a laid-out, paginated document; read ``doc.pageCount()``."""
    html_text, ctx = _render(text, settings, base_dir)
    doc = QTextDocument()
    font = QFont()
    font.setFamilies(BODY_FONTS)
    font.setPointSizeF(settings.font_pt)
    doc.setDefaultFont(font)
    doc.setDocumentMargin(0)
    doc.setUseDesignMetrics(True)
    doc.setDefaultStyleSheet(_stylesheet(settings))
    cw, ch = _content_size_px(settings)
    doc.setPageSize(QSizeF(cw, ch))
    for url, path in ctx.images:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        img = reader.read()
        if not img.isNull():
            doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(url), img)
    doc.setHtml(html_text)
    doc.setPageSize(QSizeF(cw, ch))  # re-assert after setHtml; forces layout
    doc.documentLayout().documentSize()
    return doc


def paint_page(painter: QPainter, doc: QTextDocument, settings: MarkdownSettings, index: int) -> None:
    """Paint page ``index`` (0-based) with its top-left at the painter origin."""
    pw, ph = page_size_px(settings)
    m = margin_px(settings)
    cw, ch = _content_size_px(settings)
    count = max(1, doc.pageCount())

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.fillRect(QRectF(0, 0, pw, ph), QColor("#ffffff"))

    painter.save()
    painter.translate(m, m)
    painter.setClipRect(QRectF(0, 0, cw, ch), Qt.ClipOperation.IntersectClip)
    painter.translate(0, -index * ch)
    ctx = QAbstractTextDocumentLayout.PaintContext()
    ctx.clip = QRectF(0, index * ch, cw, ch)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(RULE))
    palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.WindowText, QColor(RULE))
    palette.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    ctx.palette = palette
    doc.documentLayout().draw(painter, ctx)
    painter.restore()

    if settings.page_numbers:
        font = QFont()
        font.setFamilies(BODY_FONTS)
        font.setPointSizeF(max(6.0, settings.font_pt * 0.8))
        painter.setFont(font)
        painter.setPen(QColor(FOOTER))
        footer_h = max(m, 24.0)
        rect = QRectF(0, ph - footer_h, pw, footer_h)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), f"{index + 1} / {count}")
    painter.restore()


def export_markdown_pdf(text: str, settings: MarkdownSettings, out_path: str,
                        base_dir: str | None = None, title: str | None = None) -> int:
    """Write the markdown as a PDF; returns the page count."""
    doc = build_document(text, settings, base_dir)
    count = max(1, doc.pageCount())
    pw, ph = page_size_px(settings)
    w_pt, h_pt = _page_size_pt(settings)
    part = out_path + ".part"

    writer = QPdfWriter(part)
    writer.setResolution(96)
    writer.setCreator("To PDF")
    writer.setTitle(title if title is not None else os.path.splitext(os.path.basename(out_path))[0])
    portrait = QPageSize(QSizeF(min(w_pt, h_pt), max(w_pt, h_pt)), QPageSize.Unit.Point,
                         "", QPageSize.SizeMatchPolicy.FuzzyMatch)
    orientation = (QPageLayout.Orientation.Landscape if w_pt > h_pt
                   else QPageLayout.Orientation.Portrait)
    writer.setPageLayout(QPageLayout(portrait, orientation, QMarginsF(0, 0, 0, 0)))

    painter = QPainter()
    ok = False
    try:
        if not painter.begin(writer):
            raise OSError(f"Cannot write PDF: {out_path}")
        # The writer's device units should be 1/96 inch; scale if they are not.
        sx = writer.width() / pw if pw else 1.0
        sy = writer.height() / ph if ph else 1.0
        for i in range(count):
            if i:
                writer.newPage()
            painter.save()
            if abs(sx - 1.0) > 1e-3 or abs(sy - 1.0) > 1e-3:
                painter.scale(sx, sy)
            paint_page(painter, doc, settings, i)
            painter.restore()
        ok = painter.end()
        if not ok:
            raise OSError(f"Cannot write PDF: {out_path}")
    finally:
        if painter.isActive():
            painter.end()
        del painter
        del writer
        if not ok:
            try:
                os.remove(part)
            except OSError:
                pass
    os.replace(part, out_path)
    return count
