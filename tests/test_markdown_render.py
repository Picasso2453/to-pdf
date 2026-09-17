import os

import pytest
from pypdf import PdfReader
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtPdf import QPdfDocument

from to_pdf.markdown_render import (
    PX_PER_PT,
    MarkdownSettings,
    build_document,
    export_markdown_pdf,
    margin_px,
    markdown_to_html,
    page_size_px,
    paint_page,
)
from to_pdf.model import MM, PAGE_SIZES

A4_W, A4_H = PAGE_SIZES["A4"]


@pytest.fixture(scope="module", autouse=True)
def _fonts():
    """The offscreen platform plugin on Windows starts with an empty font
    database, so every glyph would silently vanish. Register the system fonts
    the renderer asks for (the real app uses the native plugin and has them)."""
    from PySide6.QtGui import QFontDatabase

    if not QFontDatabase.families():
        font_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf",
                     "seguisb.ttf", "seguisym.ttf", "consola.ttf", "consolab.ttf",
                     "CascadiaMono.ttf", "arial.ttf"):
            path = os.path.join(font_dir, name)
            if os.path.exists(path):
                QFontDatabase.addApplicationFont(path)
    if not QFontDatabase.families():
        pytest.skip("no fonts available to the Qt platform plugin")


def _pdf_text(path):
    # pypdf reports inter-word gaps as tabs; normalise all whitespace.
    raw = " ".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return " ".join(raw.split())


def _render_pdf_page(path, index=0, dpi=72):
    doc = QPdfDocument()
    doc.load(path)
    pt = doc.pagePointSize(index)
    size = QSize(round(pt.width() * dpi / 72), round(pt.height() * dpi / 72))
    raw = doc.render(index, size)
    # QPdfDocument leaves untouched background transparent: composite on white.
    out = QImage(size, QImage.Format.Format_RGB32)
    out.fill(Qt.GlobalColor.white)
    p = QPainter(out)
    p.drawImage(0, 0, raw)
    p.end()
    doc.close()
    return out


def _make_png(path, w, h, color="#0e7c66"):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(str(path))


# Geometry -------------------------------------------------------------

def test_page_size_px_a4_portrait_and_landscape():
    w, h = page_size_px(MarkdownSettings(size="A4", orientation="portrait"))
    assert w == pytest.approx(A4_W * 96 / 72)
    assert h == pytest.approx(A4_H * 96 / 72)
    lw, lh = page_size_px(MarkdownSettings(size="A4", orientation="landscape"))
    assert (lw, lh) == (pytest.approx(h), pytest.approx(w))
    assert PX_PER_PT == pytest.approx(4 / 3)


def test_margin_px():
    assert margin_px(MarkdownSettings(margin_mm=20)) == pytest.approx(20 * MM * 96 / 72)
    assert margin_px(MarkdownSettings(margin_mm=0)) == 0
    # 25.4 mm is one inch = 96 layout px
    assert margin_px(MarkdownSettings(margin_mm=25.4)) == pytest.approx(96)


# Pagination -----------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   \n\n\t  \n"])
def test_empty_text_gives_one_page(text):
    doc = build_document(text, MarkdownSettings())
    assert doc.pageCount() >= 1


def test_long_text_paginates_and_grows():
    para = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor."
    s = MarkdownSettings()
    counts = [build_document("\n\n".join([para] * n), s).pageCount() for n in (1, 150, 300)]
    assert counts[0] == 1
    assert counts[1] > 1
    assert counts[2] > counts[1]


def test_larger_font_needs_more_pages():
    text = "\n\n".join(["Some reasonably long paragraph of body text for layout."] * 200)
    small = build_document(text, MarkdownSettings(font_pt=9)).pageCount()
    large = build_document(text, MarkdownSettings(font_pt=16)).pageCount()
    assert large > small


def test_paint_page_every_index():
    text = "# Title\n\n" + "\n\n".join(f"Paragraph {i} with some text." for i in range(200))
    s = MarkdownSettings()
    doc = build_document(text, s)
    w, h = page_size_px(s)
    assert doc.pageCount() > 1
    for i in range(doc.pageCount()):
        img = QImage(round(w), round(h), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        paint_page(p, doc, s, i)
        p.end()
        # Page background is painted white, and there is dark text on it.
        assert QColor(img.pixel(2, 2)) == QColor("white")
        m = margin_px(s)
        dark = any(
            QColor(img.pixel(x, y)).lightness() < 128
            for y in range(round(m), round(h - m), 3)
            for x in range(round(m), round(m) + 200, 3)
        )
        assert dark, f"page {i} has no text"


# HTML structure --------------------------------------------------------

def test_task_list_glyphs():
    html = markdown_to_html("- [ ] todo\n- [x] done\n", MarkdownSettings())
    assert "\u2610 todo" in html
    assert "\u2611 done" in html
    assert "<input" not in html


def test_table_strikethrough_nested_list_html():
    md = (
        "| Left | Centre | Right |\n"
        "|:-----|:------:|------:|\n"
        "| a | b | c |\n\n"
        "~~gone~~\n\n"
        "- one\n"
        "  - two\n"
        "    - three\n"
    )
    html = markdown_to_html(md, MarkdownSettings())
    assert "<table" in html and "<th" in html and "<td" in html
    assert 'align="center"' in html and 'align="right"' in html
    assert "<s>gone</s>" in html
    # Three nested unordered lists
    assert html.count("<ul") == 3
    one, two, three = html.index("one"), html.index("two"), html.index("three")
    assert html.count("</ul>", one, three) == 0  # still inside the outer list


def test_four_space_nested_list_nests():
    html = markdown_to_html("1. one\n    - inner\n2. two\n", MarkdownSettings())
    assert html.index("<ol") < html.index("<ul") < html.index("inner") < html.index("</ul>") < html.index("two")


def test_raw_html_is_escaped():
    html = markdown_to_html("hello <b>bold</b>\n\n<div>block</div>\n", MarkdownSettings())
    assert "<b>" not in html and "<div>" not in html
    assert "&lt;b&gt;" in html


def test_code_block_is_escaped_table():
    html = markdown_to_html("```\nif a < b && c:\n    pass\n```\n", MarkdownSettings())
    assert "<pre>" in html and "<table" in html
    assert "a &lt; b &amp;&amp; c" in html


def test_remote_image_becomes_link():
    html = markdown_to_html("![Logo](https://example.com/x.png)", MarkdownSettings())
    assert '<a href="https://example.com/x.png">Logo</a>' in html
    assert "<img" not in html


def test_image_scaled_to_content_width(tmp_path):
    _make_png(tmp_path / "wide.png", 3000, 1000)
    _make_png(tmp_path / "small.png", 100, 50)
    s = MarkdownSettings()
    html = markdown_to_html("![w](wide.png)\n\n![s](small.png)", s, str(tmp_path))
    content_w = page_size_px(s)[0] - 2 * margin_px(s)
    assert f'width="{round(content_w)}" height="{round(content_w / 3)}"' in html
    assert 'width="100" height="50"' in html


def test_image_absolute_and_file_url(tmp_path):
    png = tmp_path / "pic.png"
    _make_png(png, 40, 30)
    from PySide6.QtCore import QUrl
    url = QUrl.fromLocalFile(str(png)).toString()
    for ref in (str(png).replace("\\", "/"), url):
        html = markdown_to_html(f"![x](<{ref}>)", MarkdownSettings())
        assert "<img" in html, ref


# PDF export -----------------------------------------------------------

RICH = """# Heading Alpha

Paragraph bravo with **bold** and `codeinline`.

| Col | Value |
|-----|------:|
| cellcharlie | 42 |

```python
def functiondelta():
    return 1
```
"""


def test_export_page_count_and_mediabox(tmp_path):
    out = tmp_path / "doc.pdf"
    text = RICH + "\n\n".join(["Filler paragraph text for pagination."] * 120)
    n = export_markdown_pdf(text, MarkdownSettings(), str(out), title="Doc title")
    assert n > 1
    assert not os.path.exists(str(out) + ".part")
    reader = PdfReader(str(out))
    assert len(reader.pages) == n
    for page in reader.pages:
        box = page.mediabox
        assert float(box.width) == pytest.approx(A4_W, abs=1)
        assert float(box.height) == pytest.approx(A4_H, abs=1)
    meta = reader.metadata
    assert meta.title == "Doc title"
    assert meta.get("/Creator") == "To PDF"


def test_export_landscape_mediabox(tmp_path):
    out = tmp_path / "land.pdf"
    export_markdown_pdf("hello", MarkdownSettings(orientation="landscape"), str(out))
    box = PdfReader(str(out)).pages[0].mediabox
    assert float(box.width) == pytest.approx(A4_H, abs=1)
    assert float(box.height) == pytest.approx(A4_W, abs=1)


def test_export_empty_is_one_page(tmp_path):
    out = tmp_path / "empty.pdf"
    assert export_markdown_pdf("", MarkdownSettings(), str(out)) == 1
    assert len(PdfReader(str(out)).pages) == 1


def test_export_text_extraction(tmp_path):
    out = tmp_path / "rich.pdf"
    export_markdown_pdf(RICH, MarkdownSettings(), str(out))
    text = _pdf_text(str(out))
    for word in ("Alpha", "bravo", "codeinline", "cellcharlie", "functiondelta"):
        assert word in text


def test_page_numbers_toggle(tmp_path):
    body = "\n\n".join(["Some body text here."] * 150)
    on = tmp_path / "on.pdf"
    off = tmp_path / "off.pdf"
    n = export_markdown_pdf(body, MarkdownSettings(page_numbers=True), str(on))
    assert n >= 2
    export_markdown_pdf(body, MarkdownSettings(page_numbers=False), str(off))
    assert f"1 / {n}" in _pdf_text(str(on))
    assert f"2 / {n}" in _pdf_text(str(on))
    assert f"1 / {n}" not in _pdf_text(str(off))


def test_relative_image_embedded_and_missing_alt(tmp_path):
    _make_png(tmp_path / "red.png", 400, 200, "#ff0000")
    md_file = tmp_path / "note.md"
    text = "![red box](red.png)\n\n![vanished picture](nope.png)\n"
    md_file.write_text(text, encoding="utf-8")
    out = tmp_path / "img.pdf"
    export_markdown_pdf(text, MarkdownSettings(page_numbers=False), str(out),
                        base_dir=str(md_file.parent))

    reader = PdfReader(str(out))
    xobjects = reader.pages[0]["/Resources"].get("/XObject") or {}
    kinds = [xobjects[k].get_object().get("/Subtype") for k in xobjects]
    assert "/Image" in kinds
    assert "vanished picture" in _pdf_text(str(out))

    # Rendered: the image area (top-left of the content box, 400x200 px = 300x150 pt)
    # is red.
    img = _render_pdf_page(str(out), 0, dpi=72)
    m_pt = 20 * MM
    reds = 0
    for y in range(round(m_pt) + 20, round(m_pt) + 130, 10):
        for x in range(round(m_pt) + 20, round(m_pt) + 280, 10):
            c = QColor(img.pixel(x, y))
            if c.red() > 200 and c.green() < 60 and c.blue() < 60:
                reds += 1
    assert reds > 20


def test_export_replaces_existing_file(tmp_path):
    out = tmp_path / "x.pdf"
    out.write_bytes(b"old")
    export_markdown_pdf("# new", MarkdownSettings(), str(out))
    assert out.read_bytes().startswith(b"%PDF")
