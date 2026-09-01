#!/usr/bin/env python3
"""
Compose the WatchLog homepage hero image from real product screens.

Assembles product-hero-composite.png (transparent RGBA) from the design-target
captures: the Overview as the dominant plane with a smaller Reports card
floating front-right, each with baked rounded corners + a soft drop shadow, so
it drops straight onto the dark hero field. Then run build_site_images.py to
produce the webp variants.

    python tools/build_hero_composite.py
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

IMG = Path(__file__).resolve().parents[1] / "deploy/wordpress/themes/watchlog/img"

def rounded(im: Image.Image, radius: int, border=(255, 255, 255, 40), bw=1) -> Image.Image:
    im = im.convert("RGBA")
    w, h = im.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    if bw:
        ImageDraw.Draw(out).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, outline=border, width=bw)
    return out

def with_shadow(card: Image.Image, blur=48, dy=34, alpha=150, pad=90):
    w, h = card.size
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    a = card.split()[3].point(lambda p: alpha if p > 0 else 0)
    sh.putalpha(a)
    black = Image.new("RGBA", (w, h), (3, 8, 20, 255))
    black.putalpha(a)
    shadow.paste(black, (pad, pad + dy), black)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(card, (pad, pad))
    return canvas

def main():
    overview = Image.open(IMG / "product-platform-overview.png").convert("RGBA")
    reports = Image.open(IMG / "product-reports.png").convert("RGBA")

    # dominant Overview plane
    ow = 1520
    overview = overview.resize((ow, round(overview.height * ow / overview.width)), Image.LANCZOS)
    overview = with_shadow(rounded(overview, 18), blur=55, dy=40, alpha=140)

    # floating Reports card, front-right
    rw = 720
    reports = reports.resize((rw, round(reports.height * rw / reports.width)), Image.LANCZOS)
    reports = with_shadow(rounded(reports, 16), blur=46, dy=30, alpha=165)

    W, H = 1900, 1240
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.alpha_composite(overview, (-40, 10))
    canvas.alpha_composite(reports, (W - reports.width + 30, H - reports.height + 20))

    out = IMG / "product-hero-composite.png"
    canvas.save(out)
    print("wrote", out, canvas.size)

if __name__ == "__main__":
    main()
