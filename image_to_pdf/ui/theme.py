"""Colours and the application stylesheet."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..resources import asset_path

ACCENT = QColor("#0e7c66")
ACCENT_DARK = QColor("#0a5e4d")
CANVAS = QColor("#e4e6ea")
PAGE = QColor("#ffffff")
INK = QColor("#1d232b")
MUTED = QColor("#6b7380")
BORDER = QColor("#d9dde3")
SURFACE = QColor("#f6f7f9")

STYLESHEET = f"""
QMainWindow, QWidget#root {{ background: {SURFACE.name()}; }}
QWidget {{ color: {INK.name()}; font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt; }}

QWidget#sidebar {{ background: #ffffff; border-right: 1px solid {BORDER.name()}; }}
QLabel#panelTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#hint, QLabel#zoomLabel {{ color: {MUTED.name()}; font-size: 9pt; }}
QLabel#brand {{ font-size: 13pt; font-weight: 700; color: {ACCENT_DARK.name()}; padding-right: 6px; }}

QWidget#topbar, QWidget#viewbar {{ background: #ffffff; border-bottom: 1px solid {BORDER.name()}; }}

QPushButton, QToolButton {{
    background: #ffffff; border: 1px solid {BORDER.name()}; border-radius: 6px;
    padding: 5px 10px; min-height: 18px;
}}
QPushButton:hover, QToolButton:hover {{ background: #eef6f4; border-color: #b7d8d0; }}
QPushButton:pressed, QToolButton:pressed {{ background: #dcefea; }}
QPushButton:disabled, QToolButton:disabled {{ color: #a6adb7; background: #fafbfc; border-color: #e6e9ed; }}
QPushButton#primary {{
    background: {ACCENT.name()}; color: #ffffff; border: 1px solid {ACCENT_DARK.name()};
    font-weight: 600; padding: 6px 16px;
}}
QPushButton#primary:hover {{ background: {ACCENT_DARK.name()}; }}
QPushButton#primary:disabled {{ background: #9cc9be; border-color: #9cc9be; color: #eef6f4; }}
QToolButton#icon {{ padding: 4px 8px; font-size: 11pt; }}

QComboBox, QDoubleSpinBox {{
    background: #ffffff; border: 1px solid {BORDER.name()}; border-radius: 6px; padding: 3px 8px;
    min-height: 20px;
}}
QComboBox:hover, QDoubleSpinBox:hover {{ border-color: #b7d8d0; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url("@CHEVRON_DOWN@"); width: 12px; height: 12px; }}
QDoubleSpinBox {{ padding-right: 22px; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent;
}}
QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: #eef6f4; }}
QDoubleSpinBox::up-arrow {{ image: url("@CHEVRON_UP@"); width: 10px; height: 10px; }}
QDoubleSpinBox::down-arrow {{ image: url("@CHEVRON_DOWN@"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ border: 1px solid {BORDER.name()}; selection-background-color: #dcefea; selection-color: {INK.name()}; }}

QListWidget {{ background: #ffffff; border: none; outline: none; padding: 4px; }}
QListWidget::item {{ border-radius: 8px; padding: 6px; margin: 1px 2px; }}
QListWidget::item:hover {{ background: #f1f5f4; }}
QListWidget::item:selected {{ background: #dcefea; color: {INK.name()}; }}

QGraphicsView {{ border: none; background: {CANVAS.name()}; }}
QSplitter::handle {{ background: {BORDER.name()}; }}
QStatusBar {{ background: #ffffff; border-top: 1px solid {BORDER.name()}; color: {MUTED.name()}; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle {{ background: #c3c9d1; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: #a9b0ba; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {INK.name()}; color: #ffffff; border: none; padding: 4px 8px; }}
QProgressDialog QLabel {{ padding: 4px; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, SURFACE)
    pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Text, INK)
    pal.setColor(QPalette.ColorRole.WindowText, INK)
    pal.setColor(QPalette.ColorRole.ButtonText, INK)
    pal.setColor(QPalette.ColorRole.Highlight, ACCENT)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)
    sheet = (STYLESHEET
             .replace("@CHEVRON_UP@", asset_path("chevron-up.png").replace("\\", "/"))
             .replace("@CHEVRON_DOWN@", asset_path("chevron-down.png").replace("\\", "/")))
    app.setStyleSheet(sheet)
