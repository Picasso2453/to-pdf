"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from .resources import asset_path


def export_cli(args: list[str]) -> int:
    """image-to-pdf --export OUT.pdf [--size A4] [--margin 10] IMAGE_OR_FOLDER...

    Headless: default layout (each image fitted on its own page)."""
    import argparse

    from .imaging import expand_paths, is_supported, probe
    from .model import PAGE_SIZE_CHOICES, Document, PageSettings
    from .pdf_export import export_pdf

    ap = argparse.ArgumentParser(prog="image-to-pdf")
    ap.add_argument("--export", required=True, metavar="OUT.pdf")
    ap.add_argument("--size", default="A4", choices=PAGE_SIZE_CHOICES)
    ap.add_argument("--orientation", default="auto", choices=["auto", "portrait", "landscape"])
    ap.add_argument("--margin", type=float, default=10.0, help="millimetres")
    ap.add_argument("inputs", nargs="+")
    ns = ap.parse_args(args)
    doc = Document()
    doc.settings = PageSettings(ns.size, ns.orientation, ns.margin)
    doc.add(probe(p) for p in expand_paths(ns.inputs) if is_supported(p))
    if not doc.entries:
        return 2
    export_pdf(doc.entries, doc.settings, ns.export)
    return 0


def main() -> int:
    if "--export" in sys.argv[1:]:
        return export_cli(sys.argv[1:])
    if sys.platform == "win32":
        # Own taskbar group and icon instead of python.exe's.
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Picazzo.ImageToPDF")
        except Exception:
            pass

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from . import __version__
    from .ui import theme
    from .ui.main_window import APP_NAME, MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("image-to-pdf")
    icon_path = asset_path("icon.ico")
    if Path(icon_path).exists():
        app.setWindowIcon(QIcon(icon_path))
    theme.apply(app)

    window = MainWindow([a for a in sys.argv[1:] if not a.startswith("-")])
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
