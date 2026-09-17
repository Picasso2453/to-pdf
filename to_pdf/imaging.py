"""Image probing and decoding (Pillow). No Qt widgets; QImage only."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageOps

from .model import ImageEntry, natural_key

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp",
}
EXIF_ORIENTATION = 0x0112

Image.MAX_IMAGE_PIXELS = 400_000_000  # allow big scans, still guard against bombs


def is_supported(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def expand_paths(paths: list[str]) -> list[str]:
    """Files as given, folders expanded to their images (natural order)."""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            found = [os.path.join(root, f)
                     for root, _dirs, files in os.walk(p) for f in files if is_supported(f)]
            out += sorted(found, key=natural_key)
        elif os.path.isfile(p):
            out.append(p)
    return out


def exif_orientation(im: Image.Image) -> int:
    try:
        return int(im.getexif().get(EXIF_ORIENTATION, 1)) or 1
    except Exception:
        return 1


def probe(path: str) -> ImageEntry:
    """Read only the header: displayed size, honouring EXIF orientation."""
    with Image.open(path) as im:
        w, h = im.size
        if exif_orientation(im) in (5, 6, 7, 8):
            w, h = h, w
    if w <= 0 or h <= 0:
        raise ValueError("image has no pixels")
    return ImageEntry(path=os.path.abspath(path), px_w=w, px_h=h)


def to_rgb_or_rgba(im: Image.Image) -> Image.Image:
    """Convert any Pillow mode to 8-bit RGB, or RGBA when it has transparency."""
    if im.mode in ("I;16", "I;16L", "I;16B", "I;16N", "I", "F"):
        # High bit depth greyscale: scale into 0..255 instead of clipping.
        lo, hi = im.getextrema()
        span = (hi - lo) or 1
        im = im.convert("F").point(lambda v: (v - lo) * 255.0 / span).convert("L")
    has_alpha = im.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
        im.mode == "P" and "transparency" in im.info)
    return im.convert("RGBA" if has_alpha else "RGB")


def load_display_image(path: str, max_px: int) -> Image.Image:
    """Decoded, upright, RGBA image no larger than max_px on its long side."""
    with Image.open(path) as im:
        if im.format == "JPEG":
            im.draft("RGB", (max_px, max_px))  # fast DCT-domain downscale
        im = ImageOps.exif_transpose(im)
        im = to_rgb_or_rgba(im)
        im.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        return im.convert("RGBA")


def to_qimage(im: Image.Image):
    from PySide6.QtGui import QImage

    data = im.tobytes("raw", "RGBA")
    qimg = QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888)
    return qimg.copy()  # detach from the Python buffer
