"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from .resources import asset_path


def export_cli(args: list[str]) -> int:
    """Headless export.

    "To PDF.exe" --export OUT.pdf [--size A4] [--orientation auto] [--margin MM] IMAGES/FOLDERS...
    "To PDF.exe" --export OUT.pdf [--size A4] [--orientation portrait] [--margin MM] [--font-pt 11] FILE.md

    Images: each image fitted on its own page. Markdown: one .md file."""
    import argparse
    import os

    from .imaging import expand_paths, is_supported, probe
    from .model import PAGE_SIZE_CHOICES, PAGE_SIZES, Document, PageSettings
    from .pdf_export import export_pdf

    ap = argparse.ArgumentParser(prog="To PDF")
    ap.add_argument("--export", required=True, metavar="OUT.pdf")
    ap.add_argument("--size", default="A4", choices=PAGE_SIZE_CHOICES)
    ap.add_argument("--orientation", default=None, choices=["auto", "portrait", "landscape"])
    ap.add_argument("--margin", type=float, default=None, help="millimetres")
    ap.add_argument("--font-pt", type=float, default=11.0, help="Markdown body text size")
    ap.add_argument("--no-page-numbers", action="store_true", help="Markdown: omit page numbers")
    ap.add_argument("inputs", nargs="+")
    ns = ap.parse_args(args)

    markdown = [p for p in ns.inputs if os.path.splitext(p)[1].lower() in (".md", ".markdown", ".mdown", ".mkd")]
    if markdown:
        from PySide6.QtWidgets import QApplication  # the text layout engine needs an application

        from .markdown_render import MarkdownSettings, export_markdown_pdf

        _app = QApplication.instance() or QApplication(sys.argv[:1])
        if ns.size not in PAGE_SIZES:
            ap.error("Markdown needs a paper size, not 'Fit to image'")
        settings = MarkdownSettings(size=ns.size,
                                    orientation="landscape" if ns.orientation == "landscape" else "portrait",
                                    margin_mm=20.0 if ns.margin is None else ns.margin,
                                    font_pt=ns.font_pt, page_numbers=not ns.no_page_numbers)
        src = markdown[0]
        with open(src, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
        export_markdown_pdf(text, settings, ns.export, os.path.dirname(os.path.abspath(src)),
                            title=Path(src).stem)
        return 0

    doc = Document()
    doc.settings = PageSettings(ns.size, ns.orientation or "auto", 10.0 if ns.margin is None else ns.margin)
    doc.add(probe(p) for p in expand_paths(ns.inputs) if is_supported(p))
    if not doc.entries:
        return 2
    export_pdf(doc.entries, doc.settings, ns.export)
    return 0


def _use_fast_fonts() -> None:
    """Qt's default DirectWrite font database enumerates every installed font
    on first use, which cost 5-8 s of startup on a machine with ~300 fonts.
    The GDI engine starts in milliseconds and renders Segoe UI the same."""
    import os
    if sys.platform == "win32" and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "windows:fontengine=gdi"


def main() -> int:
    _use_fast_fonts()
    if "--export" in sys.argv[1:]:
        return export_cli(sys.argv[1:])
    if sys.platform == "win32":
        # Own taskbar group and icon instead of python.exe's.
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Picazzo.ToPDF")
        except Exception:
            pass

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from . import __version__
    from .ui import theme
    from .ui.common import APP_NAME
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("to-pdf")
    icon_path = asset_path("icon.ico")
    if Path(icon_path).exists():
        app.setWindowIcon(QIcon(icon_path))
    theme.apply(app)

    window = MainWindow([a for a in sys.argv[1:] if not a.startswith("-")])
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
