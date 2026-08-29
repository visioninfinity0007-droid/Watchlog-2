#!/usr/bin/env python3
"""
Turn the supplied photography into the exact files the theme expects.

Save the images anywhere under `_drop/` with any filename. This script
identifies each one by its shape and content, splits the two that came
back as multi-panel composites, crops to the aspect the layout needs,
resizes, strips metadata, and writes correctly named JPEGs into
deploy/wordpress/themes/watchlog/img/.

    python tools/prepare_site_images.py            # do it
    python tools/prepare_site_images.py --dry-run  # report only

WHY A SCRIPT AND NOT MANUAL CROPPING
Because it will be done again. The photography will be reshot, replaced
for a second client, or re-exported at a different size, and a folder of
hand-cropped files with no record of how they were made is a dead end.
This is repeatable and reviewable.

WHAT IT HANDLES
  * Two of the supplied images are CONTACT SHEETS - three CCTV frames in
    one picture, and five premises photos in another. They are split on
    the pale gutters between panels rather than at hardcoded pixel
    positions, so a re-export at a different size still works.
  * EXIF is dropped. Photographs can carry GPS coordinates, and this is a
    security product for named client sites.
  * Everything is written as progressive JPEG at quality 82, which is the
    point where these particular images stop improving visibly.

Needs: pillow, numpy
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DROP = ROOT / "_drop"
OUT = ROOT / "deploy" / "wordpress" / "themes" / "watchlog" / "img"

QUALITY = 82

# name -> (width, height). The aspect each slot in the layout expects.
SPEC = {
    "hero-premises":     (2400, 1350),
    "recorder-shelf":    (1600, 1000),
    "unwatched-monitor": (1600, 1200),
    "still-gate":        (1280, 720),
    "still-vehicle":     (1280, 720),
    "still-empty":       (1280, 720),
    "site-pc":           (1600, 1000),
    "recorder-label":    (1400, 1000),
    "close-dusk":        (2400, 900),
    "seg-warehouse":     (1200, 800),
    "seg-retail":        (1200, 800),
    "seg-school":        (1200, 800),
    "seg-factory":       (1200, 800),
    "seg-office":        (1200, 800),
}


# ---------------------------------------------------------------------
# splitting contact sheets
# ---------------------------------------------------------------------

def gutters(arr: np.ndarray, axis: int, tol: int = 14):
    """
    Rows (or columns) that are almost uniform and pale — the gaps a
    contact sheet leaves between panels.

    Detected rather than hardcoded so the same script survives a
    re-export at another size.
    """
    line = arr.mean(axis=1 - axis) if axis == 0 else arr.mean(axis=1 - axis)
    prof = arr.std(axis=1 - axis).mean(axis=-1)
    bright = arr.mean(axis=1 - axis).mean(axis=-1)
    return (prof < tol) & (bright > 200)


def runs(mask: np.ndarray, min_len: int = 4):
    """Contiguous True runs, as (start, end) pairs."""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(mask) - start >= min_len:
        out.append((start, len(mask)))
    return out


def split_panels(img: Image.Image, expect: int):
    """
    Cut a contact sheet into its panels.

    Tries a horizontal split first, then a vertical one inside each
    band, which is the layout both supplied sheets actually use.
    """
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]

    rows = runs(gutters(arr, 0), min_len=max(3, h // 200))
    bands, prev = [], 0
    for a, b in rows:
        if a - prev > h * 0.08:
            bands.append((prev, a))
        prev = b
    if h - prev > h * 0.08:
        bands.append((prev, h))
    if not bands:
        bands = [(0, h)]

    panels = []
    for top, bottom in bands:
        sub = arr[top:bottom]
        cols = runs(gutters(sub, 1), min_len=max(3, w // 200))
        prev, cuts = 0, []
        for a, b in cols:
            if a - prev > w * 0.08:
                cuts.append((prev, a))
            prev = b
        if w - prev > w * 0.08:
            cuts.append((prev, w))
        if not cuts:
            cuts = [(0, w)]
        for left, right in cuts:
            panels.append(img.crop((left, top, right, bottom)))

    if len(panels) != expect:
        print(f"    NOTE: found {len(panels)} panels, expected {expect}")
    return panels


# ---------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------

def fit(img: Image.Image, size: tuple) -> Image.Image:
    """
    Cover-crop to the target aspect, then resize.

    Crops from the centre horizontally but favours the UPPER portion
    vertically (1/3 down rather than 1/2). Skies and ceilings carry the
    subject in this set; centre-cropping a dusk shot throws away the
    sky that makes it work.
    """
    tw, th = size
    w, h = img.size
    target, actual = tw / th, w / h

    if actual > target:                       # too wide: trim sides
        nw = int(round(h * target))
        left = (w - nw) // 2
        img = img.crop((left, 0, left + nw, h))
    elif actual < target:                     # too tall: trim top/bottom
        nh = int(round(w / target))
        top = int((h - nh) * 0.33)
        img = img.crop((0, top, w, top + nh))

    return img.convert("RGB").resize(size, Image.LANCZOS)


def save(img: Image.Image, name: str, dry: bool) -> None:
    path = OUT / f"{name}.jpg"
    if dry:
        print(f"    would write {path.relative_to(ROOT)}  {SPEC[name][0]}x{SPEC[name][1]}")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    out = fit(img, SPEC[name])
    # save() on a fresh image drops EXIF, which can carry GPS.
    out.save(path, "JPEG", quality=QUALITY, optimize=True, progressive=True)
    kb = path.stat().st_size // 1024
    print(f"    {path.name:<22} {SPEC[name][0]}x{SPEC[name][1]}  {kb} KB")


# ---------------------------------------------------------------------
# identifying which supplied image is which
# ---------------------------------------------------------------------

def classify(img: Image.Image) -> str:
    """
    Work out what a supplied image is from its shape and tone.

    Deliberately crude and printed for review — it is a starting guess,
    not a decision. Anything it cannot place is reported rather than
    silently dropped.
    """
    w, h = img.size
    ar = w / h
    a = np.asarray(img.convert("RGB").resize((64, 64)), dtype=np.float32)
    mean = a.mean()
    dark = mean < 70

    if ar > 2.4:
        return "close-dusk"
    if 1.6 < ar < 1.85 and dark:
        return "sheet-stills"        # the 3-panel CCTV contact sheet
    if 1.4 < ar < 1.6 and not dark:
        return "sheet-segments"      # the 5-panel premises sheet
    if 1.7 < ar < 1.85 and not dark:
        return "hero-premises"
    if 1.2 < ar < 1.45 and mean < 120:
        return "unwatched-monitor"
    if 1.5 < ar < 1.7:
        return "recorder-shelf"
    return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DROP.exists():
        DROP.mkdir(parents=True, exist_ok=True)
        print(f"Created {DROP.relative_to(ROOT)}. Put the images in it and "
              f"run this again.")
        return 1

    files = sorted(p for p in DROP.iterdir()
                   if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    if not files:
        print(f"No images in {DROP.relative_to(ROOT)}.")
        return 1

    print(f"Found {len(files)} image(s) in {DROP.name}/\n")
    for p in files:
        img = Image.open(p)
        kind = classify(img)
        print(f"  {p.name}  {img.width}x{img.height}  ->  {kind}")

        if kind == "sheet-stills":
            panels = split_panels(img, 3)
            for name, panel in zip(("still-gate", "still-vehicle", "still-empty"),
                                   panels):
                save(panel, name, args.dry_run)
        elif kind == "sheet-segments":
            panels = split_panels(img, 5)
            names = ("seg-warehouse", "seg-retail", "seg-school",
                     "seg-factory", "seg-office")
            for name, panel in zip(names, panels):
                save(panel, name, args.dry_run)
        elif kind in SPEC:
            save(img, kind, args.dry_run)
        else:
            print("    UNRECOGNISED — rename it to one of: "
                  + ", ".join(sorted(SPEC)))
        print()

    if not args.dry_run:
        have = sorted(p.stem for p in OUT.glob("*.jpg"))
        missing = sorted(set(SPEC) - set(have))
        print(f"  {len(have)}/{len(SPEC)} assets in place")
        if missing:
            print(f"  still missing: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
