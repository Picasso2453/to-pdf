"""Markdown → PDF: editor on the left, live paginated PDF preview on the right."""

from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QRegularExpression, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QSyntaxHighlighter,
                           QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QPlainTextEdit, QSplitter, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from ..imaging import expand_paths, is_supported
from ..markdown_render import MarkdownSettings, build_document, export_markdown_pdf
from ..model import PAGE_SIZES
from . import icons, theme
from .common import APP_NAME, ToolPage, button, show_saved_dialog, tool_button, tool_pixmap
from .launcher import is_markdown
from .markdown_preview import MarkdownPreview

RENDER_DELAY_MS = 250
MAX_DRAFT_CHARS = 2_000_000

SAMPLE = """# Project update

A short report written in **Markdown**, turned into a PDF as you type.
Edit anything on the left and watch the pages on the right.

## Highlights

- Paste text from anywhere: GitHub, ChatGPT, Obsidian, Notion exports
- *Emphasis*, **bold**, ~~strikethrough~~ and `inline code`
- Nested lists
  - two spaces or four
  - both work

## Checklist

- [x] Draft the summary
- [x] Add the numbers
- [ ] Send it out

## Numbers

| Quarter | Revenue | Growth |
|:--------|--------:|:------:|
| Q1      | € 12,400 | —     |
| Q2      | € 15,900 | +28%  |
| Q3      | € 19,250 | +21%  |

> Good documents are short documents.
> Say what matters, then stop.

## Code

```python
def to_pdf(markdown: str) -> bytes:
    return render(markdown)
```

---

Drop an image into the editor to insert it, then press **Ctrl+S** to export.
"""


def _mono_font(point_size: float = 10.5) -> QFont:
    families = set(QFontDatabase.families())
    for name in ("Cascadia Mono", "Cascadia Code", "Consolas", "JetBrains Mono", "Courier New"):
        if name in families:
            font = QFont(name)
            break
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSizeF(point_size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


# --------------------------------------------------------------------------
# syntax highlighting


def _fmt(color: str | QColor | None = None, bold: bool = False, italic: bool = False,
         background: str | None = None, strike: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    if color is not None:
        f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.DemiBold)
    if italic:
        f.setFontItalic(True)
    if background:
        f.setBackground(QColor(background))
    if strike:
        f.setFontStrikeOut(True)
    return f


class MarkdownHighlighter(QSyntaxHighlighter):
    IN_FENCE = 1

    def __init__(self, document):
        super().__init__(document)
        accent = theme.ACCENT_DARK
        muted = QColor("#8a929c")
        self.f_heading = _fmt(accent, bold=True)
        self.f_heading_mark = _fmt("#9fbfb6", bold=True)
        self.f_fence = _fmt(muted, background="#f3f5f7")
        self.f_code_block = _fmt("#3b4252", background="#f3f5f7")
        self.f_inline_code = _fmt("#b3401a", background="#f3f5f7")
        self.f_quote = _fmt("#6b7380", italic=True)
        self.f_marker = _fmt(accent, bold=True)
        self.f_task_done = _fmt(accent, bold=True)
        self.f_bold = _fmt(bold=True)
        self.f_italic = _fmt(italic=True)
        self.f_strike = _fmt("#8a929c", strike=True)
        self.f_link_text = _fmt("#0e7c66")
        self.f_link_url = _fmt(muted)
        self.f_muted = _fmt(muted)
        self.f_hr = _fmt("#b9c0c9", bold=True)

        R = QRegularExpression
        self.re_fence = R(r"^\s{0,3}(```|~~~)")
        self.re_heading = R(r"^\s{0,3}(#{1,6})(\s+.*)?$")
        self.re_quote = R(r"^\s{0,3}>.*$")
        self.re_hr = R(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
        self.re_list = R(r"^\s*([-*+]|\d{1,9}[.)])\s+(\[[ xX]\]\s)?")
        self.inline = [
            (R(r"`[^`\n]+`"), self.f_inline_code),
            (R(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1"), self.f_bold),
            (R(r"(?<![*\w])\*(?=[^\s*])([^*\n]+?)(?<=\S)\*(?!\*)"), self.f_italic),
            (R(r"(?<![_\w])_(?=[^\s_])([^_\n]+?)(?<=\S)_(?![_\w])"), self.f_italic),
            (R(r"~~(?=\S)(.+?)(?<=\S)~~"), self.f_strike),
        ]
        self.re_link = R(r"(!?\[)([^\]\n]*)(\]\()([^)\n]*)(\))")
        self.re_autolink = R(r"<https?://[^>\s]+>|https?://[^\s)>]+")
        self.re_pipe = R(r"\|")

    def highlightBlock(self, text: str) -> None:
        # fenced code blocks span lines: track them with the block state
        in_fence = self.previousBlockState() == self.IN_FENCE
        if self.re_fence.match(text).hasMatch():
            self.setFormat(0, len(text), self.f_fence)
            self.setCurrentBlockState(0 if in_fence else self.IN_FENCE)
            return
        if in_fence:
            self.setFormat(0, len(text), self.f_code_block)
            self.setCurrentBlockState(self.IN_FENCE)
            return
        self.setCurrentBlockState(0)

        m = self.re_heading.match(text)
        if m.hasMatch():
            self.setFormat(0, len(text), self.f_heading)
            self.setFormat(m.capturedStart(1), m.capturedLength(1), self.f_heading_mark)
            return
        if self.re_hr.match(text).hasMatch():
            self.setFormat(0, len(text), self.f_hr)
            return
        if self.re_quote.match(text).hasMatch():
            self.setFormat(0, len(text), self.f_quote)

        m = self.re_list.match(text)
        if m.hasMatch():
            self.setFormat(m.capturedStart(1), m.capturedLength(1), self.f_marker)
            if m.capturedLength(2):
                done = "x" in m.captured(2).lower()
                self.setFormat(m.capturedStart(2), m.capturedLength(2),
                               self.f_task_done if done else self.f_muted)

        if "|" in text:
            it = self.re_pipe.globalMatch(text)
            while it.hasNext():
                pm = it.next()
                self.setFormat(pm.capturedStart(), 1, self.f_muted)

        for rx, f in self.inline:
            it = rx.globalMatch(text)
            while it.hasNext():
                im = it.next()
                self._merge(im.capturedStart(), im.capturedLength(), f)

        it = self.re_link.globalMatch(text)
        while it.hasNext():
            lm = it.next()
            self._merge(lm.capturedStart(1), lm.capturedLength(1), self.f_muted)
            self._merge(lm.capturedStart(2), lm.capturedLength(2), self.f_link_text)
            self._merge(lm.capturedStart(3), lm.capturedLength(3), self.f_muted)
            self._merge(lm.capturedStart(4), lm.capturedLength(4), self.f_link_url)
            self._merge(lm.capturedStart(5), lm.capturedLength(5), self.f_muted)
        it = self.re_autolink.globalMatch(text)
        while it.hasNext():
            am = it.next()
            self._merge(am.capturedStart(), am.capturedLength(), self.f_link_text)

    def _merge(self, start: int, length: int, f: QTextCharFormat) -> None:
        """Apply a format on top of whatever is already there, character by character."""
        for i in range(start, start + length):
            current = self.format(i)
            current.merge(f)
            self.setFormat(i, 1, current)


# --------------------------------------------------------------------------
# editor


class EmptyState(QFrame):
    pasteRequested = Signal()
    openRequested = Signal()
    exampleRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("emptyState")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(6)
        art = QLabel()
        art.setPixmap(tool_pixmap("markdown", 64))
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(art)
        lay.addSpacing(6)
        title = QLabel("Paste or type Markdown", objectName="panelTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        hint = QLabel("The PDF preview updates as you type.", objectName="hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)
        lay.addSpacing(12)
        paste = button("  Paste from clipboard", "Paste Markdown text  (Ctrl+V)", "primary")
        paste.setIcon(icons.paste(QColor("#ffffff")))
        paste.clicked.connect(self.pasteRequested.emit)
        lay.addWidget(paste)
        row = QHBoxLayout()
        row.setSpacing(6)
        open_btn = button("Open .md file…", "Open a Markdown file  (Ctrl+O)")
        open_btn.clicked.connect(self.openRequested.emit)
        example = button("Try an example", "Load a sample document")
        example.clicked.connect(self.exampleRequested.emit)
        row.addWidget(open_btn)
        row.addWidget(example)
        lay.addLayout(row)


class MarkdownEditor(QPlainTextEdit):
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mdEditor")
        self.setFont(_mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setCursorWidth(2)
        self.document().setDocumentMargin(18)
        self.highlighter = MarkdownHighlighter(self.document())
        self.empty_state = EmptyState(self.viewport())
        self.textChanged.connect(self._update_empty_state)
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        empty = self.document().isEmpty()
        self.empty_state.setVisible(empty)
        if empty:
            self._place_empty_state()

    def _place_empty_state(self) -> None:
        es = self.empty_state
        es.adjustSize()
        size = es.sizeHint().expandedTo(QSize(300, 0))
        vr = self.viewport().rect()
        es.setGeometry((vr.width() - size.width()) // 2, max(20, (vr.height() - size.height()) // 2 - 20),
                       size.width(), size.height())

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self.empty_state.isVisible():
            self._place_empty_state()

    def keyPressEvent(self, ev) -> None:
        # Tab indents with spaces (Markdown nesting is space-based)
        if ev.key() == Qt.Key.Key_Tab and not ev.modifiers():
            self.insertPlainText("    ")
            return
        # Enter continues lists: "- item" -> "- ", "1. item" -> "2. ", "- [ ] x" -> "- [ ] "
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ev.modifiers():
            line = self.textCursor().block().text()
            m = re.match(r"^(\s*)([-*+]|(\d{1,9})([.)]))(\s+)(\[[ xX]\]\s+)?(.*)$", line)
            if m and self.textCursor().atBlockEnd():
                indent, marker, num, delim, gap, task, rest = m.groups()
                if not rest.strip():
                    # empty item: end the list instead of continuing it
                    cur = self.textCursor()
                    cur.select(QTextCursor.SelectionType.LineUnderCursor)
                    cur.insertText("")
                    return
                nxt = f"{int(num) + 1}{delim}" if num else marker
                self.insertPlainText("\n" + indent + nxt + gap + ("[ ] " if task else ""))
                return
        super().keyPressEvent(ev)

    def insertFromMimeData(self, source) -> None:
        # Files pasted/dropped from Explorer arrive as URLs: handle, don't paste paths.
        if source.hasUrls() and any(u.isLocalFile() for u in source.urls()):
            self.filesDropped.emit([u.toLocalFile() for u in source.urls() if u.isLocalFile()])
            return
        if source.hasText():
            self.insertPlainText(source.text())
            return
        super().insertFromMimeData(source)

    def canInsertFromMimeData(self, source) -> bool:
        return source.hasUrls() or source.hasText()


# --------------------------------------------------------------------------
# the tool


class MarkdownTool(ToolPage):
    kind = "markdown"
    tool_title = "Markdown → PDF"
    tips = "Ctrl+S exports · Ctrl+scroll zooms the preview · drop an image into the editor to insert it"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.settings_store = QSettings()
        self.window_title = f"{APP_NAME} — Markdown"
        self.path: str | None = None
        self.dirty = False
        self._loading = False
        self._sync_scroll = True

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(RENDER_DELAY_MS)
        self._render_timer.timeout.connect(self.render_now)

        self._make_actions()
        self._build_ui()
        self._restore_state()
        self.render_now()
        self._update_actions()

    # ------------------------------------------------------------------ UI

    def _make_actions(self) -> None:
        act = self.make_action
        self.a_new = act("New", self.new_document, "Ctrl+N", "Start a new document (keeps page settings)")
        self.a_open = act("Open…", self.open_dialog, "Ctrl+O", "Open a Markdown file")
        self.a_save = act("Save .md", self.save_markdown, "Ctrl+Shift+S", "Save the Markdown text")
        self.a_undo = act("Undo", lambda: self.editor.undo(), "Ctrl+Z")
        self.a_redo = act("Redo", lambda: self.editor.redo(), ["Ctrl+Y", "Ctrl+Shift+Z"])
        self.a_paste = act("Paste", self.paste, tip="Paste from the clipboard")
        self.a_zoom_in = act("Zoom in", lambda: self.preview.zoom_by(1.25), ["Ctrl+=", "Ctrl++"])
        self.a_zoom_out = act("Zoom out", lambda: self.preview.zoom_by(0.8), "Ctrl+-")
        self.a_fit_page = act("Whole page", lambda: self.preview.fit_page(), "Ctrl+0", "Zoom to the whole page")
        self.a_fit_width = act("Page width", lambda: self.preview.fit_width(), tip="Zoom to the page width")
        self.a_export = act("Export PDF…", self.export, "Ctrl+S")
        for a, icon in ((self.a_new, icons.new_document()), (self.a_open, icons.folder_open()),
                        (self.a_save, icons.save()), (self.a_undo, icons.undo()),
                        (self.a_redo, icons.undo(redo=True)), (self.a_paste, icons.paste()),
                        (self.a_zoom_in, icons.plus()), (self.a_zoom_out, icons.minus()),
                        (self.a_fit_page, icons.fit_page()), (self.a_fit_width, icons.fit_width())):
            a.setIcon(icon)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QWidget(objectName="topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(10, 8, 14, 8)
        tl.setSpacing(8)
        for w in self.title_block():
            tl.addWidget(w)
        tl.addSpacing(8)
        beside = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        for a in (self.a_new, self.a_open, self.a_save):
            tl.addWidget(tool_button(a, beside))
        tl.addSpacing(4)
        tl.addWidget(tool_button(self.a_undo, beside))
        tl.addWidget(tool_button(self.a_redo, beside))
        tl.addStretch(1)
        self.export_btn = button("Export PDF…", "Save the document as a PDF  (Ctrl+S)", "primary")
        self.export_btn.clicked.connect(self.export)
        tl.addWidget(self.export_btn)
        outer.addWidget(top)

        # editor side
        left = QWidget(objectName="sidebar")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        head = QWidget(objectName="viewbar")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 6, 10, 6)
        hl.setSpacing(8)
        hl.addWidget(QLabel("Markdown", objectName="panelTitle"))
        self.file_label = QLabel("", objectName="hint")
        hl.addWidget(self.file_label, 1)
        hl.addWidget(tool_button(self.a_paste, beside))
        ll.addWidget(head)
        self.editor = MarkdownEditor()
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.filesDropped.connect(self.open_paths)
        self.editor.undoAvailable.connect(self.a_undo.setEnabled)
        self.editor.redoAvailable.connect(self.a_redo.setEnabled)
        self.editor.verticalScrollBar().valueChanged.connect(self._on_editor_scroll)
        self.editor.empty_state.pasteRequested.connect(self.paste)
        self.editor.empty_state.openRequested.connect(self.open_dialog)
        self.editor.empty_state.exampleRequested.connect(self.load_example)
        ll.addWidget(self.editor, 1)

        # preview side
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        bar = QWidget(objectName="viewbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(6)

        # Page setup lives in a popup so the preview bar stays narrow.
        self.size_combo = QComboBox()
        self.size_combo.addItems(list(PAGE_SIZES))
        self.size_combo.setToolTip("Paper size")
        self.orient_combo = QComboBox()
        self.orient_combo.addItem("Portrait", "portrait")
        self.orient_combo.addItem("Landscape", "landscape")
        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(5, 50)
        self.margin_spin.setDecimals(0)
        self.margin_spin.setSuffix(" mm")
        self.margin_spin.setToolTip("Page margin")
        self.font_spin = QDoubleSpinBox()
        self.font_spin.setRange(7, 20)
        self.font_spin.setDecimals(1)
        self.font_spin.setSingleStep(0.5)
        self.font_spin.setSuffix(" pt")
        self.font_spin.setToolTip("Body text size")
        self.numbers_check = QCheckBox("Page numbers")
        self.numbers_check.setCursor(Qt.CursorShape.PointingHandCursor)
        for w in (self.size_combo, self.orient_combo):
            w.currentIndexChanged.connect(self._on_settings_edited)
        for w in (self.margin_spin, self.font_spin):
            w.setKeyboardTracking(False)
            w.valueChanged.connect(self._on_settings_edited)
        self.numbers_check.toggled.connect(self._on_settings_edited)

        form = QWidget(objectName="setupPanel")
        grid = QGridLayout(form)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for row, (label, widget) in enumerate((("Paper", self.size_combo), ("Orientation", self.orient_combo),
                                               ("Margin", self.margin_spin), ("Text size", self.font_spin))):
            grid.addWidget(QLabel(label, objectName="hint"), row, 0)
            widget.setMinimumWidth(140)
            grid.addWidget(widget, row, 1)
        grid.addWidget(self.numbers_check, 4, 1)
        self.setup_menu = QMenu(self)
        panel_action = QWidgetAction(self.setup_menu)
        panel_action.setDefaultWidget(form)
        self.setup_menu.addAction(panel_action)
        self.setup_btn = QToolButton()
        self.setup_btn.setObjectName("icon")
        self.setup_btn.setIcon(icons.page_setup())
        self.setup_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setup_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setup_btn.setMenu(self.setup_menu)
        self.setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_btn.setToolTip("Page setup: paper, orientation, margin, text size, page numbers")
        bl.addWidget(self.setup_btn)
        self.pages_label = QLabel("", objectName="hint")
        bl.addSpacing(4)
        bl.addWidget(self.pages_label)
        bl.addStretch(1)
        bl.addWidget(tool_button(self.a_zoom_out))
        self.zoom_label = QLabel("100%", objectName="zoomLabel")
        self.zoom_label.setMinimumWidth(40)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.zoom_label)
        bl.addWidget(tool_button(self.a_zoom_in))
        bl.addWidget(tool_button(self.a_fit_page))
        bl.addWidget(tool_button(self.a_fit_width))
        rl.addWidget(bar)
        self.preview = MarkdownPreview()
        self.preview.zoomChanged.connect(lambda s: self.zoom_label.setText(f"{s * 100:.0f}%"))
        self.preview.filesDropped.connect(self.open_paths)
        rl.addWidget(self.preview, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 5)
        self.splitter.setStretchFactor(1, 6)
        self.splitter.setSizes([580, 740])
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)

    # ------------------------------------------------------------ state

    def settings(self) -> MarkdownSettings:
        return MarkdownSettings(size=self.size_combo.currentText(),
                                orientation=self.orient_combo.currentData(),
                                margin_mm=float(self.margin_spin.value()),
                                font_pt=float(self.font_spin.value()),
                                page_numbers=self.numbers_check.isChecked())

    def _restore_state(self) -> None:
        s = self.settings_store
        d = MarkdownSettings()

        def num(key: str, default: float) -> float:
            try:
                return float(s.value(key, default))
            except (TypeError, ValueError):
                return default

        self._loading = True
        size = s.value("markdown/size", d.size)
        self.size_combo.setCurrentText(size if size in PAGE_SIZES else d.size)
        self.orient_combo.setCurrentIndex(max(0, self.orient_combo.findData(s.value("markdown/orientation", d.orientation))))
        self.margin_spin.setValue(num("markdown/margin_mm", d.margin_mm))
        self.font_spin.setValue(num("markdown/font_pt", d.font_pt))
        self.numbers_check.setChecked(str(s.value("markdown/page_numbers", d.page_numbers)).lower() in ("true", "1"))
        split = s.value("markdown/splitter")
        if split is not None:
            self.splitter.restoreState(split)
        # The draft survives closing the app.
        draft = s.value("markdown/draft", "")
        path = s.value("markdown/path", "") or None
        if draft:
            self.editor.setPlainText(str(draft))
            self.path = path if path and os.path.isfile(path) else None
            self.dirty = str(s.value("markdown/dirty", "false")).lower() == "true"
        self._loading = False
        self._update_file_label()
        self._update_setup_label()

    def save_state(self) -> None:
        s = self.settings_store
        st = self.settings()
        s.setValue("markdown/size", st.size)
        s.setValue("markdown/orientation", st.orientation)
        s.setValue("markdown/margin_mm", st.margin_mm)
        s.setValue("markdown/font_pt", st.font_pt)
        s.setValue("markdown/page_numbers", st.page_numbers)
        s.setValue("markdown/splitter", self.splitter.saveState())
        text = self.editor.toPlainText()
        s.setValue("markdown/draft", text if len(text) <= MAX_DRAFT_CHARS else "")
        s.setValue("markdown/path", self.path or "")
        s.setValue("markdown/dirty", self.dirty)

    def closeEvent(self, ev) -> None:  # standalone use (tests)
        self.save_state()
        super().closeEvent(ev)

    def focus_default(self) -> None:
        self.editor.setFocus()

    @property
    def base_dir(self) -> str | None:
        return os.path.dirname(self.path) if self.path else None

    # ---------------------------------------------------------- render

    def _on_text_changed(self) -> None:
        if not self._loading:
            if not self.dirty:
                self.dirty = True
                self._update_file_label()
        self._render_timer.start()
        self._update_actions()

    def _on_settings_edited(self, *_args) -> None:
        self._update_setup_label()
        if not self._loading:
            self._render_timer.start()

    def _update_setup_label(self) -> None:
        s = self.settings()
        self.setup_btn.setText(f"{s.size} · {s.orientation.capitalize()} · {s.font_pt:g} pt")

    def render_now(self) -> None:
        self._render_timer.stop()
        settings = self.settings()
        try:
            doc = build_document(self.editor.toPlainText(), settings, self.base_dir)
        except Exception as ex:  # noqa: BLE001 - never let a bad document kill the editor
            self.notify(f"Could not render: {ex}", 6000)
            return
        self._doc = doc  # keep alive while the preview paints it
        self.preview.set_document(doc, settings)
        n = self.preview.page_count
        self.pages_label.setText(f"{n} page{'s' * (n != 1)}")

    def _on_editor_scroll(self, value: int) -> None:
        bar = self.editor.verticalScrollBar()
        if self._sync_scroll and bar.maximum() > 0:
            self.preview.set_scroll_fraction(value / bar.maximum())

    # ------------------------------------------------------------ files

    def _set_text(self, text: str) -> None:
        """Replace everything as one undoable step."""
        cur = QTextCursor(self.editor.document())
        cur.beginEditBlock()
        cur.select(QTextCursor.SelectionType.Document)
        cur.insertText(text)
        cur.endEditBlock()
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)

    def new_document(self) -> None:
        if not self.editor.toPlainText():
            return
        self._loading = True
        self._set_text("")
        self._loading = False
        self.path = None
        self.dirty = False
        self._update_file_label()
        self.render_now()
        self.editor.setFocus()
        self.notify("New document started — Ctrl+Z brings the previous text back", 6000)

    def load_example(self) -> None:
        self._set_text(SAMPLE)
        self.render_now()
        self.editor.setFocus()

    def paste(self) -> None:
        self.editor.setFocus()
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None or not (mime.hasText() or mime.hasUrls()):
            self.notify("The clipboard has no text to paste", 4000)
            return
        self.editor.paste()

    def open_dialog(self) -> None:
        start = self.settings_store.value("paths/last_markdown", str(Path.home() / "Documents"))
        path, _ = QFileDialog.getOpenFileName(self, "Open Markdown", start,
                                              "Markdown (*.md *.markdown *.mdown *.mkd *.txt);;All files (*)")
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
        except OSError as ex:
            QMessageBox.critical(self, APP_NAME, f"Could not open\n{path}\n\n{ex}")
            return
        self.settings_store.setValue("paths/last_markdown", os.path.dirname(path))
        self._loading = True
        self.path = os.path.abspath(path)
        self._set_text(text)
        self._loading = False
        self.dirty = False
        self._update_file_label()
        self.render_now()
        self.notify(f"Opened {os.path.basename(path)}")

    def open_paths(self, paths: list[str]) -> None:
        files = expand_paths(paths)
        md = [p for p in files if is_markdown(p)]
        images = [p for p in files if is_supported(p)]
        if md:
            self.open_file(md[0])
        if images:
            self.insert_images(images)
        if not md and not images:
            self.notify("Drop a Markdown file or images", 4000)

    def insert_images(self, paths: list[str]) -> None:
        lines = []
        for p in paths:
            alt = Path(p).stem.replace("[", "(").replace("]", ")")
            lines.append(f"![{alt}](<{Path(p).resolve().as_posix()}>)")
        cur = self.editor.textCursor()
        prefix = "" if cur.atBlockStart() else "\n\n"
        cur.insertText(prefix + "\n\n".join(lines) + "\n")
        self.editor.setTextCursor(cur)
        self.editor.setFocus()
        self.render_now()
        self.notify(f"Inserted {len(paths)} image{'s' * (len(paths) != 1)}")

    def save_markdown(self) -> bool:
        path = self.path
        if not path:
            start = self.settings_store.value("paths/last_markdown", str(Path.home() / "Documents"))
            path, _ = QFileDialog.getSaveFileName(self, "Save Markdown",
                                                  os.path.join(start, f"{self._suggested_name()}.md"),
                                                  "Markdown (*.md)")
            if not path:
                return False
            if not os.path.splitext(path)[1]:
                path += ".md"
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self.editor.toPlainText())
        except OSError as ex:
            QMessageBox.critical(self, APP_NAME, f"Could not save\n{path}\n\n{ex}")
            return False
        self.path = os.path.abspath(path)
        self.dirty = False
        self.settings_store.setValue("paths/last_markdown", os.path.dirname(path))
        self._update_file_label()
        self.render_now()  # relative images now resolve against the file's folder
        self.notify(f"Saved {path}")
        return True

    def _suggested_name(self) -> str:
        if self.path:
            return Path(self.path).stem
        for line in self.editor.toPlainText().splitlines():
            m = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
            if m:
                name = re.sub(r'[\\/:*?"<>|]+', " ", m.group(1)).strip()
                name = re.sub(r"[*_`~\[\]]", "", name)[:80].strip()
                if name:
                    return name
        return "document"

    def _update_file_label(self) -> None:
        name = os.path.basename(self.path) if self.path else "Unsaved"
        self.file_label.setText(f"{name}{'  •' if self.dirty and self.editor.toPlainText() else ''}")
        self.file_label.setToolTip(self.path or "Not saved as a .md file yet")
        title = f"{APP_NAME} — Markdown · {name}"
        if title != self.window_title:
            self.window_title = title
            self.titleChanged.emit(title)

    def _update_actions(self) -> None:
        has_text = bool(self.editor.toPlainText().strip())
        for a in (self.a_new, self.a_save):
            a.setEnabled(bool(self.editor.toPlainText()))
        self.a_export.setEnabled(has_text)
        self.export_btn.setEnabled(has_text)

    # ----------------------------------------------------------- export

    def export(self) -> None:
        text = self.editor.toPlainText()
        if not text.strip():
            return
        start = self.settings_store.value("paths/last_export",
                                          self.base_dir or str(Path.home() / "Documents"))
        default = os.path.join(start, f"{self._suggested_name()}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", default, "PDF document (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.settings_store.setValue("paths/last_export", os.path.dirname(path))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pages = export_markdown_pdf(text, self.settings(), path, self.base_dir,
                                        title=self._suggested_name())
        except PermissionError:
            QMessageBox.critical(self, APP_NAME, f"Could not write\n{path}\n\n"
                                 "Is the file open in another program?")
            return
        except Exception as ex:  # noqa: BLE001
            QMessageBox.critical(self, APP_NAME, f"Export failed:\n\n{ex}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        size_kb = os.path.getsize(path) / 1024
        self.notify(f"Saved {path}", 8000)
        show_saved_dialog(self, f"Saved {pages} page{'s' * (pages != 1)} to\n{path}\n({size_kb:,.0f} KB)",
                          path, "New document", self.new_document)

    # ------------------------------------------------------------- drop

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            ev.acceptProposedAction()
            self.open_paths(paths)
