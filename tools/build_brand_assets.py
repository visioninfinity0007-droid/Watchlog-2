#!/usr/bin/env python3
"""
Build the WatchLog asset set from the identity sheets.

Why a consensus and not a crop
------------------------------
The sheets were generated, not drawn, and they do not agree with each
other: across the six boards there are four measurably different Ws. Even
the dominant one varies about 7% between copies. So there is no single
pristine asset sitting in a sheet waiting to be lifted out.

What there is: seven copies of the mark the sheets were *trying* to draw.
This script finds them by shape (not by position, which would break the
moment a sheet is regenerated), normalises them onto a common raster, and
takes a per-pixel majority vote. The result agrees with the sources more
closely than the sources agree with each other, and it is produced at
2048px rather than the 115px the largest good copy was drawn at.

Everything downstream - SVGs, PNGs, the app icon, the favicon - is
derived from that one consensus mask, so every asset is the same shape.

    python tools/build_brand_assets.py          build everything
    python tools/build_brand_assets.py --check  rebuild and report only

Needs: pillow, numpy, scipy, scikit-image
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from skimage import measure

from extract_assets import (VIOLET, NEAR_BLACK, blobs, mask_for, merge_boxes,
                            simplify)

ROOT = Path(__file__).resolve().parents[1]
SHEETS = ROOT / "brand-assets" / "source-sheets"
ASSETS = ROOT / "brand-assets"

MASTER_W = 2048          # resolution the consensus is built at
CLUSTER_IOU = 0.90       # two marks are "the same" above this overlap

# Colourways. Names match the design tokens; see
# design-tokens/tokens/color.json for the measured contrast behind each.
COLOURWAYS = {
    "violet":        "#5B21FF",
    "violet-bright": "#8B6BFF",
    "white":         "#FFFFFF",
    "black":         "#0B0B0F",
    "currentcolor":  "currentColor",
}

PNG_SIZES = [512, 256, 128, 64, 32, 16]
ICON_SIZES = [1024, 512, 192, 180, 32, 16]

# App icon proportions, measured off the tile on the lockup sheet.
TILE_RADIUS = 0.122      # corner radius as a fraction of tile width
TILE_MARK_W = 0.676      # mark width as a fraction of tile width


def wshaped(sub: np.ndarray, w: int, h: int) -> bool:
    """
    Does this blob look like the monogram rather than a swatch or a tile?

    A W fills roughly 40% of its box and is about 1.4x as wide as tall.
    A colour swatch is a solid block; an app tile is a solid square.
    """
    return 1.15 < w / h < 1.55 and 0.20 < sub.mean() < 0.55


def find_marks(norm_size: int = 128):
    """Every W-shaped violet blob across every sheet, normalised for comparison."""
    out = []
    for sp in sorted(SHEETS.glob("sheet-*.png")):
        img = Image.open(sp).convert("RGB")
        m = mask_for(img, VIOLET)
        for i, b in enumerate(merge_boxes(blobs(m))[:8]):
            h, w = b[2] - b[0], b[3] - b[1]
            if w < 60 or h < 45:
                continue
            sub = m[b[0]:b[2], b[1]:b[3]]
            if not wshaped(sub, w, h):
                continue
            norm = np.asarray(
                Image.fromarray((sub * 255).astype(np.uint8))
                .resize((norm_size, norm_size), Image.LANCZOS)) > 127
            out.append({"sheet": sp.stem, "index": i, "w": w, "h": h,
                        "mask": sub, "norm": norm})
    return out


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum()) / max(1, int((a | b).sum()))


def dominant_cluster(marks):
    """Group the marks by shape and return the largest group."""
    clusters = []
    for m in marks:
        for cl in clusters:
            if iou(m["norm"], cl[0]["norm"]) > CLUSTER_IOU:
                cl.append(m)
                break
        else:
            clusters.append([m])
    clusters.sort(key=len, reverse=True)
    return clusters[0], clusters[1:]


def consensus(cluster):
    """Per-pixel majority vote across the cluster, at MASTER_W."""
    ratio = float(np.median([m["w"] / m["h"] for m in cluster]))
    h = int(round(MASTER_W / ratio))
    grids = [np.asarray(Image.fromarray((m["mask"] * 255).astype(np.uint8))
                        .resize((MASTER_W, h), Image.LANCZOS),
                        dtype=np.float32) / 255.0 for m in cluster]
    vote = np.mean(grids, axis=0) >= 0.5
    # One light blur-and-rethreshold: the sources are small, and the vote
    # inherits their stair-stepping. This removes it without moving edges.
    soft = np.asarray(Image.fromarray((vote * 255).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(MASTER_W / 400)),
                      dtype=np.float32) / 255.0
    mask = soft >= 0.5
    scores = [iou(mask, g >= 0.5) for g in grids]
    return mask, ratio, scores


def contours_of(mask: np.ndarray, tolerance: float = 1.2):
    pad = np.pad(mask, 1, constant_values=False)
    paths = []
    for c in measure.find_contours(pad.astype(float), 0.5):
        pts = simplify(c, tolerance)
        if len(pts) < 3:
            continue
        paths.append("M " + " L ".join(f"{p[1]-1:.1f} {p[0]-1:.1f}"
                                       for p in pts) + " Z")
    return paths


def write_svg(path: Path, paths, fill: str, w: int, h: int,
              body_override: str = "") -> None:
    body = body_override or "\n  ".join(
        f'<path d="{d}" fill="{fill}"/>' for d in paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'fill-rule="evenodd">\n  {body}\n</svg>\n',
        encoding="utf-8")


def rgba(mask: np.ndarray, colour: str, size=None) -> Image.Image:
    """Mask -> antialiased RGBA at `size`, downsampled from the master."""
    c = colour.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    alpha = Image.fromarray((mask * 255).astype(np.uint8))
    if size:
        alpha = alpha.resize(size, Image.LANCZOS)
    solid = Image.new("RGBA", alpha.size, (r, g, b, 255))
    solid.putalpha(alpha)
    return solid


def build_logo(mask, report):
    out = ASSETS / "logo"
    h, w = mask.shape
    paths = contours_of(mask)
    report["monogram"] = {"master_px": [w, h], "contours": len(paths),
                          "points": sum(p.count(" L ") + 1 for p in paths)}

    for name, fill in COLOURWAYS.items():
        write_svg(out / f"monogram-{name}.svg", paths, fill, w, h)

    for name, fill in (("violet", "#5B21FF"), ("white", "#FFFFFF"),
                       ("black", "#0B0B0F")):
        for s in PNG_SIZES:
            rgba(mask, fill, (s, int(round(s / (w / h))))).save(
                out / f"monogram-{name}-{s}.png")
    return paths


def build_app_icon(mask, report):
    """Violet tile, mark knocked out in white. Built from the same mask."""
    out = ASSETS / "logo"
    h, w = mask.shape

    for size in ICON_SIZES:
        r = int(round(size * TILE_RADIUS))
        tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(tile).rounded_rectangle(
            [0, 0, size - 1, size - 1], radius=r, fill=(91, 33, 255, 255))
        mw = int(round(size * TILE_MARK_W))
        mh = int(round(mw / (w / h)))
        knock = Image.fromarray((mask * 255).astype(np.uint8)).resize(
            (mw, mh), Image.LANCZOS)
        white = Image.new("RGBA", (mw, mh), (255, 255, 255, 255))
        white.putalpha(knock)
        tile.alpha_composite(white, ((size - mw) // 2, (size - mh) // 2))
        tile.save(out / f"app-icon-{size}.png")

    # Vector tile + mark, so the icon scales too.
    scale = TILE_MARK_W * MASTER_W / w
    off_x = (MASTER_W - w * scale) / 2
    off_y = (MASTER_W - h * scale) / 2
    r = MASTER_W * TILE_RADIUS
    body = "\n    ".join(f'<path d="{d}" fill="#FFFFFF"/>'
                         for d in contours_of(mask))
    (out / "app-icon.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {MASTER_W} {MASTER_W}" fill-rule="evenodd">\n'
        f'  <rect width="{MASTER_W}" height="{MASTER_W}" rx="{r:.0f}" '
        f'fill="#5B21FF"/>\n'
        f'  <g transform="translate({off_x:.1f} {off_y:.1f}) '
        f'scale({scale:.4f})">\n    {body}\n  </g>\n</svg>\n',
        encoding="utf-8")

    # Multi-resolution .ico so Windows and older browsers behave.
    Image.open(out / "app-icon-512.png").save(
        out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    report["app_icon"] = {"sizes": ICON_SIZES,
                          "radius_pct": round(TILE_RADIUS * 100, 1),
                          "mark_width_pct": round(TILE_MARK_W * 100, 1)}


def build_ui_icons(report):
    """
    The eight interface icons off the icon sheet.

    Unlike the monogram these appear once each, so there is no redundancy
    to vote across. They are extracted as transparent PNGs plus a trace,
    and the manifest records that the trace is only as good as the ~130px
    it was drawn at.
    """
    sp = SHEETS / "sheet-05.png"
    if not sp.exists():
        return
    img = Image.open(sp).convert("RGB")
    dark = mask_for(img, NEAR_BLACK)
    vio = mask_for(img, VIOLET)

    # Group on both inks together. One of the eight is drawn almost
    # entirely in violet with only a thin dark sliver, so detecting on the
    # dark mask alone silently loses it.
    boxes = [b for b in merge_boxes(blobs(dark | vio), gap=28)
             if 60 < b[3] - b[1] < 260 and 60 < b[2] - b[0] < 260
             and b[0] > 120]                    # skip the sheet's title band
    # Reading order: rows top to bottom, then left to right within a row.
    boxes.sort(key=lambda b: (round(b[0] / 120), b[1]))

    out = ASSETS / "icons"
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for n, b in enumerate(boxes, 1):
        pad = 10
        r0, c0 = max(0, b[0] - pad), max(0, b[1] - pad)
        r1 = min(dark.shape[0], b[2] + pad)
        c1 = min(dark.shape[1], b[3] + pad)
        crop = img.crop((c0, r0, c1, r1))
        arr = np.asarray(crop, dtype=np.float32)
        # Alpha from distance to the nearest ink colour, so antialiasing survives.
        best = np.full(arr.shape[:2], 1e9)
        for col in (NEAR_BLACK, VIOLET):
            ch = col.lstrip("#")
            tgt = np.array([int(ch[i:i + 2], 16) for i in (0, 2, 4)],
                           dtype=np.float32)
            best = np.minimum(best, np.sqrt(((arr - tgt) ** 2).sum(axis=2)))
        alpha = np.clip(255 - (best / 90) * 255, 0, 255).astype(np.uint8)
        Image.fromarray(np.dstack([np.asarray(crop, np.uint8), alpha]),
                        "RGBA").save(out / f"icon-{n:02d}.png")

        w, h = c1 - c0, r1 - r0
        parts = []
        for m, fill in ((dark[r0:r1, c0:c1], "#0B0B0F"),
                        (vio[r0:r1, c0:c1], "#5B21FF")):
            if m.sum() < 40:
                continue
            parts += [f'<path d="{d}" fill="{fill}"/>'
                      for d in contours_of(m, 0.6)]
        write_svg(out / f"icon-{n:02d}.svg", [], "", w, h,
                  body_override="\n  ".join(parts))
        made.append({"file": f"icon-{n:02d}", "px": [w, h]})

    report["ui_icons"] = {
        "count": len(made), "source": "sheet-05.png",
        "note": "one copy each; traced at source resolution, ~130px",
        "icons": made}


def build_component(mask, ratio, report):
    """
    Emit portal/app/mark.js from the same mask.

    The component carries the geometry inline rather than loading an SVG
    file, so the mark cannot flash in late or go missing on a cold cache.
    Generating it here is what makes the "do not hand-edit" note in that
    file true - otherwise the component would drift from every other asset
    the moment someone nudged a number.
    """
    h, w = mask.shape
    vb_h = 1000
    vb_w = int(round(vb_h * ratio))
    paths = []
    for p in contours_of(mask, COMPONENT_TOLERANCE):
        pts = [seg.split() for seg in
               p.replace("M ", "").replace(" Z", "").split(" L ")]
        paths.append("M" + "L".join(
            f"{float(x)/w*vb_w:.1f} {float(y)/h*vb_h:.1f}" for x, y in pts) + "Z")

    body = "\n      ".join(f'<path d="{d}" />' for d in paths)
    src = COMPONENT_TEMPLATE.format(
        vw=vb_w, vh=vb_h, body=body,
        mean=report["agreement_mean"], self_=report["cluster_self_agreement"])
    target = ROOT / "portal" / "app" / "mark.js"
    if target.parent.exists():
        target.write_text(src, encoding="utf-8")
        report["component"] = {"file": "portal/app/mark.js",
                               "viewbox": [vb_w, vb_h],
                               "paths": len(paths),
                               "bytes": len(src)}


COMPONENT_TOLERANCE = 2.0

COMPONENT_TEMPLATE = '''// The WatchLog monogram.
//
// GENERATED - do not hand-edit. Produced by tools/build_brand_assets.py
// from the approved identity sheets in brand-assets/source-sheets/.
//
// Why extracted rather than drawn: the sheets were generated, and they
// disagree with each other - four measurably different Ws across the six
// boards. The builder finds every copy of the mark, clusters them by
// shape, discards the off-model ones, and takes a per-pixel majority vote
// across the seven that agree. The result matches the sources more
// closely ({mean}% mean IoU) than the sources match each other ({self_}%).
//
// To change the mark: replace the sheets, re-run the builder. Editing
// these numbers by hand desyncs the component from every other asset,
// all of which are derived from the same mask.
//
// viewBox is {vw}x{vh} - the mark is wider than it is tall, so `size` sets
// the HEIGHT and the width follows. Wrap it if you need a square box.

const VIEW_W = {vw};
const VIEW_H = {vh};

export default function Mark({{ size = 28, tone = "bright", title }}) {{
  // On dark surfaces the brand violet is only 2.91:1, so the mark uses
  // violet-bright there. Same reason the tokens carry both.
  const fill =
    tone === "bright" ? "var(--color-violet-bright)"
    : tone === "white" ? "var(--color-white)"
    : "var(--color-violet)";

  return (
    <svg
      width={{(size * VIEW_W) / VIEW_H}}
      height={{size}}
      viewBox={{`0 0 ${{VIEW_W}} ${{VIEW_H}}`}}
      fill={{fill}}
      fillRule="evenodd"
      role={{title ? "img" : undefined}}
      aria-hidden={{title ? undefined : "true"}}
      style={{{{ display: "block" }}}}
    >
      {{title ? <title>{{title}}</title> : null}}
      {body}
    </svg>
  );
}}
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="rebuild and report, write nothing")
    args = ap.parse_args()

    marks = find_marks()
    if not marks:
        raise SystemExit(f"FATAL: no monogram found in {SHEETS}")
    win, rejected = dominant_cluster(marks)
    mask, ratio, scores = consensus(win)

    print(f"  found {len(marks)} W-shaped marks across the sheets")
    print(f"  dominant cluster: {len(win)} copies "
          f"({', '.join(sorted({m['sheet'] for m in win}))})")
    if rejected:
        print(f"  discarded {sum(len(c) for c in rejected)} off-model copies "
              f"in {len(rejected)} other shapes")
    print(f"  consensus {mask.shape[1]}x{mask.shape[0]}px, ratio {ratio:.3f}")
    print(f"  agreement with sources: mean {np.mean(scores)*100:.1f}%  "
          f"min {min(scores)*100:.1f}%")

    # How well the sources agree with EACH OTHER - the number the
    # consensus has to beat to be worth building.
    pairs = [iou(a["norm"], b["norm"])
             for i, a in enumerate(win) for b in win[i + 1:]]
    self_agreement = float(np.mean(pairs)) * 100 if pairs else 100.0
    print(f"  sources agree with each other: mean {self_agreement:.1f}% "
          f"-> consensus is {np.mean(scores)*100 - self_agreement:+.1f}pt better")

    report = {"sources": len(marks), "cluster": len(win),
              "ratio": round(ratio, 4),
              "agreement_mean": round(float(np.mean(scores)) * 100, 2),
              "agreement_min": round(float(min(scores)) * 100, 2),
              "cluster_self_agreement": round(self_agreement, 2)}

    if args.check:
        print("  --check: nothing written")
        return

    build_logo(mask, report)
    build_app_icon(mask, report)
    build_ui_icons(report)
    build_component(mask, ratio, report)

    (ASSETS / "MANIFEST.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")

    n = sum(1 for p in ASSETS.rglob("*")
            if p.is_file() and p.suffix in {".png", ".svg", ".ico"}
            and "source-sheets" not in p.parts)
    print(f"  wrote {n} asset files under brand-assets/")
    print("  manifest -> brand-assets/MANIFEST.json")


if __name__ == "__main__":
    main()
