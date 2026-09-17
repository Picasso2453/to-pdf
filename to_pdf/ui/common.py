"""Pieces shared by the tools: base page class, buttons, saved-PDF dialog."""

from __future__ import annotations

import os
import subprocess
from typing import Callable

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QToolButton, QWidget

from ..resources import asset_path
from . import icons

APP_NAME = "To PDF"


def button(text: str, tip: str = "", object_name: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if object_name:
        b.setObjectName(object_name)
    return b


def tool_button(action: QAction, style: Qt.ToolButtonStyle | None = None) -> QToolButton:
    b = QToolButton()
    b.setDefaultAction(action)
    b.setObjectName("icon")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if style is None:
        style = (Qt.ToolButtonStyle.ToolButtonTextOnly if action.icon().isNull()
                 else Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setToolButtonStyle(style)
    return b


def tool_pixmap(kind: str, size: int) -> QPixmap:
    """The tool's tile artwork ("images" or "markdown"), crisp on HiDPI."""
    pm = QPixmap(asset_path(f"tool-{kind}.png"))
    if pm.isNull():
        return pm
    from PySide6.QtWidgets import QApplication
    ratio = QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0
    scaled = pm.scaled(int(size * ratio), int(size * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


def paint_page_shadow(painter: QPainter, rect: QRectF, px: float) -> None:
    """Soft layered shadow under a page; px = one screen pixel in scene units."""
    painter.setPen(Qt.PenStyle.NoPen)
    for spread, alpha in ((6, 10), (3, 16), (1, 26)):
        painter.setBrush(QColor(20, 28, 40, alpha))
        s = spread * px
        painter.drawRoundedRect(rect.adjusted(-s, -s + 2 * px, s, s + 2 * px), s, s)


def show_saved_dialog(parent: QWidget, message: str, path: str, new_label: str,
                      on_new: Callable[[], None]) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(APP_NAME)
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(message)
    open_btn = box.addButton("Open PDF", QMessageBox.ButtonRole.AcceptRole)
    folder_btn = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
    new_btn = box.addButton(new_label, QMessageBox.ButtonRole.ActionRole)
    new_btn.setToolTip("Start the next one (Ctrl+N)")
    box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(open_btn)
    box.exec()
    clicked = box.clickedButton()
    if clicked is open_btn:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    elif clicked is folder_btn:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif clicked is new_btn:
        on_new()


class ToolPage(QWidget):
    """One tool in the main window's stack.

    Shortcuts are scoped to the page (WidgetWithChildrenShortcut), so two tools
    can both use Ctrl+S without clashing."""

    homeRequested = Signal()
    titleChanged = Signal(str)
    statusMessage = Signal(str, int)

    kind = ""        # "images" | "markdown"
    tool_title = ""  # shown in the top bar
    tips = ""        # permanent hint in the status bar

    def __init__(self, parent=None):
        super().__init__(parent)
        self.a_home = self.make_action("Home", self.homeRequested.emit, "Alt+Home",
                                       "Back to the start screen")
        self.a_home.setIcon(icons.home())

    def make_action(self, text: str, slot, shortcut=None, tip: str | None = None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        if shortcut:
            seqs = shortcut if isinstance(shortcut, list) else [shortcut]
            a.setShortcuts([QKeySequence(s) for s in seqs])
        hint = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText) if shortcut else ""
        a.setToolTip(f"{tip or text}" + (f"  ({hint})" if hint else ""))
        self.addAction(a)
        return a

    def title_block(self) -> list[QWidget]:
        """Home button, tool icon and tool name for the left of the top bar."""
        home = tool_button(self.a_home)
        art = QLabel()
        art.setPixmap(tool_pixmap(self.kind, 26))
        name = QLabel(self.tool_title, objectName="brand")
        return [home, art, name]

    def notify(self, message: str, ms: int = 4000) -> None:
        self.statusMessage.emit(message, ms)

    # hooks for the main window
    def save_state(self) -> None:
        pass

    def open_paths(self, paths: list[str]) -> None:
        pass

    def focus_default(self) -> None:
        self.setFocus()
