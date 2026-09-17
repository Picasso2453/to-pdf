"""Start screen: pick what to turn into a PDF."""

from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, Property, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import __version__
from ..imaging import expand_paths, is_supported
from . import theme
from .common import APP_NAME, tool_pixmap

MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd", ".txt"}


def is_markdown(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in MARKDOWN_EXTENSIONS


def classify(paths: list[str]) -> str | None:
    """Which tool a set of dropped/opened paths belongs to."""
    files = expand_paths(paths)
    if any(is_markdown(p) for p in files) and not any(is_supported(p) for p in files):
        return "markdown"
    if any(is_supported(p) for p in files):
        return "images"
    if any(is_markdown(p) for p in files):
        return "markdown"
    return None


class ToolCard(QWidget):
    """A large clickable tile with the tool's artwork, name and description."""

    clicked = Signal()
    filesDropped = Signal(list)

    def __init__(self, kind: str, title: str, description: str, shortcut_hint: str,
                 accent: QColor, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.title = title
        self.description = description
        self.shortcut_hint = shortcut_hint
        self.accent = accent
        self._hover = 0.0
        self._drop = False
        self._art = tool_pixmap(kind, 96)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(300, 330)
        self.setMaximumWidth(360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(title)
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, v: float) -> None:
        self._hover = v
        self.update()

    hover = Property(float, _get_hover, _set_hover)

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, ev) -> None:
        self._animate(1.0)

    def leaveEvent(self, ev) -> None:
        self._animate(0.0)

    def focusInEvent(self, ev) -> None:
        self.update()

    def focusOutEvent(self, ev) -> None:
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(ev)

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self._drop = True
            self.update()

    def dragLeaveEvent(self, ev) -> None:
        self._drop = False
        self.update()

    def dropEvent(self, ev) -> None:
        self._drop = False
        self.update()
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            ev.acceptProposedAction()
            self.filesDropped.emit(paths)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self._hover
        lift = 3.0 * h
        r = QRectF(self.rect()).adjusted(10, 10 - lift, -10, -14 - lift)

        # shadow grows as the card lifts
        for spread, alpha in ((12, 8 + 6 * h), (6, 12 + 10 * h), (2, 18 + 8 * h)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 28, 40, int(alpha)))
            p.drawRoundedRect(r.adjusted(-spread / 2, -spread / 4 + 3 + lift * 0.6,
                                         spread / 2, spread / 2 + 3 + lift), 18, 18)

        border = QColor(self.accent) if (self._drop or self.hasFocus()) else QColor("#dfe3e8")
        if not (self._drop or self.hasFocus()) and h > 0:
            border = QColor(
                int(0xdf + (self.accent.red() - 0xdf) * h * 0.6),
                int(0xe3 + (self.accent.green() - 0xe3) * h * 0.6),
                int(0xe8 + (self.accent.blue() - 0xe8) * h * 0.6))
        bg = QColor("#ffffff")
        if self._drop:
            bg = QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 22)
            p.setBrush(QColor("#ffffff"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, 16, 16)
        p.setBrush(bg)
        p.setPen(QPen(border, 2.0 if (self._drop or self.hasFocus()) else 1.2,
                      Qt.PenStyle.DashLine if self._drop else Qt.PenStyle.SolidLine))
        p.drawRoundedRect(r, 16, 16)

        # artwork
        art = QRectF(0, 0, 96, 96)
        art.moveCenter(QPointF(r.center().x(), r.top() + 36 + 48))
        if not self._art.isNull():
            p.drawPixmap(art.toRect(), self._art)

        # title
        font = QFont(self.font())
        font.setPointSizeF(15)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(theme.INK)
        title_rect = QRectF(r.left() + 20, art.bottom() + 18, r.width() - 40, 30)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self.title)

        # description
        font.setPointSizeF(9.5)
        font.setWeight(QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(theme.MUTED)
        desc_rect = QRectF(r.left() + 26, title_rect.bottom() + 6, r.width() - 52, r.bottom() - 46 - title_rect.bottom() - 6)
        p.drawText(desc_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   "Drop files to open them here" if self._drop else self.description)

        # shortcut chip
        font.setPointSizeF(8.5)
        p.setFont(font)
        fm = p.fontMetrics()
        chip_w = fm.horizontalAdvance(self.shortcut_hint) + 16
        chip = QRectF(0, 0, chip_w, fm.height() + 6)
        chip.moveCenter(QPointF(r.center().x(), r.bottom() - 24))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f1f3f5"))
        p.drawRoundedRect(chip, 6, 6)
        p.setPen(theme.MUTED)
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, self.shortcut_hint)
        p.end()


class Launcher(QWidget):
    chosen = Signal(str, list)  # tool kind, paths to open (may be empty)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("launcher")
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.addStretch(3)

        brand = QLabel(APP_NAME, objectName="launcherTitle")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(brand)
        sub = QLabel("What would you like to turn into a PDF?", objectName="launcherSubtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(sub)
        outer.addSpacing(30)

        row = QHBoxLayout()
        row.setSpacing(22)
        row.addStretch(1)
        self.images_card = ToolCard(
            "images", "Images",
            "Combine photos and scans into one PDF, then reorder, resize and rotate them.",
            "Press 1", QColor("#0e7c66"))
        self.markdown_card = ToolCard(
            "markdown", "Markdown",
            "Paste or write Markdown and watch it turn into a clean PDF as you type.",
            "Press 2", QColor("#3b4fb0"))
        for card in (self.images_card, self.markdown_card):
            card.clicked.connect(lambda c=card: self.chosen.emit(c.kind, []))
            card.filesDropped.connect(lambda paths, c=card: self.chosen.emit(c.kind, paths))
            row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)

        outer.addSpacing(26)
        hint = QLabel("Tip: drop files anywhere on this window and the right tool opens.",
                      objectName="hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(hint)
        outer.addStretch(4)
        version = QLabel(f"v{__version__}", objectName="hint")
        version.setAlignment(Qt.AlignmentFlag.AlignRight)
        outer.addWidget(version)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_1:
            self.chosen.emit("images", [])
            return
        if ev.key() == Qt.Key.Key_2:
            self.chosen.emit("markdown", [])
            return
        super().keyPressEvent(ev)

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        kind = classify(paths)
        if kind:
            ev.acceptProposedAction()
            self.chosen.emit(kind, paths)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.SURFACE)
        p.end()
