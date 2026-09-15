import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# One QApplication (not QGuiApplication) for the whole session: a
# QGuiApplication created by one test module would crash widget tests later.
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])
_APP.setOrganizationName("image-to-pdf-tests")  # keep real settings untouched
