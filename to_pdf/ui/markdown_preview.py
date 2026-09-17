"""Paginated preview of a rendered Markdown document.

Pages are painted with markdown_render.paint_page(), the same function that
writes the PDF, so what is on screen is what gets exported. Scene units are
the renderer's 96-dpi layout pixels, so zoom 1.0 is the physical page size on
a standard screen.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView

from ..markdown_render import MarkdownSettings, page_size_px, paint_page
from . import theme
from .common import paint_page_shadow

PAGE_GAP = 28.0
MIN_ZOOM, MAX_ZOOM = 0.1, 6.0


class _PageItem(QGraphicsItem):
    def __init__(self, preview: "MarkdownPreview", index: int, w: float, h: float):
        super().__init__()
        self._preview = preview
        self.index = index
        self._rect = QRectF(0, 0, w, h)
        # Re-rendering text on every scroll step is wasteful; cache per zoom level.
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, option, widget=None) -> None:
        doc = self._preview.doc
        if doc is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        paint_page(painter, doc, self._preview.settings, self.index)
        painter.restore()


class MarkdownPreview(QGraphicsView):
    zoomChanged = Signal(float)
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.doc = None
        self.settings = MarkdownSettings()
        self.view_scale = 1.0
        self._fit_mode: str | None = "width"
        self._pages: list[QRectF] = []
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(theme.CANVAS)
        self.setAcceptDrops(True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def set_document(self, doc, settings: MarkdownSettings) -> None:
        """Show a freshly built document, keeping the reading position."""
        frac = self.scroll_fraction()
        old_count = len(self._pages)
        self.doc = doc
        self.settings = settings
        scene = self.scene()
        scene.clear()
        self._pages.clear()
        w, h = page_size_px(settings)
        count = max(1, doc.pageCount())
        y = 0.0
        for i in range(count):
            item = _PageItem(self, i, w, h)
            item.setPos(-w / 2, y)
            scene.addItem(item)
            self._pages.append(QRectF(-w / 2, y, w, h))
            y += h + PAGE_GAP
        pad = 40.0
        scene.setSceneRect(QRectF(-w / 2 - pad, -pad, w + 2 * pad, y - PAGE_GAP + 2 * pad))
        if self._fit_mode:
            self._apply_fit(center=False)
        if old_count:
            self.set_scroll_fraction(frac)
        else:
            self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self.viewport().update()

    # -- scrolling --------------------------------------------------------

    def scroll_fraction(self) -> float:
        bar = self.verticalScrollBar()
        span = bar.maximum() - bar.minimum()
        return (bar.value() - bar.minimum()) / span if span > 0 else 0.0

    def set_scroll_fraction(self, frac: float) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(round(bar.minimum() + (bar.maximum() - bar.minimum()) * max(0.0, min(1.0, frac))))

    # -- zoom -------------------------------------------------------------

    def set_zoom(self, scale: float, user: bool = True) -> None:
        scale = max(MIN_ZOOM, min(MAX_ZOOM, scale))
        if user:
            self._fit_mode = None
        self.setTransform(QTransform.fromScale(scale, scale))
        self.view_scale = scale
        self.zoomChanged.emit(scale)

    def zoom_by(self, factor: float) -> None:
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.set_zoom(self.view_scale * factor)
        self.setTransformationAnchor(anchor)

    def fit_width(self) -> None:
        self._fit_mode = "width"
        self._apply_fit(center=True)

    def fit_page(self) -> None:
        self._fit_mode = "page"
        self._apply_fit(center=True)

    def _current_page(self) -> QRectF | None:
        if not self._pages:
            return None
        center = self.mapToScene(self.viewport().rect().center())
        return min(self._pages, key=lambda r: abs(r.center().y() - center.y()))

    def _apply_fit(self, center: bool) -> None:
        page = self._current_page()
        if page is None:
            return
        frac = self.scroll_fraction()
        if self._fit_mode == "width":
            scale = (self.viewport().width() - 4) / self.sceneRect().width()
        else:
            scale = min((self.viewport().width() - 4) / self.sceneRect().width(),
                        (self.viewport().height() - 48) / page.height())
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.set_zoom(scale, user=False)
        self.setTransformationAnchor(anchor)
        if center and self._fit_mode == "page":
            self.centerOn(page.center())
        else:
            self.set_scroll_fraction(frac)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self._fit_mode:
            self._apply_fit(center=False)

    def wheelEvent(self, ev) -> None:
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.view_scale * 1.0016 ** ev.angleDelta().y())
            ev.accept()
        else:
            super().wheelEvent(ev)

    # -- drop -------------------------------------------------------------

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            ev.acceptProposedAction()
            self.filesDropped.emit(paths)

    # -- painting ---------------------------------------------------------

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, theme.CANVAS)
        px = 1.0 / self.view_scale
        for r in self._pages:
            if r.adjusted(-8 * px, -8 * px, 8 * px, 8 * px).intersects(rect):
                paint_page_shadow(painter, r, px)
                painter.fillRect(r, theme.PAGE)
