"""Write the document to PDF with reportlab.

Each page is laid out with model.resolve(), the same function the preview
uses, so the PDF matches what is on screen.

JPEG files are embedded byte-for-byte (no recompression). Their EXIF
orientation is applied as a PDF transform rather than by rotating pixels, so
phone photos stay lossless. Everything else is decoded by Pillow and stored
losslessly (Flate), with transparency preserved.
"""

from __future__ import annotations

import io
import os
from typing import Callable

from PIL import Image
from reportlab import rl_config
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from . import __version__
from .imaging import exif_orientation, to_rgb_or_rgba
from .model import ImageEntry, PageSettings, resolve

# Visual transform turning the stored pixels into the upright picture, in
# PDF (y-up) coordinates around the image centre: (a, b, c, d) as used by
# canvas.transform(a, b, c, d, 0, 0), i.e. x' = a*x + c*y, y' = b*x + d*y.
# Orientations 5-8 also swap width and height.
_ORIENT = {
    1: (1, 0, 0, 1),
    2: (-1, 0, 0, 1),   # mirrored horizontally
    3: (-1, 0, 0, -1),  # rotated 180
    4: (1, 0, 0, -1),   # mirrored vertically
    5: (0, -1, -1, 0),  # transposed
    6: (0, -1, 1, 0),   # needs 90 clockwise
    7: (0, 1, 1, 0),    # transversed
    8: (0, 1, -1, 0),   # needs 90 counter-clockwise
}

ProgressFn = Callable[[int, int], bool]  # (done, total) -> keep going?


class ExportCancelled(Exception):
    pass


def _image_source(path: str) -> tuple[object, int]:
    """(reportlab image source, EXIF orientation still to apply)."""
    with Image.open(path) as im:
        orientation = exif_orientation(im)
        if im.format == "JPEG" and im.mode in ("RGB", "L"):
            with open(path, "rb") as fh:
                return ImageReader(io.BytesIO(fh.read())), orientation
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        converted = to_rgb_or_rgba(im)
    return ImageReader(converted), orientation


def export_pdf(entries: list[ImageEntry], settings: PageSettings, out_path: str,
               progress: ProgressFn | None = None, title: str | None = None) -> None:
    if not entries:
        raise ValueError("nothing to export")
    rl_config.useA85 = 0  # binary streams; ASCII85 would inflate images by 25%
    tmp_path = out_path + ".part"
    c = canvas.Canvas(tmp_path, pageCompression=1)
    c.setCreator(f"Image to PDF {__version__}")
    c.setProducer(f"Image to PDF {__version__}")
    if title:
        c.setTitle(title)

    total = len(entries)
    for i, entry in enumerate(entries):
        g = resolve(entry, settings)
        c.setPageSize((g.page_w, g.page_h))
        c.saveState()
        c.translate(g.cx, g.page_h - g.cy)
        c.rotate(-g.rotation)  # preview rotates clockwise in y-down space
        source, orientation = _image_source(entry.path)
        a, b, cc, d = _ORIENT.get(orientation, _ORIENT[1])
        c.transform(a, b, cc, d, 0, 0)
        # Stored-pixel box: orientations 5-8 swap the displayed dimensions.
        w, h = (g.h, g.w) if orientation in (5, 6, 7, 8) else (g.w, g.h)
        c.drawImage(source, -w / 2, -h / 2, w, h, mask="auto")
        c.restoreState()
        c.showPage()
        if progress and not progress(i + 1, total):
            raise ExportCancelled()
    c.save()
    os.replace(tmp_path, out_path)
