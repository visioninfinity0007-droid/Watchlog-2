# WatchLog brand assets

**Everything in `logo/` and `icons/` is generated. Do not hand-edit it.**

    source-sheets/   the approved identity sheets - the only inputs
    logo/            monogram, app icon, favicon        GENERATED
    icons/           interface icon set                 GENERATED
    MANIFEST.json    what was built, and how well it matched   GENERATED
    product/         screenshots and product imagery    hand-managed
    marketing/       diagrams, social cards             hand-managed
    photography/     premises photography               hand-managed

Rebuild with:

    python tools/build_brand_assets.py           write everything
    python tools/build_brand_assets.py --check   report only, write nothing

## Why these are generated rather than saved by hand

The identity sheets were produced by an image generator, and they do not
agree with each other. Across the six boards there are **four measurably
different Ws**, and even the dominant one varies about 7% from copy to
copy. Saving "the logo" from a sheet means picking one of several
inconsistent drawings and hoping it was the right one.

The builder instead treats the sheets as noisy observations of one
intended mark:

1. Find every violet blob shaped like the monogram, on every sheet —
   **by shape, not by position**, so regenerating a sheet does not break
   the pipeline.
2. Cluster them by overlap. The largest cluster is the real mark; the
   rest are discarded and counted in the manifest.
3. Normalise the cluster onto a common raster at 2048px and take a
   **per-pixel majority vote**.

The consensus matches the source copies **95.7%** on average, where the
sources match each other only **93.2%** — it is closer to what the sheets
were trying to draw than any individual sheet is. It is also produced at
2048px, against the 115px the largest usable copy was actually drawn at.

Everything downstream — every SVG, every PNG size, the app icon, the
favicon, and the portal's `Mark` component — is derived from that single
mask, so no two assets can drift apart.

## What is verified, and what is not

The traced vector was rasterised in Chrome and compared against the
consensus mask: **99.64% IoU**, total area within 0.08%. The deployed
portal was then checked to be serving that exact path data byte for byte.

**The eight interface icons are weaker.** Unlike the monogram they appear
once each, so there is no redundancy to vote across. They are extracted
and traced at their source resolution of roughly 130px — fine for UI at
24–32px, not good enough for print or large format. `MANIFEST.json`
records this. If they are ever needed larger, they need redrawing, not
rescaling.

**Filenames are positional.** `icon-01` … `icon-08` are numbered in
reading order off the sheet, not by meaning. Rename them once someone has
looked at them and decided what each one is.

## Colour

The sheets' own status colours **fail WCAG on white** — the green is
4.02:1 and the amber 2.97:1, against a 4.5:1 minimum. The corrected,
measured values live in `design-tokens/tokens/color.json` and are what
ships; the sheet values are not used. The brand violet on the sheets
samples as `#4308FB` against the token's `#5B21FF`; both pass
comfortably on white and the token value is the one in production.
