"""Draw the app icon (stacked pages with a picture) and save it as a multi-size .ico."""

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # drawn large, downsampled per size
OUT = Path(__file__).resolve().parent.parent / "image_to_pdf" / "assets"


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


def page(size, glyph: bool) -> Image.Image:
    w, h = size
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fold = int(w * 0.24)
    d.polygon([(0, 0), (w - fold, 0), (w, fold), (w, h), (0, h)], fill=(255, 255, 255, 255))
    d.polygon([(w - fold, 0), (w - fold, fold), (w, fold)], fill=(196, 226, 219, 255))
    if glyph:
        teal = (14, 124, 102, 255)
        x0, y0, x1, y1 = int(w * 0.16), int(h * 0.40), int(w * 0.84), int(h * 0.84)
        d.rounded_rectangle((x0, y0, x1, y1), radius=int(w * 0.05), fill=(224, 241, 237, 255))
        d.polygon([(x0, y1), (x0 + (x1 - x0) * 0.38, y0 + (y1 - y0) * 0.30),
                   (x0 + (x1 - x0) * 0.62, y0 + (y1 - y0) * 0.66),
                   (x0 + (x1 - x0) * 0.76, y0 + (y1 - y0) * 0.50), (x1, y1)], fill=teal)
        r = int(w * 0.075)
        cx, cy = int(x0 + (x1 - x0) * 0.76), int(y0 + (y1 - y0) * 0.24)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(245, 176, 65, 255))
    return im


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # vertical teal gradient inside a rounded square
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bottom = (24, 150, 124), (10, 100, 82)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (S, S), 0)
    rounded(ImageDraw.Draw(mask), (40, 40, S - 40, S - 40), 210, 255)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    im.paste(grad, (0, 0), mask)

    pw, ph = 470, 600
    back = page((pw, ph), glyph=False).rotate(10, resample=Image.Resampling.BICUBIC, expand=True)
    back.putalpha(back.getchannel("A").point(lambda a: a * 0.75))
    im.alpha_composite(back, (S // 2 - back.width // 2 + 80, S // 2 - back.height // 2 - 40))
    front = page((pw, ph), glyph=True)
    shadow = Image.new("RGBA", (pw, ph), (6, 60, 50, 0))
    shadow.putalpha(front.getchannel("A").point(lambda a: a * 110 // 255))
    im.alpha_composite(shadow, (S // 2 - pw // 2 - 40 + 14, S // 2 - ph // 2 + 50 + 18))
    im.alpha_composite(front, (S // 2 - pw // 2 - 40, S // 2 - ph // 2 + 50))

    # chevrons for spin box / combo box arrows (drawn 4x, stored at 2x for HiDPI)
    for name, pts in (("chevron-up.png", [(8, 40), (32, 16), (56, 40)]),
                      ("chevron-down.png", [(8, 24), (32, 48), (56, 24)])):
        ch = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(ch).line(pts, fill=(80, 88, 100, 255), width=7, joint="curve")
        ch.resize((24, 24), Image.Resampling.LANCZOS).save(OUT / name)

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    im.resize((256, 256), Image.Resampling.LANCZOS).save(OUT / "icon.ico", sizes=sizes)
    im.resize((512, 512), Image.Resampling.LANCZOS).save(OUT / "icon.png")
    print(f"wrote {OUT / 'icon.ico'}")


if __name__ == "__main__":
    main()
