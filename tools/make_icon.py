"""Draw the icons: the app icon (.ico/.png), one tile per tool, and UI chevrons.

Everything is drawn large with Pillow and downsampled, so edges stay smooth.
"""

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # drawn large, downsampled per size
OUT = Path(__file__).resolve().parent.parent / "to_pdf" / "assets"

TEAL = ((24, 150, 124), (10, 100, 82))
SLATE = ((76, 96, 190), (44, 58, 138))


def tile(colors) -> Image.Image:
    """Rounded square with a vertical gradient."""
    top, bottom = colors
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=255)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    im.paste(grad, (0, 0), mask)
    return im


def page(size, fold_fill) -> Image.Image:
    w, h = size
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fold = int(w * 0.24)
    d.polygon([(0, 0), (w - fold, 0), (w, fold), (w, h), (0, h)], fill=(255, 255, 255, 255))
    d.polygon([(w - fold, 0), (w - fold, fold), (w, fold)], fill=fold_fill)
    return im


def place_pages(im: Image.Image, front: Image.Image, back: bool, shadow_rgb) -> None:
    pw, ph = front.size
    if back:
        b = page((pw, ph), (0, 0, 0, 0)).rotate(10, resample=Image.Resampling.BICUBIC, expand=True)
        b.putalpha(b.getchannel("A").point(lambda a: a * 0.75))
        im.alpha_composite(b, (S // 2 - b.width // 2 + 80, S // 2 - b.height // 2 - 40))
    shadow = Image.new("RGBA", (pw, ph), shadow_rgb + (0,))
    shadow.putalpha(front.getchannel("A").point(lambda a: a * 110 // 255))
    x, y = S // 2 - pw // 2 - (40 if back else 0), S // 2 - ph // 2 + (50 if back else 10)
    im.alpha_composite(shadow, (x + 14, y + 18))
    im.alpha_composite(front, (x, y))


def images_icon() -> Image.Image:
    im = tile(TEAL)
    pw, ph = 470, 600
    front = page((pw, ph), (196, 226, 219, 255))
    d = ImageDraw.Draw(front)
    teal = (14, 124, 102, 255)
    x0, y0, x1, y1 = int(pw * 0.16), int(ph * 0.40), int(pw * 0.84), int(ph * 0.84)
    d.rounded_rectangle((x0, y0, x1, y1), radius=int(pw * 0.05), fill=(224, 241, 237, 255))
    d.polygon([(x0, y1), (x0 + (x1 - x0) * 0.38, y0 + (y1 - y0) * 0.30),
               (x0 + (x1 - x0) * 0.62, y0 + (y1 - y0) * 0.66),
               (x0 + (x1 - x0) * 0.76, y0 + (y1 - y0) * 0.50), (x1, y1)], fill=teal)
    r = int(pw * 0.075)
    cx, cy = int(x0 + (x1 - x0) * 0.76), int(y0 + (y1 - y0) * 0.24)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(245, 176, 65, 255))
    place_pages(im, front, back=True, shadow_rgb=(6, 60, 50))
    return im


def markdown_icon() -> Image.Image:
    im = tile(SLATE)
    pw, ph = 470, 600
    front = page((pw, ph), (205, 212, 240, 255))
    d = ImageDraw.Draw(front)
    slate = (52, 68, 150, 255)
    # a few "text lines" at the top
    for i, frac in enumerate((0.62, 0.78, 0.5)):
        y = int(ph * 0.17) + i * 46
        d.rounded_rectangle((int(pw * 0.14), y, int(pw * 0.14 + (pw * 0.62) * frac), y + 20),
                            radius=10, fill=(214, 220, 240, 255))
    # the Markdown mark: an "M" and a down arrow inside a rounded frame
    fx0, fy0, fx1, fy1 = int(pw * 0.12), int(ph * 0.48), int(pw * 0.88), int(ph * 0.84)
    d.rounded_rectangle((fx0, fy0, fx1, fy1), radius=34, outline=slate, width=22)
    mx0, mx1 = fx0 + 52, fx0 + 210
    my0, my1 = fy0 + 58, fy1 - 58
    d.line([(mx0, my1), (mx0, my0), ((mx0 + mx1) // 2, my0 + 70), (mx1, my0), (mx1, my1)],
           fill=slate, width=30, joint="curve")
    ax = fx1 - 88
    d.line([(ax, my0), (ax, my1 - 40)], fill=slate, width=30)
    d.polygon([(ax - 50, my1 - 62), (ax + 50, my1 - 62), (ax, my1 + 4)], fill=slate)
    place_pages(im, front, back=True, shadow_rgb=(20, 28, 80))
    return im


def app_icon() -> Image.Image:
    """Generic 'anything to PDF': a page with an arrow and a PDF band."""
    im = tile(TEAL)
    pw, ph = 500, 640
    front = page((pw, ph), (196, 226, 219, 255))
    d = ImageDraw.Draw(front)
    teal = (14, 124, 102, 255)
    # arrow pointing down into the page
    cx = pw // 2
    d.line([(cx, int(ph * 0.14)), (cx, int(ph * 0.46))], fill=teal, width=46)
    d.polygon([(cx - 92, int(ph * 0.40)), (cx + 92, int(ph * 0.40)), (cx, int(ph * 0.57))], fill=teal)
    # PDF band
    d.rounded_rectangle((int(pw * 0.12), int(ph * 0.66), int(pw * 0.88), int(ph * 0.88)),
                        radius=26, fill=(214, 64, 52, 255))
    # "PDF" lettering drawn as strokes so no font file is needed
    white = (255, 255, 255, 255)
    by0, by1 = int(ph * 0.71), int(ph * 0.83)
    mid = (by0 + by1) // 2
    sw = 20
    x = int(pw * 0.24)
    # P
    d.line([(x, by1), (x, by0)], fill=white, width=sw)
    d.arc((x - 40, by0, x + 50, mid + 4), start=270, end=90, fill=white, width=sw)
    # D
    x += 120
    d.line([(x, by1), (x, by0)], fill=white, width=sw)
    d.arc((x - 64, by0, x + 58, by1), start=270, end=90, fill=white, width=sw)
    # F
    x += 116
    d.line([(x, by1), (x, by0)], fill=white, width=sw)
    d.line([(x, by0 + sw // 2), (x + 64, by0 + sw // 2)], fill=white, width=sw)
    d.line([(x, mid), (x + 50, mid)], fill=white, width=sw)
    place_pages(im, front, back=False, shadow_rgb=(6, 60, 50))
    return im


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    icon = app_icon()
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(OUT / "icon.ico", sizes=sizes)
    icon.resize((512, 512), Image.Resampling.LANCZOS).save(OUT / "icon.png")
    images_icon().resize((256, 256), Image.Resampling.LANCZOS).save(OUT / "tool-images.png")
    markdown_icon().resize((256, 256), Image.Resampling.LANCZOS).save(OUT / "tool-markdown.png")

    # chevrons for spin box / combo box arrows (drawn 4x, stored at 2x for HiDPI)
    for name, pts in (("chevron-up.png", [(8, 40), (32, 16), (56, 40)]),
                      ("chevron-down.png", [(8, 24), (32, 48), (56, 24)])):
        ch = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(ch).line(pts, fill=(80, 88, 100, 255), width=7, joint="curve")
        ch.resize((24, 24), Image.Resampling.LANCZOS).save(OUT / name)
    check = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(check).line([(14, 33), (27, 46), (51, 20)], fill=(255, 255, 255, 255), width=8, joint="curve")
    check.resize((24, 24), Image.Resampling.LANCZOS).save(OUT / "check.png")
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
