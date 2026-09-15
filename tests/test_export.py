from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image, ImageDraw, ImageOps
from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtPdf import QPdfDocument
from pypdf import PdfReader

from image_to_pdf import imaging, pdf_export
from image_to_pdf.model import (
    FIT_TO_IMAGE,
    MM,
    PAGE_SIZES,
    PX_TO_PT,
    PageSettings,
    natural_landscape,
)

RED = (220, 30, 30)
GREEN = (30, 200, 30)
BLUE = (30, 30, 220)
YELLOW = (230, 220, 30)


# --------------------------------------------------------------------------
# fixtures / helpers


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def quadrant_image(w: int, h: int, colors, mode: str = "RGB") -> Image.Image:
    """colors = (top_left, top_right, bottom_left, bottom_right)."""
    im = Image.new(mode, (w, h), colors[0] if mode != "RGBA" else colors[0])
    draw = ImageDraw.Draw(im)
    hw, hh = w // 2, h // 2
    draw.rectangle([0, 0, hw - 1, hh - 1], fill=colors[0])
    draw.rectangle([hw, 0, w - 1, hh - 1], fill=colors[1])
    draw.rectangle([0, hh, hw - 1, h - 1], fill=colors[2])
    draw.rectangle([hw, hh, w - 1, h - 1], fill=colors[3])
    return im


def render_page(pdf_path, page_index, w, h):
    doc = QPdfDocument()
    status = doc.load(str(pdf_path))
    assert doc.pageCount() > page_index, f"failed to load {pdf_path}: {status}"
    return doc.render(page_index, QSize(w, h))


def sample(img, fx, fy):
    """Sample a pixel, alpha-composited over white.

    QPdfDocument.render() returns a straight-alpha ARGB32 image where
    untouched page background is fully transparent (alpha=0), not opaque
    white -- unlike how a real PDF viewer or printer would show an empty
    page. Composite over white ourselves so "background" reads as white,
    matching what a user actually sees.
    """
    x = min(int(img.width() * fx), img.width() - 1)
    y = min(int(img.height() * fy), img.height() - 1)
    c = img.pixelColor(x, y)
    a = c.alphaF()
    r = c.red() * a + 255 * (1 - a)
    g = c.green() * a + 255 * (1 - a)
    b = c.blue() * a + 255 * (1 - a)
    return (round(r), round(g), round(b))


def assert_close(c1, c2, tol=40, msg=""):
    assert all(abs(a - b) <= tol for a, b in zip(c1, c2)), f"{msg}: {c1} != {c2} (tol={tol})"


def assert_quadrants(img, tl, tr, bl, br, tol=40):
    assert_close(sample(img, 0.25, 0.25), tl, tol, "TL")
    assert_close(sample(img, 0.75, 0.25), tr, tol, "TR")
    assert_close(sample(img, 0.25, 0.75), bl, tol, "BL")
    assert_close(sample(img, 0.75, 0.75), br, tol, "BR")


# --------------------------------------------------------------------------
# a. plain fit-to-image export, no rotation


def test_fit_to_image_quadrants(qapp, tmp_path):
    im = quadrant_image(400, 300, (RED, GREEN, BLUE, YELLOW))
    src = tmp_path / "quad.png"
    im.save(src)

    entry = imaging.probe(str(src))
    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=0.0)
    out = tmp_path / "out.pdf"
    pdf_export.export_pdf([entry], settings, str(out))

    page = render_page(out, 0, 400, 300)
    assert_quadrants(page, RED, GREEN, BLUE, YELLOW)


# --------------------------------------------------------------------------
# b. rotation=90 (clockwise on screen)


def test_rotation_90_quadrants(qapp, tmp_path):
    im = quadrant_image(400, 300, (RED, GREEN, BLUE, YELLOW))
    src = tmp_path / "quad.png"
    im.save(src)

    entry = imaging.probe(str(src))
    entry.placement.rotation = 90.0
    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=0.0)
    out = tmp_path / "out.pdf"
    pdf_export.export_pdf([entry], settings, str(out))

    # Fit-to-image page swaps dimensions for a quarter-turned placement.
    m = 0.0 * MM
    w, h = pdf_export.resolve(entry, settings).page_w, pdf_export.resolve(entry, settings).page_h
    assert w == pytest.approx(300 * PX_TO_PT + 2 * m)
    assert h == pytest.approx(400 * PX_TO_PT + 2 * m)

    page = render_page(out, 0, 300, 400)
    # clockwise 90: what was bottom-left is now top-left, etc.
    assert_quadrants(page, tl=BLUE, tr=RED, bl=YELLOW, br=GREEN)


# --------------------------------------------------------------------------
# c. EXIF orientation handling (the critical one: applied as a PDF transform)


# Inverse of the "display" transform for each EXIF orientation tag, i.e. the
# op to turn the upright display image D into the raw stored pixels R such
# that PIL.ImageOps.exif_transpose(R tagged with that orientation) == D.
_INVERSE_OP = {
    1: None,
    2: Image.FLIP_LEFT_RIGHT,          # self-inverse
    3: Image.ROTATE_180,               # self-inverse
    4: Image.FLIP_TOP_BOTTOM,          # self-inverse
    5: Image.TRANSPOSE,                # self-inverse
    6: Image.ROTATE_90,                # forward is ROTATE_270
    7: Image.TRANSVERSE,               # self-inverse
    8: Image.ROTATE_270,               # forward is ROTATE_90
}


@pytest.mark.parametrize("orientation", list(range(1, 9)))
def test_exif_orientation_applied_as_pdf_transform(qapp, tmp_path, orientation):
    dw, dh = 480, 320  # non-square, upright "display" size
    display = quadrant_image(dw, dh, (RED, GREEN, BLUE, YELLOW))

    op = _INVERSE_OP[orientation]
    raw = display.transpose(op) if op is not None else display.copy()

    src = tmp_path / f"orient{orientation}.jpg"
    exif = Image.Exif()
    exif[imaging.EXIF_ORIENTATION] = orientation
    raw.save(src, format="JPEG", quality=95, exif=exif.tobytes())

    # Sanity: exif_transpose really does reconstruct the display image.
    with Image.open(src) as reopened:
        assert imaging.exif_orientation(reopened) == orientation
        transposed = ImageOps.exif_transpose(reopened).convert("RGB")
    assert_quadrants_pil(transposed, RED, GREEN, BLUE, YELLOW)

    entry = imaging.probe(str(src))
    assert (entry.px_w, entry.px_h) == (dw, dh)

    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=0.0)
    out = tmp_path / "out.pdf"
    pdf_export.export_pdf([entry], settings, str(out))

    page = render_page(out, 0, dw, dh)
    assert_quadrants(page, RED, GREEN, BLUE, YELLOW, tol=45)


def assert_quadrants_pil(im, tl, tr, bl, br, tol=40):
    w, h = im.size

    def px(fx, fy):
        return im.getpixel((min(int(w * fx), w - 1), min(int(h * fy), h - 1)))

    assert_close(px(0.25, 0.25), tl, tol, "TL(pil)")
    assert_close(px(0.75, 0.25), tr, tol, "TR(pil)")
    assert_close(px(0.25, 0.75), bl, tol, "BL(pil)")
    assert_close(px(0.75, 0.75), br, tol, "BR(pil)")


# --------------------------------------------------------------------------
# d. A4, multiple pages, landscape auto-orientation


def test_a4_multi_page_and_landscape_auto(qapp, tmp_path):
    portrait_im = quadrant_image(300, 400, (RED, GREEN, BLUE, YELLOW))
    landscape_im = quadrant_image(400, 300, (RED, GREEN, BLUE, YELLOW))
    square_im = quadrant_image(200, 200, (RED, GREEN, BLUE, YELLOW))

    paths = []
    for name, im in [("p.png", portrait_im), ("l.png", landscape_im), ("s.png", square_im)]:
        p = tmp_path / name
        im.save(p)
        paths.append(p)

    entries = [imaging.probe(str(p)) for p in paths]
    for e in entries:
        e.placement.landscape = natural_landscape(e)

    settings = PageSettings(size="A4", orientation="auto", margin_mm=10.0)
    out = tmp_path / "out.pdf"
    pdf_export.export_pdf(entries, settings, str(out))

    reader = PdfReader(str(out))
    assert len(reader.pages) == 3

    a4_w, a4_h = PAGE_SIZES["A4"]
    portrait_box = reader.pages[0].mediabox
    landscape_box = reader.pages[1].mediabox
    square_box = reader.pages[2].mediabox

    assert float(portrait_box.width) == pytest.approx(a4_w, abs=0.1)
    assert float(portrait_box.height) == pytest.approx(a4_h, abs=0.1)

    assert float(landscape_box.width) == pytest.approx(a4_h, abs=0.1)
    assert float(landscape_box.height) == pytest.approx(a4_w, abs=0.1)

    assert float(square_box.width) == pytest.approx(a4_w, abs=0.1)
    assert float(square_box.height) == pytest.approx(a4_h, abs=0.1)


# --------------------------------------------------------------------------
# e. RGBA with transparency


def test_rgba_transparent_half_exports(qapp, tmp_path):
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 0, 99, 199], fill=(255, 0, 0, 255))  # opaque red left half
    # right half stays fully transparent
    src = tmp_path / "rgba.png"
    im.save(src)

    entry = imaging.probe(str(src))
    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=0.0)
    out = tmp_path / "out.pdf"
    pdf_export.export_pdf([entry], settings, str(out))
    assert out.exists()

    page = render_page(out, 0, 200, 200)
    transparent_area = sample(page, 0.75, 0.5)
    assert_close(transparent_area, (255, 255, 255), tol=25, msg="transparent area should render white-ish")


# --------------------------------------------------------------------------
# f. 16-bit greyscale


def test_16bit_greyscale_exports(qapp, tmp_path):
    im = Image.new("I", (200, 150), 0)
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 0, 99, 149], fill=2000)
    draw.rectangle([100, 0, 199, 149], fill=60000)
    im16 = im.convert("I;16")
    src = tmp_path / "grey16.png"
    im16.save(src)

    entry = imaging.probe(str(src))
    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=0.0)
    out = tmp_path / "out.pdf"
    pdf_export.export_pdf([entry], settings, str(out))
    assert out.exists()

    page = render_page(out, 0, 200, 150)
    samples = [sample(page, fx, 0.5) for fx in (0.1, 0.3, 0.5, 0.7, 0.9)]
    assert not all(c == (255, 255, 255) for c in samples), "export should not be blank white"
    # dark half should be visibly darker than the light half
    dark = sample(page, 0.25, 0.5)
    light = sample(page, 0.75, 0.5)
    assert sum(dark) < sum(light)


# --------------------------------------------------------------------------
# g. cancellation


def test_export_cancelled_leaves_no_output(qapp, tmp_path):
    im = quadrant_image(100, 100, (RED, GREEN, BLUE, YELLOW))
    src = tmp_path / "a.png"
    im.save(src)
    src2 = tmp_path / "b.png"
    im.save(src2)

    entries = [imaging.probe(str(src)), imaging.probe(str(src2))]
    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=0.0)
    out = tmp_path / "out.pdf"

    def progress(done, total):
        return False  # cancel immediately

    with pytest.raises(pdf_export.ExportCancelled):
        pdf_export.export_pdf(entries, settings, str(out), progress=progress)

    assert not out.exists()
    part = tmp_path / "out.pdf.part"
    if part.exists():
        pytest.fail(
            "BUG: export_pdf leaves a stray '<out>.part' file behind on cancellation "
            f"({part}); it is only renamed to the final path in the success path "
            "(os.replace at the end of export_pdf), never cleaned up on ExportCancelled."
        )
