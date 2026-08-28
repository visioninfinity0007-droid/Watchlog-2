# WatchLog — Brand Guidelines

Version 2.0, 2026-08-28. Identity direction: **The Modern Standard** —
deep violet. Version 1.0's teal direction was rejected and replaced.

Source of truth for how WatchLog presents itself. Colour and type values are **not** listed here as hex codes —
they live in `projects/watchlog/design-tokens/tokens/`, and that is the
only place they may be changed.

---

## 1. What WatchLog is

WatchLog watches the cameras a business already owns and tells them what
happened.

It is **not** a camera brand, **not** a guarding company, and **not** a
replacement for either. It is the layer that turns a recorder nobody
looks at into a daily report somebody reads.

**One line:** *Your cameras already see everything. WatchLog tells you
what they saw.*

### Who it is for
Businesses that bought CCTV, had it installed, and have not opened the
footage since. Warehouses, retail chains, schools, factories, offices —
anywhere with a recorder in a cupboard and nobody watching it.

### The problem, stated honestly
Cameras record. Nobody reviews. Footage is only ever looked at *after*
something goes wrong, and by then it is evidence rather than security.

### What WatchLog changes
Every incident is logged with a still image the moment it happens, and a
summary lands every morning. Nothing needs to be watched live.

---

## 2. Positioning against the obvious alternatives

| They already have | Why WatchLog still matters |
|---|---|
| A DVR that records 30 days | Recording is not monitoring. Nobody watches it. |
| A phone app from the camera vendor | Shows live video on demand. Does not tell you anything happened. |
| Guards on site | Guards cover what they can see, on shift. Cameras cover everything, always. |
| A monitoring contract | Costs many times more and usually needs the cameras replaced. |

**We do not sell fear.** Competitors in this category lean on break-in
imagery, red alarm styling, and "protect what matters". WatchLog sells
the opposite feeling: *calm, and finally knowing.* That decision drives
every visual choice below.

---

## 3. Voice

**Plain, specific, unexcited.**

WatchLog is read by facility managers and business owners, not security
engineers. It is also read at 8am over tea, on a phone.

| Do | Don't |
|---|---|
| "14 events overnight. Two at the loading bay after midnight." | "Suspicious activity detected!" |
| "Camera 3 has been silent for 26 hours." | "SECURITY BREACH — CAMERA OFFLINE" |
| "Works with the cameras you already have." | "Enterprise-grade AI-powered surveillance intelligence." |
| "It takes about ten minutes to set up." | "Seamless frictionless onboarding." |

**Rules**
- Numbers over adjectives. "14 events" beats "several incidents".
- Never manufacture urgency. If something is genuinely wrong, say what
  and where; if not, say it was quiet.
- No exclamation marks in product copy. Ever.
- Say "cameras" and "recorder", not "CCTV estate" or "edge devices".
- Never claim the system prevents anything. It observes and reports.

---

## 4. Naming

**WatchLog** — one word, capital W, capital L. Never "Watchlog",
"WATCHLOG", "Watch Log", or "watchlog" outside a URL or code identifier.

Product surfaces:
- **WatchLog** — the product as a whole
- **the agent** — the small program installed at a site (lowercase; it is
  not a brand)
- **the portal** — where customers sign in
- **a site** — one physical location with one recorder
- **an incident** — one thing the cameras reported

Never call an incident an "alert" in customer-facing copy. An alert
demands action; most incidents do not.

---

## 5. Visual principles

1. **One brand colour, used decisively.** Deep violet `#5B21FF`. Not a
   palette of brand colours — one, and everything else is ink, white and
   greys. That is the discipline of the chosen direction.

2. **Light for selling, dark for working.** The marketing site is
   white-dominant. The product is near-black, because operators read it
   for long stretches and camera stills sit better on a dark canvas.
   A deliberate split, not an inconsistency.

3. **The brand colour is never a status colour.** Violet is not green,
   amber or red — those three mean *healthy*, *attention* and *fault* and
   nothing else. If a brand colour could be mistaken for a status, the
   status system stops working. Violet is far enough from all three that
   the question never arises.

4. **Every colour has a light and a dark variant, and they are not
   interchangeable.** Violet is 6.76:1 on white but only 2.91:1 on ink —
   on dark surfaces the token is `violet-bright`. The same is true of all
   three status colours. Using the wrong one is an accessibility failure,
   not a style preference.

5. **Red is rationed.** A dashboard that is frequently red teaches people
   to ignore red. Faults only.

6. **Evidence over illustration.** Where a screenshot can be shown,
   show it. Stock photography of hooded figures, glowing padlocks and
   holographic globes is banned outright.

7. **Weight carries hierarchy, colour carries meaning.** Headlines get
   weight and size, not colour. Colour is reserved for the brand violet
   and for status.

8. **Figures must not jitter.** Every number that updates live uses
   tabular figures, or the dashboard looks like it is malfunctioning.

---

## 6. Imagery

**Allowed**
- Real product screenshots, including the dark dashboard
- Real camera stills — with faces and plates blurred, always
- Plain photography of ordinary business premises in daylight
- Simple diagrams: site → agent → cloud → report

**Banned**
- Hooded intruders, crowbars, smashed glass
- Glowing padlock and shield motifs
- Blue holographic "cyber" imagery
- Any red-tinted CCTV-noir treatment
- Stock photos of people pointing at screens

Every simulated or demonstration image must be labelled as such. A
sample frame that could be mistaken for a customer's real footage is
never acceptable in a demo or on the site.

---

## 7. Logo

**Extracted, not drawn.** Production files in `brand-assets/logo/`.

- **The wordmark is the hero.** "WatchLog", geometric grotesque, bold,
  tight negative tracking. In B2B software the name is the primary
  identifier; the mark is secondary.
- **The monogram** is a W of two angular strokes, **1.414× as wide as it
  is tall**. `size` therefore sets its *height*; the width follows. It
  exists for the favicon and the app tile.
- Solid shapes, not outlines: solid survives 16px, where a stroked mark
  turns to mush. Two closed contours, no opacity layers — so the colour
  versions and the one-ink version are the same geometry, and nothing
  breaks on a single-colour print.
- Never on a background that leaves it below 3:1. On dark, use the
  `violet-bright` version.
- **App tile:** corner radius 12.2% of the tile, mark 67.6% of the tile
  width, optically centred. Both measured off the approved lockup sheet.

### Where the geometry comes from — and why it must not be hand-edited

The identity sheets were generated rather than drawn, and they **do not
agree with each other**: four measurably different Ws appear across the
six boards, and even the dominant one varies about 7% between copies.
There was no single clean asset to lift out.

`tools/build_brand_assets.py` resolves that. It finds every copy of the
mark by shape (not by position, so a regenerated sheet does not break
it), clusters them, discards the off-model ones, and takes a per-pixel
majority vote across the seven that agree — at 2048px, well above the
115px the largest good copy was drawn at. The result matches the sources
**95.7%** on average where the sources match each other only **93.2%**.

Every asset — SVGs, PNGs, app icon, favicon, and the portal's `Mark`
component — is derived from that one mask, so they cannot drift apart.
`portal/app/mark.js` is generated and carries a do-not-edit banner.

To change the mark: replace the sheets, re-run the builder, redeploy.
Never edit the path data by hand.

    python tools/build_brand_assets.py           rebuild everything
    python tools/build_brand_assets.py --check   report without writing

---

## 8. Accessibility, as a rule not an aspiration

- Body text meets 4.5:1 minimum. Every colour token records its measured
  ratio in its own comment.
- `violet-bright` is dark-surface-only at 3.72:1 on white. It must never
  carry text on a light background — use `violet` there.
- The same split applies to all three status colours: `status-ok` /
  `status-warn` / `status-bad` are for light surfaces, and the
  `-dark` variants for dark. Both sets were measured; none is guesswork.
- Status is never communicated by colour alone — always a word or icon
  beside it. Roughly 8% of men have some colour vision deficiency, and
  this product is read by facility staff at speed.
- Motion respects `prefers-reduced-motion`.

---

## 9. Where things live

| | |
|---|---|
| Colour, type, spacing, motion values | `design-tokens/tokens/*.json` |
| Built CSS / SCSS / JS | `design-tokens/build/` — generated, never hand-edited |
| Layout and interaction rules | `03_Design/DESIGN_STANDARDS.md` |
| Site structure and copy | `03_Design/SITE_MAP.md`, `WEBSITE_CONTENT.md` |
