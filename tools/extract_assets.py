#!/usr/bin/env python3
"""
Extract WatchLog brand assets from the identity sheets.

Why this exists: regenerating the logo produces a different mark every
time. The sheets already contain the correct one, so the reliable move is
to lift it out of the image rather than ask for it again.

What it does:

  isolate   find every pixel matching a brand colour, within a tolerance
  crop      take the largest connected blob of that colour, plus padding
  cutout    write a PNG with a transparent background
  trace     follow the blob's outline and emit a real SVG path, so the
            mark becomes resolution-independent rather than a crop

Tracing matters. A cropped PNG of a 400px mark is useless on a business
card or a large-format print; a traced path is not.

    python tools/extract_assets.py --list          what is in a sheet
    python tools/extract_assets.py --all           run the manifest
    python tools/extract_assets.py --source X.png --colour "#5B21FF" \
           --out brand-assets/logo/monogram-violet --trace

Needs: pillow, numpy, scikit-image
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from skimage import measure

ROOT = Path(__file__).resolve().parents[1]
SHEETS = ROOT / "brand-assets" / "source-sheets"

VIOLET = "#5B21FF"
NEAR_BLACK = "#0B0B0F"


def hex_rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def mask_for(img: Image.Image, colour: str, tol: int = 60) -> np.ndarray:
    """
    Pixels close to `colour`.

    Distance in plain RGB rather than a perceptual space on purpose: the
    sheets are flat vector renders, so the target colour is nearly exact
    and a generous tolerance only picks up antialiased edges - which is
    what we want included.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    dist = np.sqrt(((rgb - hex_rgb(colour)) ** 2).sum(axis=2))
    return dist < tol


def blobs(mask: np.ndarray, min_pixels: int = 400):
    """Connected regions of the mask, largest first."""
    labels = measure.label(mask, connectivity=2)
    out = []
    for r in measure.regionprops(labels):
        if r.area >= min_pixels:
            out.append(r)
    return sorted(out, key=lambda r: r.area, reverse=True)


def merge_boxes(regions, gap: int = 40):
    """
    Merge regions that sit close together.

    The monogram is two separate strokes plus an overlap facet - three
    disconnected blobs. Taking only the largest would return half a
    letter, so anything within `gap` pixels is treated as one mark.
    """
    boxes = [list(r.bbox) for r in regions]      # (minr, minc, maxr, maxc)
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                near = not (a[2] + gap < b[0] or b[2] + gap < a[0] or
                            a[3] + gap < b[1] or b[3] + gap < a[1])
                if near:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]),
                                max(a[2], b[2]), max(a[3], b[3])]
                    boxes.pop(j)
                    merged = True
                    break
            if merged:
                break
    return sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)


def cutout(img: Image.Image, box, colours, tol: int = 60, pad: int = 12,
           square: bool = True) -> Image.Image:
    """
    Crop to `box` and make everything that is not one of `colours`
    transparent. Antialiased edge pixels keep partial alpha so the result
    does not look jagged.
    """
    minr, minc, maxr, maxc = box
    h, w = np.asarray(img).shape[:2]
    minr, minc = max(0, minr - pad), max(0, minc - pad)
    maxr, maxc = min(h, maxr + pad), min(w, maxc + pad)

    if square:
        bh, bw = maxr - minr, maxc - minc
        if bh > bw:
            grow = (bh - bw) // 2
            minc, maxc = max(0, minc - grow), min(w, maxc + grow)
        else:
            grow = (bw - bh) // 2
            minr, maxr = max(0, minr - grow), min(h, maxr + grow)

    crop = img.convert("RGB").crop((minc, minr, maxc, maxr))
    rgb = np.asarray(crop, dtype=np.float32)

    # Alpha = how close each pixel is to the NEAREST wanted colour.
    best = np.full(rgb.shape[:2], 1e9)
    for c in colours:
        d = np.sqrt(((rgb - hex_rgb(c)) ** 2).sum(axis=2))
        best = np.minimum(best, d)
    alpha = np.clip(255 - (best / tol) * 255, 0, 255).astype(np.uint8)

    out = np.dstack([np.asarray(crop, dtype=np.uint8), alpha])
    return Image.fromarray(out, "RGBA")


def simplify(points, tolerance: float = 0.8):
    """
    Douglas-Peucker. Raw contours carry roughly one point per pixel, which
    makes an unusably large SVG.

    Iterative rather than recursive: a contour around a large shape can be
    thousands of points, and the recursive form hits Python's stack limit
    on exactly the marks we care about.

    The perpendicular distance is computed by hand because numpy 2.x
    removed the 2-D cross product that the textbook version uses.
    """
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n < 3:
        return pts

    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        start, end = pts[first], pts[last]
        seg = end - start
        norm = float(np.hypot(seg[0], seg[1]))
        span = pts[first + 1:last]
        if norm == 0:
            dist = np.hypot(*(span - start).T)
        else:
            rel = span - start
            # 2-D cross product, written out: ax*by - ay*bx
            dist = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm
        if len(dist) == 0:
            continue
        i = int(np.argmax(dist))
        if dist[i] > tolerance:
            split = first + 1 + i
            keep[split] = True
            stack.append((first, split))
            stack.append((split, last))

    return pts[keep]


def trace(mask: np.ndarray, box, colour: str, tolerance: float = 0.8) -> str:
    """Outline the mask inside `box` and emit an SVG of real paths."""
    minr, minc, maxr, maxc = box
    sub = mask[minr:maxr, minc:maxc]
    pad = np.pad(sub, 1, constant_values=False)

    paths = []
    for contour in measure.find_contours(pad.astype(float), 0.5):
        pts = simplify(contour, tolerance)
        if len(pts) < 3:
            continue
        # find_contours returns (row, col); SVG wants (x, y).
        d = "M " + " L ".join(f"{p[1] - 1:.2f} {p[0] - 1:.2f}" for p in pts) + " Z"
        paths.append(f'<path d="{d}" fill="{colour}"/>')

    w, h = maxc - minc, maxr - minr
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" fill-rule="evenodd">\n  '
            + "\n  ".join(paths) + "\n</svg>\n")


def describe(path: Path) -> None:
    """What colours and shapes are in this sheet?"""
    img = Image.open(path)
    print(f"\n  {path.name}  {img.width}x{img.height}")
    for name, colour in (("violet", VIOLET), ("near-black", NEAR_BLACK)):
        m = mask_for(img, colour)
        regions = blobs(m)
        merged = merge_boxes(regions)
        print(f"    {name:11} {m.sum():>8} px, {len(regions)} blobs -> "
              f"{len(merged)} shapes")
        for i, b in enumerate(merged[:6]):
            print(f"      shape {i}: {b[3]-b[1]:>4} x {b[2]-b[0]:>4} px "
                  f"at ({b[1]},{b[0]})")


def extract(source: Path, colours, out_stem: Path, index: int = 0,
            tol: int = 60, do_trace: bool = False, square: bool = True,
            size: int | None = None) -> None:
    img = Image.open(source)
    m = mask_for(img, colours[0], tol)
    merged = merge_boxes(blobs(m))
    if not merged:
        print(f"  no {colours[0]} shape found in {source.name}")
        return
    if index >= len(merged):
        print(f"  {source.name}: only {len(merged)} shapes, wanted #{index}")
        return
    box = merged[index]

    out_stem.parent.mkdir(parents=True, exist_ok=True)

    png = cutout(img, box, colours, tol, square=square)
    if size:
        png = png.resize((size, size), Image.LANCZOS)
    png_path = out_stem.with_suffix(".png")
    png.save(png_path)
    print(f"  {png_path.relative_to(ROOT)}  {png.width}x{png.height}")

    if do_trace:
        svg = trace(m, box, colours[0])
        svg_path = out_stem.with_suffix(".svg")
        svg_path.write_text(svg, encoding="utf-8")
        n = svg.count("<path")
        print(f"  {svg_path.relative_to(ROOT)}  {n} paths (vector, any size)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="report what is in each sheet and exit")
    ap.add_argument("--all", action="store_true", help="run the manifest")
    ap.add_argument("--source")
    ap.add_argument("--colour", default=VIOLET)
    ap.add_argument("--out")
    ap.add_argument("--index", type=int, default=0,
                    help="which shape, largest first")
    ap.add_argument("--tol", type=int, default=60)
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--size", type=int)
    args = ap.parse_args()

    if not SHEETS.exists():
        sys.exit(f"FATAL: put the identity sheets in {SHEETS} first.")

    files = sorted(list(SHEETS.glob("*.png")) + list(SHEETS.glob("*.jpg")))
    if not files:
        sys.exit(f"FATAL: no images in {SHEETS}")

    if args.list:
        for f in files:
            describe(f)
        return

    if args.all:
        manifest = ROOT / "tools" / "asset_manifest.json"
        if not manifest.exists():
            sys.exit("FATAL: run --list first, then write tools/asset_manifest.json")
        for job in json.loads(manifest.read_text(encoding="utf-8")):
            extract(SHEETS / job["source"], job["colours"],
                    ROOT / job["out"], job.get("index", 0),
                    job.get("tol", 60), job.get("trace", False),
                    job.get("square", True), job.get("size"))
        return

    if not (args.source and args.out):
        ap.error("need --source and --out, or --list, or --all")
    extract(Path(args.source), [args.colour], ROOT / args.out,
            args.index, args.tol, args.trace, size=args.size)


if __name__ == "__main__":
    main()
