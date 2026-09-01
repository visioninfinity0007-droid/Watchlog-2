#!/usr/bin/env python3
"""
Build the WatchLog social share card (og-card.png, 1200x630).

New navy/blue brand territory, precise positioning, NO anti-guard messaging
(the old card said "No monthly guard." — removed per the revamp brief).

    python tools/build_og_card.py
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

IMG = Path(__file__).resolve().parents[1] / "deploy/wordpress/themes/watchlog/img"
W, H = 1200, 630

def font(names, size):
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()

# Windows system fonts (clean geometric-ish sans); fall back to DejaVu.
BOLD = ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf"]
SEMI = ["C:/Windows/Fonts/segoeuisb.ttf", "C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf"]
REG  = ["C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf"]

def lerp(a, b, t): return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

def main():
    # dark navy field, radial glow toward top-right (matches --field-dark)
    ink, navy, glow = (7, 17, 31), (11, 29, 58), (18, 41, 79)
    base = Image.new("RGB", (W, H), ink)
    px = base.load()
    cx, cy, R = W * 0.82, 0, W * 1.05
    for y in range(H):
        for x in range(0, W, 2):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / R
            d = min(max(d, 0), 1)
            c = lerp(glow, ink, d ** 0.9) if d < 0.55 else lerp(lerp(glow, ink, .55 ** .9), ink, (d - .55) / .45)
            px[x, y] = c
            if x + 1 < W: px[x + 1, y] = c
    d = ImageDraw.Draw(base)

    # mark badge (the app icon: violet rounded square W)
    try:
        badge = Image.open(IMG / "icon-512.png").convert("RGBA").resize((104, 104), Image.LANCZOS)
        base.paste(badge, (96, 96), badge)
        tx = 96 + 104 + 26
    except Exception:
        tx = 96
    d.text((tx, 120), "WatchLog", font=font(BOLD, 62), fill=(255, 255, 255))

    # eyebrow
    d.text((100, 250), "CCTV INTELLIGENCE FOR BUSINESSES", font=font(SEMI, 24), fill=(114, 212, 255))
    # headline (two lines)
    hf = font(BOLD, 60)
    d.text((100, 300), "Make your existing", font=hf, fill=(255, 255, 255))
    d.text((100, 370), "cameras useful every day.", font=hf, fill=(255, 255, 255))
    # subline
    sf = font(REG, 27)
    d.text((100, 470), "Validated incidents, camera-health visibility and a daily report", font=sf, fill=(180, 196, 220))
    d.text((100, 506), "— without exposing your recorder to the internet.", font=sf, fill=(180, 196, 220))

    # thin accent underline
    d.rectangle([100, 250 + 34, 100 + 300, 250 + 36], fill=(23, 72, 211))

    out = IMG / "og-card.png"
    base.save(out, "PNG")
    print("wrote", out, base.size)

if __name__ == "__main__":
    main()
