"""Start screen, tool routing and the Markdown tool's UI behaviour."""

import pytest
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from to_pdf.ui.image_tool import ImageTool
from to_pdf.ui.launcher import classify
from to_pdf.ui.main_window import MainWindow


@pytest.fixture
def window():
    QSettings().clear()
    w = MainWindow()
    w.resize(1280, 800)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


@pytest.fixture
def files(tmp_path):
    img = tmp_path / "photo.png"
    Image.new("RGB", (64, 48), (200, 80, 40)).save(img)
    md = tmp_path / "notes.md"
    md.write_text("# Notes\n\nSome **text**.\n\n![photo](photo.png)\n", encoding="utf-8")
    return {"img": str(img), "md": str(md), "dir": str(tmp_path)}


def test_starts_on_launcher(window):
    assert window.stack.currentWidget() is window.launcher
    assert window.tools == {}


def test_launcher_card_opens_tool_and_home_returns(window):
    window.launcher.images_card.clicked.emit()
    assert isinstance(window.stack.currentWidget(), ImageTool)
    window.current_tool().a_home.trigger()
    assert window.stack.currentWidget() is window.launcher
    window.launcher.markdown_card.clicked.emit()
    assert window.current_tool().kind == "markdown"
    assert set(window.tools) == {"images", "markdown"}


def test_tools_keep_state_between_visits(window, files):
    tool = window.open_tool("images", [files["img"]])
    assert len(tool.doc.entries) == 1
    window.show_launcher()
    assert window.open_tool("images").doc.entries == tool.doc.entries


@pytest.mark.parametrize("names,expected", [
    (["a.png"], "images"),
    (["a.md"], "markdown"),
    (["a.md", "b.jpg"], "images"),
    (["a.pdf"], None),
])
def test_classify(tmp_path, names, expected):
    paths = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"x")
        paths.append(str(p))
    assert classify(paths) == expected


def test_initial_md_path_opens_markdown_tool(files):
    QSettings().clear()
    w = MainWindow([files["md"]])
    w.show()
    for _ in range(5):
        QApplication.processEvents()
    tool = w.current_tool()
    assert tool is not None and tool.kind == "markdown"
    assert tool.path.endswith("notes.md")
    assert "# Notes" in tool.editor.toPlainText()
    assert tool.preview.page_count >= 1
    w.close()


def test_markdown_new_is_undoable_and_keeps_settings(window, files):
    tool = window.open_tool("markdown", [files["md"]])
    tool.font_spin.setValue(13)
    tool.a_new.trigger()
    assert tool.editor.toPlainText() == ""
    assert tool.path is None
    assert tool.settings().font_pt == 13
    assert not tool.a_export.isEnabled()
    tool.editor.undo()
    assert "# Notes" in tool.editor.toPlainText()


def test_markdown_render_updates_page_count(window):
    tool = window.open_tool("markdown")
    tool._set_text("\n\n".join(f"Paragraph {i} " + "words " * 60 for i in range(80)))
    tool.render_now()
    assert tool.preview.page_count > 3
    assert tool.pages_label.text().startswith(str(tool.preview.page_count))


def test_markdown_list_continuation(window):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    tool = window.open_tool("markdown")
    ed = tool.editor
    ed.setFocus()
    ed.insertPlainText("- [ ] first")
    QApplication.sendEvent(ed, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert ed.toPlainText() == "- [ ] first\n- [ ] "
    QApplication.sendEvent(ed, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert ed.toPlainText() == "- [ ] first\n"  # empty item ends the list


def test_insert_images_as_markdown(window, files):
    tool = window.open_tool("markdown")
    tool.open_paths([files["img"]])
    text = tool.editor.toPlainText()
    assert text.startswith("![photo](<") and text.rstrip().endswith("photo.png>)")


def test_draft_survives_restart(files):
    QSettings().clear()
    w = MainWindow()
    tool = w.open_tool("markdown")
    tool._set_text("# Draft\n\nkeep me")
    w.close()
    w2 = MainWindow()
    assert "keep me" in w2.open_tool("markdown").editor.toPlainText()
    w2.close()
