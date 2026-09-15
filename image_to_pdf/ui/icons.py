"""Small line icons painted at runtime, so they are crisp at any DPI."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme

_SIZE = 20


def _icon(draw, color: QColor = theme.INK) -> QIcon:
    icon = QIcon()
    for scale in (1, 2, 3):
        pm = QPixmap(_SIZE * scale, _SIZE * scale)
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        draw(p)
        p.end()
        icon.addPixmap(pm)
    return icon


def _arrow_head(p: QPainter, tip: QPointF, angle_deg: float, size: float = 4.2) -> None:
    a = math.radians(angle_deg)
    for side in (-1, 1):
        b = a + side * math.radians(150)
        p.drawLine(tip, QPointF(tip.x() + size * math.cos(b), tip.y() + size * math.sin(b)))


def rotate(clockwise: bool) -> QIcon:
    def draw(p: QPainter) -> None:
        r = QRectF(4, 4, 12, 12)
        # Arc from the top, going round 270 degrees; Qt angles are CCW from 3 o'clock.
        if clockwise:
            p.drawArc(r, 90 * 16, -270 * 16)
            _arrow_head(p, QPointF(4, 10), -90)
        else:
            p.drawArc(r, 90 * 16, 270 * 16)
            _arrow_head(p, QPointF(16, 10), -90)
    return _icon(draw)


def chevron(up: bool) -> QIcon:
    def draw(p: QPainter) -> None:
        y0, y1 = (12.5, 7.5) if up else (7.5, 12.5)
        p.drawPolyline([QPointF(5, y0), QPointF(10, y1), QPointF(15, y0)])
    return _icon(draw)


def plus() -> QIcon:
    return _icon(lambda p: (p.drawLine(QPointF(10, 4.5), QPointF(10, 15.5)),
                            p.drawLine(QPointF(4.5, 10), QPointF(15.5, 10))))


def minus() -> QIcon:
    return _icon(lambda p: p.drawLine(QPointF(4.5, 10), QPointF(15.5, 10)))


def undo(redo: bool = False) -> QIcon:
    def draw(p: QPainter) -> None:
        if redo:
            p.translate(_SIZE, 0)
            p.scale(-1, 1)
        path = QPainterPath(QPointF(5, 8))
        path.lineTo(12, 8)
        path.cubicTo(18, 8, 18, 16, 12, 16)
        path.lineTo(8, 16)
        p.drawPath(path)
        _arrow_head(p, QPointF(5, 8), 180, 3.8)
    return _icon(draw)


def trash() -> QIcon:
    def draw(p: QPainter) -> None:
        p.drawLine(QPointF(4, 6), QPointF(16, 6))
        p.drawPolyline([QPointF(8, 6), QPointF(8.5, 3.8), QPointF(11.5, 3.8), QPointF(12, 6)])
        p.drawPolyline([QPointF(5.5, 6), QPointF(6.5, 16.5), QPointF(13.5, 16.5), QPointF(14.5, 6)])
    return _icon(draw)


def more() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.INK)
        for x in (5, 10, 15):
            p.drawEllipse(QPointF(x, 10), 1.5, 1.5)
    return _icon(draw)
