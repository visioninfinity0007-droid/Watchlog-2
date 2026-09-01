#!/usr/bin/env python3
"""
Build optimized web images for the WatchLog marketing theme.

For each source image it emits, into deploy/wordpress/themes/watchlog/img/:
  <name>.webp        full-size WebP (primary delivery)
  <name>-sm.webp     mobile WebP (<= SM_W wide) for srcset
  <name>.<jpg|png>   lightly-optimized original-format fallback

Photos are JPEG-source (q82 webp); diagrams/screenshots are PNG-source
(q90 webp to keep text/UI crisp). Run after dropping new assets in --src,
and again after capturing product-*.png screenshots into the theme img dir.

    python tools/build_site_images.py --src "<asset pack dir>"
    python tools/build_site_images.py --src deploy/wordpress/themes/watchlog/img  # re-pack screenshots in place
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "deploy/wordpress/themes/watchlog/img"
SM_W = 960          # mobile srcset width
MAX_W = 2000        # cap absurdly large sources
PHOTO_Q = 82
DIAGRAM_Q = 90

def emit(src: Path):
    name = src.stem
    ext = src.suffix.lower()
    is_png = ext == ".png"
    im = Image.open(src)
    if im.mode in ("P", "LA"):
        im = im.convert("RGBA" if is_png else "RGB")
    elif im.mode == "RGBA" and not is_png:
        im = im.convert("RGB")
    # cap width
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    q = DIAGRAM_Q if is_png else PHOTO_Q
    # primary webp
    im.save(DST / f"{name}.webp", "WEBP", quality=q, method=6)
    # mobile webp
    if im.width > SM_W:
        sm = im.resize((SM_W, round(im.height * SM_W / im.width)), Image.LANCZOS)
    else:
        sm = im
    sm.save(DST / f"{name}-sm.webp", "WEBP", quality=q, method=6)
    # fallback in original format (optimized)
    if is_png:
        im.save(DST / f"{name}.png", "PNG", optimize=True)
        fb = f"{name}.png"
    else:
        im.save(DST / f"{name}.jpg", "JPEG", quality=80, optimize=True, progressive=True)
        fb = f"{name}.jpg"
    return name, im.width, im.height, fb

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--only", default="", help="substring filter")
    args = ap.parse_args()
    src = Path(args.src)
    DST.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src.iterdir()
                   if p.suffix.lower() in (".jpg", ".jpeg", ".png")
                   and args.only in p.name
                   and not p.stem.endswith("-sm")
                   and not p.name.endswith(".webp"))
    if not files:
        print("no source images found in", src); return 1
    print(f"{'name':32} {'wxh':12} fallback")
    for f in files:
        # skip re-processing our own outputs when src == DST
        if f.parent == DST and f.suffix == ".webp":
            continue
        name, w, h, fb = emit(f)
        print(f"{name:32} {f'{w}x{h}':12} {fb}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
