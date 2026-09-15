"""Main window: image list on the left, PDF preview on the right."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QLabel, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QProgressDialog, QPushButton, QSplitter, QToolButton, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..imaging import SUPPORTED_EXTENSIONS, expand_paths, is_supported, load_display_image, probe, to_qimage
from ..model import ORIENTATIONS, PAGE_SIZE_CHOICES, Document, ImageEntry, PageSettings
from ..pdf_export import ExportCancelled, export_pdf
from . import icons
from .image_list import ID_ROLE, PAGE_ROLE, PIXMAP_ROLE, SUBTITLE_ROLE, ImageList
from .page_view import PageView

APP_NAME = "Image to PDF"
PREVIEW_PX = 1800
THUMB_PX = 120


class _LoaderSignals(QObject):
    loaded = Signal(str, QImage)
    failed = Signal(str, str)


class _PreviewTask(QRunnable):
    def __init__(self, path: str, signals: _LoaderSignals):
        super().__init__()
        self.path = path
        self.signals = signals

    def run(self) -> None:
        try:
            self.signals.loaded.emit(self.path, to_qimage(load_display_image(self.path, PREVIEW_PX)))
        except Exception as ex:  # noqa: BLE001 - reported to the user
            self.signals.failed.emit(self.path, str(ex))


def _button(text: str, tip: str = "", object_name: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if object_name:
        b.setObjectName(object_name)
    return b


def _tool(action: QAction, style: Qt.ToolButtonStyle | None = None) -> QToolButton:
    b = QToolButton()
    b.setDefaultAction(action)
    b.setObjectName("icon")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if style is None:
        style = (Qt.ToolButtonStyle.ToolButtonTextOnly if action.icon().isNull()
                 else Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setToolButtonStyle(style)
    return b


class MainWindow(QMainWindow):
    def __init__(self, initial_paths: list[str] | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 860)
        self.setAcceptDrops(True)
        self.settings_store = QSettings()

        self.doc = Document()
        self.doc.subscribe(self._on_doc_changed)
        self._pixmaps: dict[str, QPixmap] = {}   # path -> preview
        self._thumbs: dict[str, QPixmap] = {}    # path -> list thumbnail
        self._pending: set[str] = set()
        self._failed: dict[str, str] = {}
        self._syncing = False

        self._signals = _LoaderSignals()
        self._signals.loaded.connect(self._on_preview_loaded)
        self._signals.failed.connect(self._on_preview_failed)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(6, (os.cpu_count() or 4) - 1)))

        self._make_actions()
        self._build_ui()
        self._restore_state()
        self._on_doc_changed()

        if initial_paths:
            QTimer.singleShot(0, lambda: self.add_paths(initial_paths))

    # ------------------------------------------------------------------ UI

    def _make_actions(self) -> None:
        def act(text, slot, shortcut=None, tip=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcuts([QKeySequence(s) for s in (shortcut if isinstance(shortcut, list) else [shortcut])])
            a.setToolTip(f"{tip or text}" + (f"  ({a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})" if shortcut else ""))
            self.addAction(a)
            return a

        self.a_add = act("Add images…", self.browse, "Ctrl+O")
        self.a_remove = act("Remove", self.remove_selected, tip="Remove selected images")
        self.a_clear = act("Clear all", self.clear_all)
        self.a_sort = act("Sort by file name", lambda: self.doc.sort_by_name())
        self.a_up = act("▲", lambda: self._move(-1), "Ctrl+Up", "Move up")
        self.a_down = act("▼", lambda: self._move(1), "Ctrl+Down", "Move down")
        self.a_undo = act("Undo", self.doc.undo, "Ctrl+Z")
        self.a_redo = act("Redo", self.doc.redo, ["Ctrl+Y", "Ctrl+Shift+Z"])
        self.a_rot_l = act("⟲", lambda: self.doc.rotate(self._targets(), -90), "Ctrl+Shift+R", "Rotate left 90°")
        self.a_rot_r = act("⟳", lambda: self.doc.rotate(self._targets(), 90), "Ctrl+R", "Rotate right 90°")
        self.a_fit = act("Fit", lambda: self.doc.fit(self._targets()), "Ctrl+F", "Fit image inside the margins")
        self.a_fill = act("Fill", lambda: self.doc.fill(self._targets()), "Ctrl+Shift+F", "Fill the page (may crop)")
        self.a_center = act("Center", lambda: self.doc.center(self._targets()), "Ctrl+E", "Center on the page")
        self.a_reset = act("Reset", lambda: self.doc.reset(self._targets()), tip="Undo rotation and sizing")
        self.a_zoom_in = act("+", lambda: self.view.zoom_by(1.25), ["Ctrl+=", "Ctrl++"], "Zoom in")
        self.a_zoom_out = act("−", lambda: self.view.zoom_by(0.8), "Ctrl+-", "Zoom out")
        self.a_fit_page = act("Whole page", lambda: self.view.fit_page(), "Ctrl+0", "Zoom to the whole page")
        self.a_fit_width = act("Page width", lambda: self.view.fit_width(), tip="Zoom to the page width")
        self.a_export = act("Export PDF…", self.export, "Ctrl+S")
        self.a_select_all = act("Select all", lambda: self.list.selectAll(), "Ctrl+A")

        for a, icon in ((self.a_rot_l, icons.rotate(False)), (self.a_rot_r, icons.rotate(True)),
                        (self.a_up, icons.chevron(True)), (self.a_down, icons.chevron(False)),
                        (self.a_zoom_in, icons.plus()), (self.a_zoom_out, icons.minus()),
                        (self.a_undo, icons.undo()), (self.a_redo, icons.undo(redo=True)),
                        (self.a_remove, icons.trash())):
            a.setIcon(icon)

    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # top bar: brand, undo/redo, page settings, export
        top = QWidget(objectName="topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(14, 8, 14, 8)
        tl.setSpacing(8)
        tl.addWidget(QLabel(APP_NAME, objectName="brand"))
        tl.addSpacing(8)
        tl.addWidget(_tool(self.a_undo, Qt.ToolButtonStyle.ToolButtonTextBesideIcon))
        tl.addWidget(_tool(self.a_redo, Qt.ToolButtonStyle.ToolButtonTextBesideIcon))
        tl.addStretch(1)

        tl.addWidget(QLabel("Page size", objectName="hint"))
        self.size_combo = QComboBox()
        self.size_combo.addItems(PAGE_SIZE_CHOICES)
        self.size_combo.setToolTip("Paper size. “Fit to image” makes each page exactly as big as its image.")
        self.size_combo.currentTextChanged.connect(self._on_settings_edited)
        tl.addWidget(self.size_combo)
        tl.addSpacing(6)
        tl.addWidget(QLabel("Orientation", objectName="hint"))
        self.orient_combo = QComboBox()
        for o in ORIENTATIONS:
            self.orient_combo.addItem("Auto (per image)" if o == "auto" else o.capitalize(), o)
        self.orient_combo.currentIndexChanged.connect(self._on_settings_edited)
        tl.addWidget(self.orient_combo)
        tl.addSpacing(6)
        tl.addWidget(QLabel("Margin", objectName="hint"))
        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0, 60)
        self.margin_spin.setDecimals(0)
        self.margin_spin.setSuffix(" mm")
        self.margin_spin.setKeyboardTracking(False)
        self.margin_spin.valueChanged.connect(lambda _v: self._on_settings_edited(coalesce="margin"))
        tl.addWidget(self.margin_spin)
        tl.addSpacing(12)
        self.export_btn = _button("Export PDF…", "Save all pages as one PDF  (Ctrl+S)", "primary")
        self.export_btn.clicked.connect(self.export)
        tl.addWidget(self.export_btn)
        outer.addWidget(top)

        # sidebar
        side = QWidget(objectName="sidebar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 12, 10, 10)
        sl.setSpacing(8)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 4, 0)
        head.addWidget(QLabel("Images", objectName="panelTitle"))
        self.count_label = QLabel("", objectName="hint")
        head.addWidget(self.count_label)
        head.addStretch(1)
        head.addWidget(_tool(self.a_up))
        head.addWidget(_tool(self.a_down))
        sl.addLayout(head)

        self.list = ImageList()
        self.list.filesDropped.connect(self.add_paths)
        self.list.orderChanged.connect(self.doc.reorder)
        self.list.deleteRequested.connect(self.remove_selected)
        self.list.addRequested.connect(self.browse)
        self.list.itemSelectionChanged.connect(self._on_list_selection)
        sl.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        add_btn = _button("+  Add images", "Add image files  (Ctrl+O)", "primary")
        add_btn.clicked.connect(self.browse)
        row.addWidget(add_btn, 1)
        row.addWidget(_tool(self.a_remove, Qt.ToolButtonStyle.ToolButtonTextBesideIcon))
        more = QToolButton()
        more.setObjectName("icon")
        more.setIcon(icons.more())
        more.setToolTip("More")
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction(self.a_sort)
        menu.addAction(self.a_select_all)
        menu.addSeparator()
        menu.addAction(self.a_clear)
        more.setMenu(menu)
        more.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        row.addWidget(more)
        sl.addLayout(row)

        # preview side
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        bar = QWidget(objectName="viewbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(6)
        bl.addWidget(QLabel("Selected image", objectName="hint"))
        for a in (self.a_rot_l, self.a_rot_r):
            bl.addWidget(_tool(a))
        for a in (self.a_fit, self.a_fill, self.a_center, self.a_reset):
            bl.addWidget(_tool(a))
        bl.addStretch(1)
        bl.addWidget(_tool(self.a_zoom_out))
        self.zoom_label = QLabel("100%", objectName="zoomLabel")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.zoom_label)
        bl.addWidget(_tool(self.a_zoom_in))
        bl.addWidget(_tool(self.a_fit_page))
        bl.addWidget(_tool(self.a_fit_width))
        rl.addWidget(bar)

        self.view = PageView()
        self.view.imageEdited.connect(self.doc.set_geometry)
        self.view.currentChanged.connect(self._on_view_selection)
        self.view.filesDropped.connect(lambda paths: self.add_paths(paths))
        self.view.deleteRequested.connect(self.remove_selected)
        self.view.zoomChanged.connect(self._on_zoom)
        rl.addWidget(self.view, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(side)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([340, 980])
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)
        self.setCentralWidget(root)

        self.status = self.statusBar()
        self.status.setSizeGripEnabled(False)
        tips = QLabel("Drag an image to move it · corners resize · top knob rotates "
                      "(Shift snaps to 15°) · Ctrl+scroll zooms", objectName="hint")
        self.status.addPermanentWidget(tips)

    # ------------------------------------------------------------ state

    def _restore_state(self) -> None:
        s = self.settings_store
        geo = s.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        split = s.value("window/splitter")
        if split is not None:
            self.splitter.restoreState(split)
        size = s.value("page/size", "A4")
        orient = s.value("page/orientation", "auto")
        try:
            margin = float(s.value("page/margin_mm", 10.0))
        except (TypeError, ValueError):
            margin = 10.0
        # Initial settings are not an undoable edit.
        self.doc.settings = PageSettings(size if size in PAGE_SIZE_CHOICES else "A4",
                                         orient if orient in ORIENTATIONS else "auto", margin)

    def closeEvent(self, ev) -> None:
        s = self.settings_store
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/splitter", self.splitter.saveState())
        s.setValue("page/size", self.doc.settings.size)
        s.setValue("page/orientation", self.doc.settings.orientation)
        s.setValue("page/margin_mm", self.doc.settings.margin_mm)
        self._pool.clear()
        super().closeEvent(ev)

    # ---------------------------------------------------------- adding

    def browse(self) -> None:
        start = self.settings_store.value("paths/last_open", str(Path.home() / "Pictures"))
        pattern = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add images", start,
                                                f"Images ({pattern});;All files (*)")
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths: list[str], row: int = -1) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        entries: list[ImageEntry] = []
        errors: list[tuple[str, str]] = []
        try:
            for path in expand_paths(paths):
                if not is_supported(path):
                    errors.append((path, "not a supported image type"))
                    continue
                try:
                    entries.append(probe(path))
                except Exception as ex:  # noqa: BLE001
                    errors.append((path, str(ex) or type(ex).__name__))
        finally:
            QApplication.restoreOverrideCursor()
        if entries:
            self.settings_store.setValue("paths/last_open", os.path.dirname(entries[0].path))
            self.doc.add(entries, None if row < 0 else row)
            for e in entries:
                self._request_preview(e.path)
            self._select_ids([e.id for e in entries[:1]])
            self.status.showMessage(f"Added {len(entries)} image{'s' * (len(entries) != 1)}", 4000)
        if errors:
            shown = "\n".join(f"• {os.path.basename(p)} — {why}" for p, why in errors[:12])
            more = f"\n…and {len(errors) - 12} more" if len(errors) > 12 else ""
            QMessageBox.warning(self, APP_NAME, f"Some files were skipped:\n\n{shown}{more}")

    def _request_preview(self, path: str) -> None:
        if path in self._pixmaps or path in self._pending:
            return
        self._pending.add(path)
        self._pool.start(_PreviewTask(path, self._signals))

    def _on_preview_loaded(self, path: str, image: QImage) -> None:
        self._pending.discard(path)
        pix = QPixmap.fromImage(image)
        self._pixmaps[path] = pix
        self._thumbs[path] = pix.scaled(THUMB_PX, THUMB_PX, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
        self.view.set_pixmap_for_path(path, pix)
        for i in range(self.list.count()):
            it = self.list.item(i)
            e = self.doc.get(it.data(ID_ROLE))
            if e is not None and e.path == path:
                it.setData(PIXMAP_ROLE, self._thumbs[path])

    def _on_preview_failed(self, path: str, message: str) -> None:
        self._pending.discard(path)
        self._failed[path] = message
        self.status.showMessage(f"Could not decode {os.path.basename(path)}: {message}", 8000)

    # --------------------------------------------------------- editing

    def _targets(self) -> list[str]:
        return self.list.selected_ids()

    def _move(self, delta: int) -> None:
        self.doc.move(self._targets(), delta)

    def remove_selected(self) -> None:
        ids = self._targets()
        if not ids:
            return
        row = min(self.doc.index_of(i) for i in ids)
        self.doc.remove(ids)
        if self.doc.entries:
            self._select_ids([self.doc.entries[min(row, len(self.doc.entries) - 1)].id])
        self.status.showMessage(f"Removed {len(ids)} image{'s' * (len(ids) != 1)} — Ctrl+Z to undo", 5000)

    def clear_all(self) -> None:
        if not self.doc.entries:
            return
        self.doc.clear()
        self.status.showMessage("Cleared — Ctrl+Z to undo", 5000)

    def _on_settings_edited(self, *_args, coalesce: str | None = None) -> None:
        if self._syncing:
            return
        self.doc.set_settings(PageSettings(self.size_combo.currentText(),
                                           self.orient_combo.currentData(),
                                           float(self.margin_spin.value())), coalesce)

    # ------------------------------------------------------- selection

    def _select_ids(self, ids: list[str]) -> None:
        self.list.clearSelection()
        first = None
        for i in ids:
            it = self.list.item_for(i)
            if it is not None:
                it.setSelected(True)
                first = first or it
        if first is not None:
            self.list.setCurrentItem(first, self.list.selectionModel().SelectionFlag.Select
                                     if len(ids) > 1 else self.list.selectionModel().SelectionFlag.ClearAndSelect)
            self.list.scrollToItem(first)

    def _on_list_selection(self) -> None:
        if self._syncing:
            return
        current = self.list.current_id()
        ids = self.list.selected_ids()
        if current not in ids:
            current = ids[0] if ids else None
        self.view.select(current)
        self._update_actions()

    def _on_view_selection(self, entry_id: str) -> None:
        if entry_id:
            it = self.list.item_for(entry_id)
            if it is not None and not (it.isSelected() and len(self.list.selectedItems()) == 1):
                self._syncing = True
                self.list.setCurrentItem(it, self.list.selectionModel().SelectionFlag.ClearAndSelect)
                self.list.scrollToItem(it)
                self._syncing = False
        else:
            self._syncing = True
            self.list.clearSelection()
            self._syncing = False
        self._update_actions()

    # ------------------------------------------------------------ sync

    def _on_doc_changed(self) -> None:
        selected = self.list.selected_ids() if hasattr(self, "list") else []
        current = self.list.current_id() if hasattr(self, "list") else None
        self._syncing = True
        try:
            self._sync_settings_widgets()
            self._sync_list(selected, current)
        finally:
            self._syncing = False
        live = {e.id for e in self.doc.entries}
        view_sel = current if current in live else next((i for i in selected if i in live), None)
        self.view.rebuild(self.doc, lambda e: self._pixmaps.get(e.path), view_sel)
        for e in self.doc.entries:  # entries restored by undo may need previews again
            self._request_preview(e.path)
        self._update_actions()

    def _sync_settings_widgets(self) -> None:
        s = self.doc.settings
        self.size_combo.setCurrentText(s.size)
        self.orient_combo.setCurrentIndex(max(0, self.orient_combo.findData(s.orientation)))
        self.margin_spin.setValue(s.margin_mm)

    def _sync_list(self, selected: list[str], current: str | None) -> None:
        lw = self.list
        scroll = lw.verticalScrollBar().value()
        lw.clear()
        for i, e in enumerate(self.doc.entries):
            it = QListWidgetItem(e.name)
            it.setData(ID_ROLE, e.id)
            it.setData(PAGE_ROLE, i + 1)
            it.setData(SUBTITLE_ROLE, f"{e.px_w} × {e.px_h} px")
            it.setData(PIXMAP_ROLE, self._thumbs.get(e.path))
            it.setToolTip(e.path)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            lw.addItem(it)
        sel = set(selected)
        for i in range(lw.count()):
            it = lw.item(i)
            if it.data(ID_ROLE) == current:
                lw.setCurrentItem(it, lw.selectionModel().SelectionFlag.NoUpdate)
            if it.data(ID_ROLE) in sel:
                it.setSelected(True)
        lw.verticalScrollBar().setValue(scroll)

    def _update_actions(self) -> None:
        n = len(self.doc.entries)
        has_sel = bool(self.list.selectedItems())
        for a in (self.a_remove, self.a_up, self.a_down, self.a_rot_l, self.a_rot_r,
                  self.a_fit, self.a_fill, self.a_center, self.a_reset):
            a.setEnabled(has_sel)
        for a in (self.a_clear, self.a_sort, self.a_export, self.a_select_all,
                  self.a_zoom_in, self.a_zoom_out, self.a_fit_page, self.a_fit_width):
            a.setEnabled(n > 0)
        self.export_btn.setEnabled(n > 0)
        self.a_undo.setEnabled(self.doc.can_undo)
        self.a_redo.setEnabled(self.doc.can_redo)
        self.count_label.setText(f"{n} page{'s' * (n != 1)}" if n else "")
        self.setWindowTitle(f"{APP_NAME} — {n} page{'s' * (n != 1)}" if n else APP_NAME)

    def _on_zoom(self, scale: float) -> None:
        # view_scale is scene points -> screen pixels; show it as print-size %
        # (100% = the page at its physical size on a 96 dpi screen).
        self.zoom_label.setText(f"{scale * 72 / 96 * 100:.0f}%")

    # ---------------------------------------------------------- export

    def export(self) -> None:
        if not self.doc.entries:
            return
        first = self.doc.entries[0]
        start_dir = self.settings_store.value("paths/last_export", os.path.dirname(first.path))
        default = os.path.join(start_dir, f"{Path(first.path).stem}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", default, "PDF document (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.settings_store.setValue("paths/last_export", os.path.dirname(path))

        entries = [replace(e, placement=replace(e.placement)) for e in self.doc.entries]
        settings = replace(self.doc.settings)
        dlg = QProgressDialog("Writing PDF…", "Cancel", 0, len(entries), self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(400)
        dlg.setValue(0)

        def progress(done: int, total: int) -> bool:
            dlg.setLabelText(f"Writing page {min(done + 1, total)} of {total}…")
            dlg.setValue(done)
            QApplication.processEvents()
            return not dlg.wasCanceled()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            export_pdf(entries, settings, path, progress, title=Path(path).stem)
        except ExportCancelled:
            self.status.showMessage("Export cancelled", 4000)
            return
        except PermissionError:
            QMessageBox.critical(self, APP_NAME, f"Could not write\n{path}\n\n"
                                 "Is the file open in another program?")
            return
        except Exception as ex:  # noqa: BLE001
            QMessageBox.critical(self, APP_NAME, f"Export failed:\n\n{ex}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            dlg.close()

        size_mb = os.path.getsize(path) / 1_048_576
        self.status.showMessage(f"Saved {path} ({size_mb:.1f} MB)", 8000)
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Saved {len(entries)} page{'s' * (len(entries) != 1)} to\n{path}\n({size_mb:.1f} MB)")
        open_btn = box.addButton("Open PDF", QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(open_btn)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif box.clickedButton() is folder_btn:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    # ------------------------------------------------- window-level drop

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            ev.acceptProposedAction()
            self.add_paths(paths)
