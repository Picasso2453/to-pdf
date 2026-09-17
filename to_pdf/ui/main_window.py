"""The window: start screen plus one page per tool, in a stack."""

from __future__ import annotations

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QLabel, QMainWindow, QStackedWidget

from .common import APP_NAME, ToolPage
from .launcher import Launcher, classify


class MainWindow(QMainWindow):
    def __init__(self, initial_paths: list[str] | None = None, start: str | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 860)
        self.settings_store = QSettings()

        self.stack = QStackedWidget()
        self.launcher = Launcher()
        self.launcher.chosen.connect(self.open_tool)
        self.stack.addWidget(self.launcher)
        self.setCentralWidget(self.stack)
        self.tools: dict[str, ToolPage] = {}

        self.status = self.statusBar()
        self.status.setSizeGripEnabled(False)
        self.tips = QLabel("", objectName="hint")
        self.status.addPermanentWidget(self.tips)

        geo = self.settings_store.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.show_launcher()

        kind = start or (classify(initial_paths) if initial_paths else None)
        if kind:
            QTimer.singleShot(0, lambda: self.open_tool(kind, initial_paths or []))

    def tool(self, kind: str) -> ToolPage:
        """The tool page, created on first use."""
        if kind not in self.tools:
            if kind == "images":
                from .image_tool import ImageTool
                page = ImageTool()
            elif kind == "markdown":
                from .markdown_tool import MarkdownTool
                page = MarkdownTool()
            else:
                raise ValueError(kind)
            page.homeRequested.connect(self.show_launcher)
            page.titleChanged.connect(self._on_tool_title)
            page.statusMessage.connect(self.status.showMessage)
            self.stack.addWidget(page)
            self.tools[kind] = page
        return self.tools[kind]

    def open_tool(self, kind: str, paths: list[str] | None = None) -> ToolPage:
        page = self.tool(kind)
        self.stack.setCurrentWidget(page)
        self.tips.setText(page.tips)
        self.setWindowTitle(getattr(page, "window_title", APP_NAME))
        page.focus_default()
        if paths:
            page.open_paths(paths)
        self.settings_store.setValue("window/last_tool", kind)
        return page

    def show_launcher(self) -> None:
        self.stack.setCurrentWidget(self.launcher)
        self.tips.setText("")
        self.setWindowTitle(APP_NAME)
        self.launcher.setFocus()

    def current_tool(self) -> ToolPage | None:
        w = self.stack.currentWidget()
        return w if isinstance(w, ToolPage) else None

    def _on_tool_title(self, title: str) -> None:
        if self.sender() is self.stack.currentWidget():
            self.setWindowTitle(title)

    def closeEvent(self, ev) -> None:
        for page in self.tools.values():
            if hasattr(page, "confirm_close") and not page.confirm_close():
                ev.ignore()
                return
        self.settings_store.setValue("window/geometry", self.saveGeometry())
        for page in self.tools.values():
            page.save_state()
        super().closeEvent(ev)
