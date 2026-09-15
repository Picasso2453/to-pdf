"""The PDF preview: pages stacked vertically, one image per page.

Each image is an ImageItem whose origin is the image centre, so the item's
position is the centre on the page and its rotation is the page rotation.
Item coordinates therefore differ from scene coordinates only by rotation,
which keeps hit-testing of the handles simple. Handles are drawn by the view
(drawForeground) so they stay crisp and are never dimmed by the overflow mask.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsScene, QGraphicsView

from ..model import MIN_WIDTH_PT, MM, Document, ImageEntry, resolve
from . import theme

PAGE_GAP = 40.0       # points between pages
HANDLE_PX = 9.0       # drawn handle size, screen pixels
HIT_PX = 11.0         # grab radius around a handle
ROT_OFFSET_PX = 30.0  # distance of the rotate knob above the top edge
SNAP_PX = 8.0         # centre snapping distance
ROT_SNAP_DEG = 3.0    # snap to right angles within this many degrees
MIN_ZOOM, MAX_ZOOM = 0.05, 8.0

_rotate_cursor: QCursor | None = None


def rotate_cursor() -> QCursor:
    """A circular-arrow cursor, drawn once."""
    global _rotate_cursor
    if _rotate_cursor is None:
        pm = QPixmap(26, 26)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for colour, width in ((QColor("white"), 4.2), (theme.INK, 1.8)):
            p.setPen(QPen(colour, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(5, 5, 16, 16), 100 * 16, 280 * 16)
            head = QPolygonF([QPointF(9.5, 1.5), QPointF(15.5, 5.2), QPointF(9.5, 9.0)])
            p.setBrush(colour)
            p.drawPolygon(head)
        p.end()
        _rotate_cursor = QCursor(pm, 13, 13)
    return _rotate_cursor


def _length(p: QPointF) -> float:
    return math.hypot(p.x(), p.y())


class ImageItem(QGraphicsObject):
    edited = Signal(str)  # entry id, emitted once when a drag ends

    def __init__(self, entry: ImageEntry, width: float, page_rect: QRectF, view: "PageView"):
        super().__init__()
        self.entry_id = entry.id
        self.path = entry.path
        self.aspect = entry.aspect
        self.page_rect = page_rect
        self._w = width
        self._view = view
        self._pixmap: QPixmap | None = None
        self.drag: dict | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)
        self.setAcceptHoverEvents(True)

    # -- geometry ---------------------------------------------------------

    @property
    def w(self) -> float:
        return self._w

    @property
    def h(self) -> float:
        return self._w / self.aspect

    def set_width(self, w: float) -> None:
        self.prepareGeometryChange()
        self._w = max(w, MIN_WIDTH_PT)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def refresh_geometry(self) -> None:
        """Handle sizes are in screen pixels, so the zoom level changes the bounds."""
        self.prepareGeometryChange()

    def _px(self, n: float) -> float:
        return n / self._view.view_scale

    def image_rect(self) -> QRectF:
        return QRectF(-self.w / 2, -self.h / 2, self.w, self.h)

    def corners(self) -> list[tuple[float, float]]:
        return [(-1, -1), (1, -1), (1, 1), (-1, 1)]

    def corner_point(self, sx: float, sy: float) -> QPointF:
        return QPointF(sx * self.w / 2, sy * self.h / 2)

    def knob_point(self) -> QPointF:
        return QPointF(0, -self.h / 2 - self._px(ROT_OFFSET_PX))

    def boundingRect(self) -> QRectF:
        m = self._px(HIT_PX + 2)
        return self.image_rect().adjusted(-m, -m - self._px(ROT_OFFSET_PX), m, m)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.image_rect())
        if self.isSelected():
            r = self._px(HIT_PX)
            for sx, sy in self.corners():
                path.addEllipse(self.corner_point(sx, sy), r, r)
            path.addEllipse(self.knob_point(), r, r)
        return path

    def hit(self, pos: QPointF) -> tuple | None:
        if self.isSelected():
            r = self._px(HIT_PX)
            if _length(pos - self.knob_point()) <= r:
                return ("rotate",)
            for sx, sy in self.corners():
                if _length(pos - self.corner_point(sx, sy)) <= r:
                    return ("resize", sx, sy)
        if self.image_rect().contains(pos):
            return ("move",)
        return None

    # -- painting ---------------------------------------------------------

    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self.image_rect()
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(r, self._pixmap, QRectF(self._pixmap.rect()))
        else:
            painter.fillRect(r, QColor("#eceef1"))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self.prepareGeometryChange()  # the shape grows handles
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(1 if self.isSelected() else 0)
        return super().itemChange(change, value)

    # -- interaction ------------------------------------------------------

    def hoverMoveEvent(self, ev) -> None:
        hit = self.hit(ev.pos())
        if hit is None:
            self.unsetCursor()
        elif hit[0] == "rotate":
            self.setCursor(rotate_cursor())
        elif hit[0] == "resize":
            v = self.mapToScene(self.corner_point(hit[1], hit[2])) - self.pos()
            deg = math.degrees(math.atan2(v.y(), v.x())) % 180
            shapes = [(0, Qt.CursorShape.SizeHorCursor), (45, Qt.CursorShape.SizeFDiagCursor),
                      (90, Qt.CursorShape.SizeVerCursor), (135, Qt.CursorShape.SizeBDiagCursor),
                      (180, Qt.CursorShape.SizeHorCursor)]
            self.setCursor(min(shapes, key=lambda s: abs(s[0] - deg))[1])
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        hit = self.hit(ev.pos())
        if hit is None:
            ev.ignore()
            return
        if not self.isSelected():
            self.scene().clearSelection()
            self.setSelected(True)
            hit = ("move",)
        self.setFocus()
        d = {"kind": hit[0], "press": ev.scenePos(), "pos": QPointF(self.pos()),
             "w": self.w, "rot": self.rotation(), "changed": False}
        if hit[0] == "resize":
            corner = self.corner_point(hit[1], hit[2])
            anchor = self.mapToScene(-corner)
            diag = self.mapToScene(corner) - anchor
            d.update(anchor=anchor, diag=_length(diag), u=diag / _length(diag))
        elif hit[0] == "rotate":
            v = ev.scenePos() - self.pos()
            d["a0"] = math.atan2(v.y(), v.x())
        self.drag = d
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        d = self.drag
        if d is None:
            return
        p = ev.scenePos()
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if d["kind"] == "move":
            delta = p - d["press"]
            if shift:  # constrain to the dominant axis
                if abs(delta.x()) > abs(delta.y()):
                    delta.setY(0)
                else:
                    delta.setX(0)
            new = d["pos"] + delta
            c = self.page_rect.center()
            if abs(new.x() - c.x()) < self._px(SNAP_PX):
                new.setX(c.x())
            if abs(new.y() - c.y()) < self._px(SNAP_PX):
                new.setY(c.y())
            self.setPos(new)
        elif d["kind"] == "resize":
            # The opposite corner stays put; the dragged corner follows the
            # mouse projected onto the diagonal, so the aspect ratio holds.
            v = p - d["anchor"]
            min_len = d["diag"] * MIN_WIDTH_PT / d["w"]
            length = max(v.x() * d["u"].x() + v.y() * d["u"].y(), min_len)
            self.set_width(d["w"] * length / d["diag"])
            self.setPos(d["anchor"] + d["u"] * (length / 2))
        else:
            v = p - self.pos()
            rot = d["rot"] + math.degrees(math.atan2(v.y(), v.x()) - d["a0"])
            if shift:
                rot = round(rot / 15.0) * 15.0
            else:
                nearest = round(rot / 90.0) * 90.0
                if abs(rot - nearest) < ROT_SNAP_DEG:
                    rot = nearest
            self.setRotation(rot)
        d["changed"] = True
        self._view.viewport().update()

    def mouseReleaseEvent(self, ev) -> None:
        d, self.drag = self.drag, None
        if d and d["changed"]:
            self.edited.emit(self.entry_id)
        self._view.viewport().update()


class PageView(QGraphicsView):
    imageEdited = Signal(str, float, float, float, float)  # id, cx, cy, w (page pt), rotation
    currentChanged = Signal(str)  # id of the image selected in the preview, "" for none
    filesDropped = Signal(list)
    deleteRequested = Signal()
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.view_scale = 1.0
        self._fit_mode: str | None = "page"  # re-applied on resize until the user zooms
        self._pages: list[tuple[str, QRectF, str]] = []
        self._items: dict[str, ImageItem] = {}
        self._suppress = False
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform
                            | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(theme.CANVAS)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scene().selectionChanged.connect(self._on_scene_selection)

    # -- content ----------------------------------------------------------

    def rebuild(self, doc: Document, pixmap_for: Callable[[ImageEntry], QPixmap | None],
                selected_id: str | None) -> None:
        was_empty = not self._pages
        self._suppress = True
        scene = self.scene()
        scene.clear()
        self._items.clear()
        self._pages.clear()
        y, max_w = 0.0, 0.0
        for e in doc.entries:
            g = resolve(e, doc.settings)
            rect = QRectF(-g.page_w / 2, y, g.page_w, g.page_h)
            item = ImageItem(e, g.w, rect, self)
            item.set_pixmap(pixmap_for(e))
            item.setPos(rect.left() + g.cx, rect.top() + g.cy)
            item.setRotation(g.rotation)
            item.edited.connect(self._on_item_edited)
            scene.addItem(item)
            self._items[e.id] = item
            self._pages.append((e.id, rect, e.name))
            y += g.page_h + PAGE_GAP
            max_w = max(max_w, g.page_w)
        pad = 60.0
        if self._pages:
            scene.setSceneRect(QRectF(-max_w / 2 - pad, -pad, max_w + 2 * pad, y - PAGE_GAP + 2 * pad))
        else:
            scene.setSceneRect(QRectF(0, 0, 1, 1))
        if selected_id in self._items:
            self._items[selected_id].setSelected(True)
        self._suppress = False
        if was_empty and self._pages:
            self._fit_mode = "page"
        if self._fit_mode:
            self._apply_fit()
        self.viewport().update()

    def set_pixmap_for_path(self, path: str, pixmap: QPixmap) -> None:
        for item in self._items.values():
            if item.path == path:
                item.set_pixmap(pixmap)

    def selected_item(self) -> ImageItem | None:
        items = self.scene().selectedItems()
        return items[0] if items and isinstance(items[0], ImageItem) else None

    def select(self, entry_id: str | None, scroll: bool = True) -> None:
        self._suppress = True
        self.scene().clearSelection()
        item = self._items.get(entry_id or "")
        if item is not None:
            item.setSelected(True)
        self._suppress = False
        if item is not None and scroll:
            self.scroll_to(entry_id)
        self.viewport().update()

    def scroll_to(self, entry_id: str) -> None:
        item = self._items.get(entry_id)
        if item is None:
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        if not visible.contains(item.page_rect):
            self.centerOn(item.page_rect.center())

    def _on_scene_selection(self) -> None:
        if self._suppress:
            return
        item = self.selected_item()
        self.currentChanged.emit(item.entry_id if item else "")

    def _on_item_edited(self, entry_id: str) -> None:
        item = self._items.get(entry_id)
        if item is None:
            return
        r = item.page_rect
        self.imageEdited.emit(entry_id, item.x() - r.left(), item.y() - r.top(),
                              item.w, item.rotation())

    # -- zoom -------------------------------------------------------------

    def set_zoom(self, scale: float, user: bool = True) -> None:
        scale = max(MIN_ZOOM, min(MAX_ZOOM, scale))
        if user:
            self._fit_mode = None
        self.setTransform(QTransform.fromScale(scale, scale))
        self.view_scale = scale
        for item in self._items.values():
            item.refresh_geometry()
        self.zoomChanged.emit(scale)

    def zoom_by(self, factor: float) -> None:
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.set_zoom(self.view_scale * factor)
        self.setTransformationAnchor(anchor)

    def fit_page(self) -> None:
        self._fit_mode = "page"
        self._apply_fit()

    def fit_width(self) -> None:
        self._fit_mode = "width"
        self._apply_fit()

    def _focus_page(self) -> QRectF | None:
        item = self.selected_item()
        if item is not None:
            return item.page_rect
        if not self._pages:
            return None
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        return max((r for _, r, _ in self._pages),
                   key=lambda r: r.intersected(visible).height() * r.intersected(visible).width())

    def _apply_fit(self) -> None:
        page = self._focus_page()
        if page is None:
            return
        vw, vh = self.viewport().width() - 48, self.viewport().height() - 72
        if self._fit_mode == "width":
            # Fit the scene rect (pages plus padding) so no horizontal scrollbar appears.
            scale = (self.viewport().width() - 4) / self.sceneRect().width()
        else:
            scale = min(vw / page.width(), vh / page.height())
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.set_zoom(scale, user=False)
        self.setTransformationAnchor(anchor)
        if self._fit_mode == "page":
            self.centerOn(page.center())
        else:
            self.centerOn(QPointF(0, page.top() + self.mapToScene(self.viewport().rect())
                                  .boundingRect().height() / 2 - 30 / scale))

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self._fit_mode:
            self._apply_fit()

    def wheelEvent(self, ev) -> None:
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.view_scale * 1.0016 ** ev.angleDelta().y())
            ev.accept()
        else:
            super().wheelEvent(ev)

    # -- input ------------------------------------------------------------

    def mousePressEvent(self, ev) -> None:
        super().mousePressEvent(ev)
        if ev.button() == Qt.MouseButton.LeftButton and self.itemAt(ev.position().toPoint()) is None:
            # A click on the blank part of a page selects that page's image.
            p = self.mapToScene(ev.position().toPoint())
            for entry_id, rect, _ in self._pages:
                if rect.contains(p):
                    self._items[entry_id].setSelected(True)
                    break

    def keyPressEvent(self, ev) -> None:
        item = self.selected_item()
        key = ev.key()
        if item is not None and key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10.0 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(key, 0.0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(key, 0.0)
            item.setPos(item.pos() + QPointF(dx, dy))
            self._on_item_edited(item.entry_id)
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit()
            return
        super().keyPressEvent(ev)

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
        painter.setPen(Qt.PenStyle.NoPen)
        for _, r, _ in self._pages:
            if not r.adjusted(-8 * px, -8 * px, 8 * px, 8 * px).intersects(rect):
                continue
            for spread, alpha in ((6, 10), (3, 16), (1, 26)):
                painter.setBrush(QColor(20, 28, 40, alpha))
                s = spread * px
                painter.drawRoundedRect(r.adjusted(-s, -s + 2 * px, s, s + 2 * px), s, s)
            painter.fillRect(r, theme.PAGE)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if not self._pages:
            self._draw_empty_state(painter)
            return
        # Dim whatever falls outside the pages: it will be cropped in the PDF.
        outside = QPainterPath()
        outside.addRect(rect.adjusted(-2, -2, 2, 2))
        pages = QPainterPath()
        for _, r, _ in self._pages:
            pages.addRect(r)
        mask = QColor(theme.CANVAS)
        mask.setAlpha(215)
        painter.fillPath(outside.subtracted(pages), mask)

        self._draw_labels(painter, rect)
        item = self.selected_item()
        if item is not None:
            self._draw_chrome(painter, item)

    def _draw_labels(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        world = painter.transform()
        painter.resetTransform()
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        painter.setFont(font)
        painter.setPen(theme.MUTED)
        for i, (_, r, name) in enumerate(self._pages):
            if not r.adjusted(0, -30 / self.view_scale, 0, 0).intersects(rect):
                continue
            top_left = world.map(r.topLeft())
            width = world.map(r.topRight()).x() - top_left.x()
            label = f"{i + 1}    {name}"
            elided = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideMiddle, int(max(width, 40)))
            painter.drawText(QPointF(top_left.x(), top_left.y() - 8), elided)
        painter.restore()

    def _draw_chrome(self, painter: QPainter, item: ImageItem) -> None:
        px = 1.0 / self.view_scale
        pen = QPen(theme.ACCENT, 1.5)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(item.mapToScene(item.image_rect()))

        top = item.mapToScene(QPointF(0, -item.h / 2))
        knob = item.mapToScene(item.knob_point())
        painter.drawLine(top, knob)
        painter.setBrush(QBrush(QColor("white")))
        hs = HANDLE_PX * px
        for sx, sy in item.corners():
            c = item.mapToScene(item.corner_point(sx, sy))
            painter.drawRoundedRect(QRectF(c.x() - hs / 2, c.y() - hs / 2, hs, hs), 2 * px, 2 * px)
        painter.setBrush(QBrush(theme.ACCENT))
        painter.drawEllipse(knob, 5.5 * px, 5.5 * px)

        # While dragging, show the value being changed next to the cursor.
        d = item.drag
        if d and d["changed"]:
            if d["kind"] == "rotate":
                angle = (item.rotation() + 180) % 360 - 180
                text = f"{angle:.0f}\N{DEGREE SIGN}"
            elif d["kind"] == "resize":
                text = f"{item.w / MM:.0f} \N{MULTIPLICATION SIGN} {item.h / MM:.0f} mm"
            else:
                text = ""
            if text:
                self._draw_badge(painter, knob if d["kind"] == "rotate" else
                                 item.mapToScene(item.corner_point(1, 1)), text)

    def _draw_badge(self, painter: QPainter, at: QPointF, text: str) -> None:
        painter.save()
        p = painter.transform().map(at)
        painter.resetTransform()
        font = QFont(self.font())
        font.setPointSizeF(9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        box = QRectF(p.x() + 14, p.y() - 10, fm.horizontalAdvance(text) + 16, fm.height() + 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.INK)
        painter.drawRoundedRect(box, 5, 5)
        painter.setPen(QColor("white"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _draw_empty_state(self, painter: QPainter) -> None:
        painter.save()
        painter.resetTransform()
        vr = QRectF(self.viewport().rect())
        page = QRectF(0, 0, 150, 200)
        page.moveCenter(vr.center() - QPointF(0, 40))
        pen = QPen(QColor("#b9c0c9"), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 255, 255, 150))
        painter.drawRoundedRect(page, 6, 6)
        # a small "picture" glyph inside the page
        glyph = QPainterPath()
        c = page.center()
        glyph.moveTo(c.x() - 36, c.y() + 22)
        glyph.lineTo(c.x() - 10, c.y() - 8)
        glyph.lineTo(c.x() + 6, c.y() + 10)
        glyph.lineTo(c.x() + 16, c.y())
        glyph.lineTo(c.x() + 36, c.y() + 22)
        glyph.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#cfd5dc"))
        painter.drawPath(glyph)
        painter.drawEllipse(QPointF(c.x() + 18, c.y() - 22), 7, 7)

        font = QFont(self.font())
        font.setPointSizeF(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(theme.INK)
        text_rect = QRectF(vr.left(), page.bottom() + 20, vr.width(), 24)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, "Your PDF preview appears here")
        font.setPointSizeF(9.5)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(theme.MUTED)
        painter.drawText(text_rect.translated(0, 26), Qt.AlignmentFlag.AlignCenter,
                         "Drop images anywhere in the window, or use Add images")
        painter.restore()
