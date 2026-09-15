"""Left-hand list: one row per image/page, drag to reorder, drop files to add."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QListWidget, QListWidgetItem, QStyle,
                               QStyledItemDelegate)

from . import theme

ID_ROLE = Qt.ItemDataRole.UserRole
SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 1
PAGE_ROLE = Qt.ItemDataRole.UserRole + 2
PIXMAP_ROLE = Qt.ItemDataRole.UserRole + 3

ROW_H = 78
THUMB = 58


def drop_order(order: list[str], moving: set[str], row: int) -> list[str]:
    """Order after dropping the `moving` ids (kept in their relative order)
    at insertion row `row` (0..len(order)) of the current `order`."""
    before = sum(1 for i in order[:row] if i in moving)
    rest = [i for i in order if i not in moving]
    block = [i for i in order if i in moving]
    at = row - before
    return rest[:at] + block + rest[at:]


class RowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW_H)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(4, 2, -4, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#dcefea") if selected else QColor("#f1f5f4"))
            painter.drawRoundedRect(r, 8, 8)

        font = QFont(option.font)
        # page number
        num_rect = QRectF(r.left(), r.top(), 30, r.height())
        font.setPointSizeF(9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(theme.ACCENT_DARK if selected else theme.MUTED)
        painter.drawText(num_rect, Qt.AlignmentFlag.AlignCenter, str(index.data(PAGE_ROLE)))

        # thumbnail, letterboxed in a square
        box = QRectF(num_rect.right(), r.center().y() - THUMB / 2, THUMB, THUMB)
        pm: QPixmap | None = index.data(PIXMAP_ROLE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eef0f3"))
        painter.drawRoundedRect(box, 4, 4)
        if pm is not None and not pm.isNull():
            size = pm.size().scaled(int(THUMB), int(THUMB), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(0, 0, size.width(), size.height())
            target.moveCenter(box.center())
            painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
            painter.drawRect(target.adjusted(-0.5, -0.5, 0.5, 0.5))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(target, pm, QRectF(pm.rect()))

        # name and details
        text_left = box.right() + 12
        text_w = max(r.right() - text_left - 8, 10)
        font.setPointSizeF(9.5)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(theme.INK)
        name = painter.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "",
                                                Qt.TextElideMode.ElideMiddle, int(text_w))
        painter.drawText(QRectF(text_left, r.center().y() - 19, text_w, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        font.setPointSizeF(8.5)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(theme.MUTED)
        painter.drawText(QRectF(text_left, r.center().y() + 1, text_w, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         index.data(SUBTITLE_ROLE) or "")
        painter.restore()


class ImageList(QListWidget):
    filesDropped = Signal(list, int)   # paths, insert row (-1 = end)
    orderChanged = Signal(list)        # entry ids in the new order
    deleteRequested = Signal()
    addRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(RowDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(False)  # we draw our own insertion line
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._drop_row: int | None = None

    # -- helpers ----------------------------------------------------------

    def ids(self) -> list[str]:
        return [self.item(i).data(ID_ROLE) for i in range(self.count())]

    def selected_ids(self) -> list[str]:
        rows = sorted(self.row(it) for it in self.selectedItems())
        return [self.item(r).data(ID_ROLE) for r in rows]

    def current_id(self) -> str | None:
        it = self.currentItem()
        return it.data(ID_ROLE) if it is not None and it.isSelected() else None

    def item_for(self, entry_id: str) -> QListWidgetItem | None:
        for i in range(self.count()):
            if self.item(i).data(ID_ROLE) == entry_id:
                return self.item(i)
        return None

    def _row_at(self, pos) -> int:
        """Insertion row (0..count) for a drop at viewport position pos."""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return self.count()
        rect = self.visualRect(idx)
        return idx.row() + (1 if pos.y() > rect.center().y() else 0)

    # -- drag and drop ----------------------------------------------------

    def _is_external(self, ev) -> bool:
        return ev.source() is not self and ev.mimeData().hasUrls()

    def dragEnterEvent(self, ev) -> None:
        if self._is_external(ev) or ev.source() is self:
            ev.acceptProposedAction()
            self._drop_row = self._row_at(ev.position().toPoint())
            self.viewport().update()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev) -> None:
        if self._is_external(ev) or ev.source() is self:
            ev.acceptProposedAction()
            row = self._row_at(ev.position().toPoint())
            if row != self._drop_row:
                self._drop_row = row
                self.viewport().update()
            super().dragMoveEvent(ev)  # auto-scroll near the edges
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragLeaveEvent(self, ev) -> None:
        self._drop_row = None
        self.viewport().update()
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev) -> None:
        row = self._row_at(ev.position().toPoint())
        self._drop_row = None
        self.viewport().update()
        if self._is_external(ev):
            paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
            ev.acceptProposedAction()
            insert = -1 if row >= self.count() else row
            QTimer.singleShot(0, lambda: self.filesDropped.emit(paths, insert))
            return
        if ev.source() is self:
            # Compute the new order ourselves and tell Qt nothing moved, so it
            # never removes "source" rows behind our back.
            order = self.ids()
            new = drop_order(order, set(self.selected_ids()), row)
            ev.setDropAction(Qt.DropAction.IgnoreAction)
            ev.accept()
            if new != order:
                QTimer.singleShot(0, lambda: self.orderChanged.emit(new))
            return
        ev.ignore()

    # -- keys / clicks ----------------------------------------------------

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit()
            return
        super().keyPressEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        if not self.indexAt(ev.position().toPoint()).isValid():
            self.addRequested.emit()
            return
        super().mouseDoubleClickEvent(ev)

    # -- painting ---------------------------------------------------------

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.count() == 0:
            self._paint_empty(p)
        if self._drop_row is not None:
            if self.count() == 0:
                y = 8
            elif self._drop_row >= self.count():
                y = self.visualItemRect(self.item(self.count() - 1)).bottom() + 1
            else:
                y = self.visualItemRect(self.item(self._drop_row)).top()
            p.setPen(QPen(theme.ACCENT, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(10, y), QPointF(self.viewport().width() - 10, y))
            p.setBrush(theme.ACCENT)
            p.drawEllipse(QPointF(10, y), 3.5, 3.5)
        p.end()

    def _paint_empty(self, p: QPainter) -> None:
        vr = QRectF(self.viewport().rect()).adjusted(12, 12, -12, -12)
        active = self._drop_row is not None
        pen = QPen(theme.ACCENT if active else QColor("#c3c9d1"), 1.6, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor("#f0f7f5") if active else QColor("#fafbfc"))
        p.drawRoundedRect(vr, 12, 12)

        c = vr.center()
        # upward arrow into a tray
        p.setPen(QPen(theme.ACCENT if active else QColor("#9aa3ae"), 2.2,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        top = c.y() - 58
        p.drawLine(QPointF(c.x(), top), QPointF(c.x(), top + 30))
        p.drawPolyline([QPointF(c.x() - 10, top + 10), QPointF(c.x(), top), QPointF(c.x() + 10, top + 10)])
        p.drawPolyline([QPointF(c.x() - 22, top + 26), QPointF(c.x() - 22, top + 40),
                        QPointF(c.x() + 22, top + 40), QPointF(c.x() + 22, top + 26)])

        font = QFont(self.font())
        font.setPointSizeF(11)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(theme.INK)
        text = QRect(int(vr.left()), int(c.y()), int(vr.width()), 24)
        p.drawText(text, Qt.AlignmentFlag.AlignCenter, "Drag & drop images here")
        font.setPointSizeF(9)
        font.setWeight(QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(theme.MUTED)
        p.drawText(text.translated(0, 24), Qt.AlignmentFlag.AlignCenter, "or double-click to browse")
        p.drawText(text.translated(0, 52), Qt.AlignmentFlag.AlignCenter,
                   "JPG · PNG · WebP · TIFF · BMP · GIF")
