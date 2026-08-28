# WatchLog — asset prompts

One asset per prompt. Each names the **exact filename, format, pixel size
and background** to produce. Save every file under the given name in the
given folder — the portal and website read from `brand-assets/`, so a
correctly named file is picked up without any further work.

Generate in the same chat that produced the identity sheets, so the model
still has the mark in context. If you start a fresh chat, upload the
construction sheet first and say *"match this exactly"*.

**Reference values, from your sheets:**
Deep Violet `#5B21FF` · Near Black `#0B0B0F` · White `#FFFFFF` ·
Cool Gray `#E9E9EE` · Healthy Green `#1F9D55` · Attention Amber `#C98900`
· Fault Red `#D14343`

**Before you save anything:** if a prompt says transparent background,
check it over a dark surface. Generators routinely return a white square
that only looks transparent on a white page.

---

# Tier 1 — blocking. Nothing can ship without these.

## 1. Monogram, violet

**Save as** `brand-assets/logo/monogram-violet.png`
**Format** PNG · **Size** 1024 × 1024 · **Background** transparent

> The WatchLog W monogram exactly as in the construction sheet — two
> angular chevron strokes forming a W, right stroke narrower than the
> left, with the lighter facet where the strokes overlap.
>
> Colour: Deep Violet `#5B21FF`, including the lighter overlap facet.
> Nothing else in the image.
>
> 1024×1024 px, transparent background, the mark centred and occupying
> about 80% of the canvas. Flat vector-style shapes, hard edges, no
> gradient, no shadow, no glow, no outline, no border, no text.

## 2. Monogram, white

**Save as** `brand-assets/logo/monogram-white.png`
**Format** PNG · **Size** 1024 × 1024 · **Background** transparent

> The same WatchLog W monogram, rendered entirely in white `#FFFFFF`,
> including a slightly translucent white for the overlap facet so the two
> strokes still read as separate.
>
> 1024×1024 px, transparent background, centred, about 80% of the canvas.
> This version is used on dark surfaces. Flat shapes, no gradient, no
> shadow, no glow, no text.

## 3. Horizontal lockup, for light backgrounds

**Save as** `brand-assets/logo/lockup-horizontal-light.png`
**Format** PNG · **Size** 2400 × 600 · **Background** transparent

> The WatchLog horizontal lockup exactly as in the lockup sheet: the
> word **WatchLog** in the bold geometric grotesque with tight negative
> letter-spacing, in Near Black `#0B0B0F`, with the W monogram in Deep
> Violet `#5B21FF` positioned to the RIGHT of the word, its height
> matching the cap height of the text.
>
> 2400×600 px, transparent background, the lockup centred with even clear
> space around it. Flat, no gradient, no shadow. The spelling must be
> exactly "WatchLog" — capital W, lowercase a-t-c-h, capital L, lowercase
> o-g.

## 4. Horizontal lockup, for dark backgrounds

**Save as** `brand-assets/logo/lockup-horizontal-dark.png`
**Format** PNG · **Size** 2400 × 600 · **Background** transparent

> The same horizontal lockup, with the word **WatchLog** in white
> `#FFFFFF` and the W monogram in Deep Violet `#5B21FF` to the right of
> it.
>
> 2400×600 px, **transparent** background — not black. This sits on dark
> surfaces of varying tone, so it must not carry its own background
> panel. Flat, no gradient, no shadow. Spelling exactly "WatchLog".

## 5. Stacked lockup, for light backgrounds

**Save as** `brand-assets/logo/lockup-stacked-light.png`
**Format** PNG · **Size** 1600 × 1600 · **Background** transparent

> The WatchLog stacked lockup: the W monogram in Deep Violet `#5B21FF`
> centred above, and the word **WatchLog** in Near Black `#0B0B0F`
> centred directly beneath it, both optically centred on the same
> vertical axis.
>
> 1600×1600 px, transparent background. Flat, no gradient, no shadow.
> Spelling exactly "WatchLog".

## 6. Stacked lockup, for dark backgrounds

**Save as** `brand-assets/logo/lockup-stacked-dark.png`
**Format** PNG · **Size** 1600 × 1600 · **Background** transparent

> The same stacked lockup, monogram in Deep Violet `#5B21FF` above, the
> word **WatchLog** in white `#FFFFFF` beneath.
>
> 1600×1600 px, **transparent** background — not black. Flat, no
> gradient, no shadow. Spelling exactly "WatchLog".

## 7. App icon

**Save as** `brand-assets/logo/app-icon.png`
**Format** PNG · **Size** 1024 × 1024 · **Background** solid violet

> The WatchLog app icon: a rounded square filled solid Deep Violet
> `#5B21FF`, corner radius about 22% of the width, with the W monogram
> centred on it in white `#FFFFFF` at roughly 55% of the tile width.
>
> 1024×1024 px, the violet tile filling the entire canvas edge to edge —
> no outer margin, no transparent border, no drop shadow, no text, no
> device mockup around it.

## 8. Favicon

**Save as** `brand-assets/logo/favicon-512.png`
**Format** PNG · **Size** 512 × 512 · **Background** solid violet

> The same rounded-square app icon — solid Deep Violet `#5B21FF` tile,
> white W monogram centred — but with the monogram strokes drawn slightly
> THICKER and the overlap facet removed entirely, so it stays readable
> when scaled down to 16×16 pixels in a browser tab.
>
> 512×512 px, tile filling the whole canvas, no margin, no shadow, no
> text.

---

# Tier 2 — the product. Needed for the portal and any demo.

## 9–16. Interface icons

Eight icons, same treatment. Generate each separately.

**Save as** `brand-assets/icons/<name>.png` using these names:
`site`, `camera`, `incident`, `report`, `alert`, `settings`, `user`,
`download`
**Format** PNG · **Size** 256 × 256 each · **Background** transparent

Use this prompt, swapping only the subject line:

> A single interface icon in the WatchLog icon style: drawn on a 24-pixel
> grid, 2-pixel uniform stroke weight, butt caps, 2-pixel corner radius,
> outline style with no fill. Strokes in Near Black `#0B0B0F`, with ONE
> small detail picked out in Deep Violet `#5B21FF` as an accent.
>
> **The subject: `<SUBJECT>`**
>
> 256×256 px, transparent background, the icon centred and occupying
> about 70% of the canvas. One icon only — not a grid, not a set, no
> label, no text, no frame, no shadow.

Subjects, one per icon:

| File | `<SUBJECT>` |
|---|---|
| `site.png` | two simple office buildings side by side, the door picked out in violet |
| `camera.png` | a CCTV camera on a wall bracket, angled down-left, the lens picked out in violet |
| `incident.png` | a flag on a pole, the flag itself in violet |
| `report.png` | a document with a folded corner and three text lines, the folded corner in violet |
| `alert.png` | a rounded triangle with an exclamation mark, the exclamation in violet |
| `settings.png` | a gear, the inner circle in violet |
| `user.png` | a head and shoulders, the shoulder line in violet |
| `download.png` | a downward arrow into a tray, the tray in violet |

## 17. Dashboard screenshot — the hero product image

**Save as** `brand-assets/product/dashboard-dark.png`
**Format** PNG · **Size** 2400 × 1500 · **Background** near-black, full bleed

> A realistic screenshot of a security monitoring dashboard, dark theme.
> Page background Near Black `#0B0B0F`, cards one step lighter at
> `#17171F`, 12-pixel corner radius, hairline borders at 12% white.
>
> Top bar: the WatchLog W monogram in Deep Violet `#5B21FF` beside the
> word WatchLog in white. Below, four stat cards reading
> **1,248 EVENTS · 7D**, **12 CAMERAS**, **3/3 AGENTS ONLINE**,
> **4 SITES** — large white figures, small grey uppercase labels beneath.
>
> Then a table headed "Sites & agents" with three rows — Karachi Head
> Office, Lahore Warehouse, Faisalabad Plant — each with a small rounded
> status pill: two green `#1F9D55` reading "online", one amber `#C98900`
> reading "stale". Below, a row of four small camera-still thumbnails.
>
> Bold geometric grotesque throughout, numbers tabular and aligned.
> 2400×1500 px, filling the whole frame — no browser chrome, no laptop
> mockup, no perspective, no glow, no neon.

## 18. Daily report

**Save as** `brand-assets/product/daily-report.png`
**Format** PNG · **Size** 1600 × 2000 · **Background** white, full bleed

> A clean daily report on a white background, viewed straight on, portrait.
>
> Header: the WatchLog lockup, then "Daily Report", "Karachi Head
> Office", "Tuesday 26 August". Then one very large figure: **14 events
> overnight**. Beneath it three lines — "2 after midnight — Loading Bay",
> "1 camera silent for 26 hours — Rear Perimeter", "No faults". Then
> three small camera stills in a row with timestamps.
>
> Text in Near Black `#0B0B0F`, one violet `#5B21FF` rule under the
> heading, amber `#C98900` only on the "camera silent" line. Generous
> margins, lots of white space, editorial typography.
>
> 1600×2000 px, the page filling the frame — no paper mockup, no desk, no
> shadow, no hands.

## 19. Incident card

**Save as** `brand-assets/product/incident-card.png`
**Format** PNG · **Size** 1200 × 900 · **Background** near-black

> A single dark UI card on a Near Black `#0B0B0F` background. A 16:9
> image on top, caption beneath, 12-pixel corner radius, hairline border.
>
> The image is a plausible CCTV still: a warehouse loading bay at night,
> desaturated and slightly grainy as a real camera would be, one person
> mid-stride in the middle distance with their **face fully blurred**.
> Ordinary, not threatening — someone walking, not an intruder.
>
> Caption: **Loading Bay** · motion, and a smaller grey line
> "26 Aug 2026, 02:14". Small white text reading "SIMULATED FRAME" in the
> corner of the image itself.
>
> 1200×900 px.

---

# Tier 3 — marketing. Needed for the website.

## 20. How it works — diagram

**Save as** `brand-assets/marketing/how-it-works.png`
**Format** PNG · **Size** 2400 × 800 · **Background** white

> A simple horizontal flow diagram, four stages left to right, on a white
> background.
>
> 1. A building outline labelled "Your site", with a small recorder icon
> 2. An arrow labelled "local network" to a small PC labelled "WatchLog agent"
> 3. An arrow labelled **"outbound only — no ports opened"** to a cloud
>    labelled "WatchLog"
> 4. An arrow to a phone showing a report, labelled "Daily report"
>
> Thin even 2px strokes in Near Black `#0B0B0F`; the arrows and the
> "outbound only" label in Deep Violet `#5B21FF`. That label is the most
> important text in the image.
>
> 2400×800 px. Flat and technical — no isometric 3D, no gradient, no
> shadow, no illustration style.

## 21. Social / OG card

**Save as** `brand-assets/marketing/og-card.png`
**Format** PNG · **Size** 1200 × 630 · **Background** near-black, full bleed

> A social sharing card, 1200×630 px, Near Black `#0B0B0F` background
> filling the whole frame.
>
> Left two-thirds: the WatchLog lockup small at the top, then in large
> bold white type "Your cameras already see everything." and beneath it
> in Deep Violet `#5B21FF` "WatchLog tells you what they saw."
>
> Right third: a partial dark dashboard screenshot bleeding off the right
> edge. Generous margins. No border, no logo repetition, nothing else.

## 22–25. Premises photography

**Save as** `brand-assets/photography/premises-<n>.jpg` where `<n>` is
`warehouse`, `retail`, `factory`, `office`
**Format** JPG · **Size** 2000 × 1333 (3:2) · **Background** n/a, photographic

> A documentary photograph of an ordinary Pakistani commercial premises
> in daylight: **`<SUBJECT>`**.
>
> Natural light, wide angle, straight-on or slight angle. No people, or
> only distant unidentifiable figures. Realistic and unstyled — not
> glossy stock photography. A CCTV camera may be visible on a wall but
> must not be the subject.
>
> Slightly cool, muted colour grade. 2000×1333 px, 3:2 landscape.
> No drama, no night scenes, no security guards, no crime, no people
> pointing at anything.

| File | `<SUBJECT>` |
|---|---|
| `premises-warehouse.jpg` | a warehouse loading dock with a roller shutter |
| `premises-retail.jpg` | a small retail shop front on a street |
| `premises-factory.jpg` | a light manufacturing floor |
| `premises-office.jpg` | a modest office reception |

---

# When the files come back

Check each against this before saving:

1. **Is the spelling exactly "WatchLog"?** Generators misspell constantly.
   Wrong spelling is an automatic reject, however good it looks.
2. **Is the background actually transparent** where it should be? Open it
   over something dark.
3. **Does the monogram match the construction sheet** — right stroke
   narrower, overlap facet present?
4. **At 16px, does the favicon still read as a W?**
5. **Is red used anywhere other than a fault?** Reject if so.
6. **Is any demo camera still unlabelled?** Every simulated frame must
   say so.

Then drop them in `brand-assets/` under the exact filenames above and
tell me. I will wire them into the portal and the website — favicon, app
icon, headers, the marketing pages — and re-deploy.
