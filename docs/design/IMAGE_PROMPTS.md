# WatchLog — Imagery & Icon System: Research + Generation Prompts

Purpose: replace the current imagery (and fix the icon system) with a cohesive, premium,
on-brand set. Every asset below has a copy-paste generation prompt (or, for diagrams/icons,
a build spec). Read the art-direction rules first: they are what make the set feel like ONE brand.

---

## 1. What's wrong with the current images (grounded review)

Reviewed the live assets, not assumptions:

- **CCTV stills bake garbled text.** The timestamp and camera-label overlays are AI-melted
  gibberish (e.g. cctv-person, cctv-empty-rain). This is the #1 tell that they're fake. General
  image models cannot render clean small text.
- **Some CCTV stills are fear-based.** cctv-person is a hooded figure prowling a gate at night =
  the break-in/intruder trope. WatchLog sells calm operational visibility and works **alongside**
  guards. Menacing imagery is off-brand and (for schools especially) explicitly disallowed.
- **Photography grade is flat / off-brand.** solution-retail etc. are bright, washed, desaturated
  and hazy. The brand is deep navy/blue and cinematic; the photos don't share that grade, so they
  don't feel like one system, and they read "stock."
- **No CCTV cue in environment shots.** Solution photos rarely show a camera, so the "we use your
  cameras" story isn't visually reinforced.
- **Garbled signage/product labels** in interiors (retail shelves) from the same text limitation.
- **Diagrams** are raster PNGs that use the **WatchLog W mark as a generic node icon** for
  recorder/agent/cloud etc. The mark must identify WatchLog only; other nodes need semantic icons.
- **Icons** are a hand-authored stroke set: serviceable but uneven optical weight and limited
  coverage. Not AI's job to fix (see §7).

## 2. Competitor / category research (what premium looks like)

- **Rhombus / Verkada / Spot AI** (reviewed rhombus.com + 2026 category trend pieces): they blend
  **real product UI with real operational context** (footage + a plain-English caption like
  "Manager's office door left ajar" + timestamp/location), a **balanced dark/light** system (not
  aggressive all-dark), **minimalist line icons** in one family, real client photography, and
  restraint. Premium comes from precision + whitespace, not flashy effects.
- **2026 category message** validates WatchLog's angle: video is now a **"shared intelligence layer
  across safety, operations and facilities,"** with **edge AI** (on-device filtering) and turning
  **"footage into valuable data."** Our copy already matches this; the imagery should too.
- **Takeaway for us:** neutral operational scenes (not crime), a clean caption/label treatment
  (added in code, not baked), one cool cinematic grade, a visible camera in context shots, and
  minimalist line icons in a single family.

## 3. Art-direction rules (apply to ALL photography)

- **Grade:** cinematic, cool. Deep navy/teal shadows, controlled warm practical highlights (sodium
  lamps, interior light), low-to-medium saturation, gentle contrast. Think dusk/blue-hour.
- **Mood:** calm, orderly, professional. **Never** fearful, menacing, or crime-themed. No hooded
  figures, no break-ins, no weapons, no distress.
- **Context:** authentic **Pakistan / South-Asian** commercial settings (warehouses, shutter-front
  shops, factories, campuses, offices). Real, not glossy Western stock.
- **Camera cue:** where natural, include one modest **bullet/dome CCTV camera** in frame.
- **Composition:** leave deliberate **negative space** on the side noted, for text overlay. Medium
  or wide shots; avoid extreme fisheye on environment photos.
- **People:** if present, incidental and non-identifiable (back/side, distance, or motion blur).
  Never a face as the subject. No facial-recognition connotation.
- **NEVER bake text** (no timestamps, camera labels, logos, signage copy). Models garble it and it
  reads fake. Generate clean; add overlays in CSS/post (see §5).
- **Consistency:** same grade + lens character across the whole set so tiles sit together.

### Shared style suffix (append to every photo prompt)
> `cinematic cool color grade, deep navy and teal shadows, restrained warm highlights, low saturation, soft directional dusk/overcast light, 35mm full-frame look, subtle film grain, calm and orderly mood, authentic Pakistani commercial setting, premium B2B security brand aesthetic, photoreal, high detail`

### Global negative prompt (append to every photo prompt)
> `text, letters, numbers, timestamp, caption, watermark, logo, brand names, readable signage, garbled writing, hooded figure, intruder, burglar, crime, weapon, gun, knife, violence, blood, horror, close-up face, portrait, surveillance-of-people creepiness, oversaturated, HDR, neon, cartoon, illustration, 3D render, cluttered, distorted, extreme fisheye`

### Model note
Best current fit for photoreal + control: **FLUX 2 / Imagen 3 / Midjourney v6** for the photography;
**Nano Banana Pro (edit)** if you want authentic CCTV artifacts and clean overlay text baked reliably.
Whatever the tool: generate at 2x the display size, export the flat frame, then run through
`tools/build_site_images.py`. Aspect ratios per asset are listed below.

---

## 4. Environment & solution photography prompts

Aspect 3:2, ~2400px wide. Negative space per note. Append the shared suffix + negative prompt.

**A1 · hero-industry-atmosphere** (homepage hero backdrop; keep dark, text sits left)
> A wide dusk view of a mid-size industrial premises in Pakistan: a plastered boundary wall, a steel
> gate, a warehouse block with a few lit windows, a sodium lamp glowing. A single modern white bullet
> CCTV camera mounted on the right foreground wall, in focus, angled over the yard. Deep blue-hour sky.
> Empty, quiet, orderly. Strong negative space across the left half for headline text. 16:9.

**A2 · environment-recorder** (How it works / Setup / Home problem)
> Close, editorial product shot of an ordinary CCTV recorder (NVR/DVR box) sitting on a metal shelf in
> a small back office, a few neatly routed cables, a single status LED. Shallow depth of field, the
> recorder sharp, background softly blurred. Calm, tidy, real. Negative space top-right. 3:2.

**A3 · environment-site-pc** (How it works / Setup)
> An ordinary Windows desktop PC (mini-tower or compact) under a reception/back-office desk in a small
> Pakistani business, powered on, a small blue power light, tidy cabling, a keyboard edge visible. It
> looks unremarkable and always-on. Cool grade, soft window light. Negative space left. 3:2.

**A4 · solution-warehouse**
> Interior of a working warehouse / logistics yard in Pakistan at dusk: pallet racking, a roller
> loading-bay door partly open, a parked delivery van outside, one bullet CCTV camera high on a pillar.
> Nobody in focus. Orderly, end-of-shift calm. Negative space lower area for a label. 3:2.

**A5 · solution-retail**
> A tidy South-Asian shutter-front retail shop at dusk, roller shutter half-open, stocked shelves
> (packaging unbranded/blurred), a glass counter, a dome CCTV camera in the ceiling corner. Warm
> interior light against a cool blue street outside. Calm, closing time. Negative space top. 3:2.

**A6 · solution-manufacturing**
> A clean light-industrial factory floor / gate area in Pakistan: machinery bays, a shift gate, a
> parked forklift, one bullet CCTV camera on a gantry. Cool grade, a few warm work lamps. No people in
> focus. Orderly and safe-feeling (not hazardous). Negative space lower-left. 3:2.

**A7 · solution-school-campus**  (calm, NOT crime; daytime is fine)
> A quiet school / college campus boundary in Pakistan in soft early light: a painted perimeter wall, a
> gate, a courtyard and a low building, trees. A modest bullet CCTV camera on the gate post. Empty,
> peaceful, well-kept. No children's faces. Reassuring, not surveillance-heavy. Negative space top. 3:2.

**A8 · solution-office-commercial**
> A small modern commercial office / building lobby in Pakistan after hours: a reception desk, glass
> door, a corridor, subtle interior lighting, a dome CCTV camera in the ceiling corner. Cool grade,
> calm and empty. Negative space right. 3:2.

---

## 5. CCTV example stills (the important redo)

These feed the AI-filter demo and the incidents story. They must look like **real business CCTV**,
be **neutral/operational (never a crime)**, and carry **NO baked text**. Add the timestamp, camera
label and detection box as a **clean CSS/HTML overlay** in the component (spec at the end) so text is
always legible and on-brand.

Aspect **16:9**, ~1600px. CCTV style prefix + the shared negative prompt (keep "hooded figure,
intruder, crime, weapon" in the negative).

### CCTV style prefix (append to each below)
> `still frame from a fixed business CCTV camera, elevated corner mounting, wide-angle lens (mild, not extreme fisheye), slightly soft focus, faint sensor noise and light compression, muted contrast, cool white balance, ordinary commercial premises, plausible real-world CCTV look, no on-image text or timestamp or label`

**KEPT examples (person / car / motorcycle — neutral, operational):**

**C1 · cctv-person**  (replace the hooded intruder)
> Daytime CCTV view over a warehouse loading area: a worker in a hi-vis vest walking across the yard
> carrying a clipboard, seen from above and behind (not identifiable). Pallets and a parked van nearby.
> Completely ordinary workday activity.

**C2 · cctv-vehicle**
> CCTV view of a delivery van or car pulling up to a loading bay / gate in daylight, seen from a high
> corner. Ordinary logistics. No number plate legible.

**C3 · cctv-motorcycle**
> CCTV view of a motorcycle riding through a business gate in Pakistan in daylight, rider from behind,
> helmet on, unremarkable. High corner angle.

**ACTIVITY examples:**

**C4 · cctv-loading-activity**
> Daytime CCTV view of a loading bay with a couple of workers moving boxes/pallets, a van backed in.
> Busy but calm, routine operations. High corner angle.

**C5 · cctv-gate-movement**
> Dusk CCTV view of a site gate with a car waiting to enter and a guard cabin to the side. Routine
> entry, calm. High corner angle. (Guard cabin is fine: WatchLog works alongside guards.)

**FILTERED (noise) examples:**

**C6 · cctv-empty-rain**  (keep the subject, fix grade + text)
> Night CCTV view of an empty wet yard/street after rain, reflections on the tarmac, nothing moving.
> Quiet, no people, no vehicles.

**C7 · cctv-headlights**
> Night CCTV view of a wall/yard with bright headlight glare sweeping across it, lens flare, but no
> vehicle actually in view. Just light and motion, no subject.

**SITE-HEALTH example:**

**C8 · cctv-camera-offline**  (represents a dead camera)
> A CCTV "no signal" frame: a flat deep-blue screen with faint horizontal scanlines / mild static, or a
> frozen heavily-corrupted grey frame. No text. Reads instantly as "this camera has dropped." 16:9.

### Clean overlay spec (added in code, not baked)
On top of each still the component draws: a small **timestamp** (top-right, monospace, `2026-05-14
01:47:22`), a **camera label** (bottom-left, `CAM 05 · MAIN GATE`), and for KEPT stills a **detection
box** (thin ice-blue rounded rect around the subject with a tiny label `Person · 0.98`). For FILTERED
stills, a muted `No object of interest` chip. This is the "AI product" cue and it's always legible.

---

## 6. Branded diagrams (rebuild as SVG, do not AI-generate)

The six diagrams (architecture, ai-filtering, privacy, report-flow, multi-site, compatibility) should
be **built as SVG/HTML from the design tokens + icon set**, not image-generated. Reasons: AI garbles
the node labels, can't use the real mark, and can't stay crisp/editable. The homepage already proves
this (the interactive architecture + AI demo are HTML). Spec for each (semantic icons, **W mark only
on the WatchLog node**, left-to-right flow, outbound-only arrows):

- **diagram-architecture:** Recorder → Site Agent → Local filtering → Outbound → WatchLog → Portal/Reports.
  Icons: recorder, pc, cpu/shield-check, arrow-out, **W mark**, layout/report. One direction only.
- **diagram-ai-filtering:** Raw events (rain/headlights/motion chips) → Local AI (cpu) → Kept
  (person/car/motorcycle). Green "kept" / grey "filtered". Fail-open footnote.
- **diagram-privacy:** two zones — "Your site" (recorder + credentials, locked) and "WatchLog"
  (events + one still). A single outbound arrow between; a struck-through inbound arrow. Labels: no
  port forwarding, no live access.
- **diagram-report-flow:** Incidents → Composed summary → Delivery (WhatsApp/email) → History.
- **diagram-multi-site:** one WatchLog hub with 3 site nodes (Head Office, Warehouse, Factory), each
  with a small health dot.
- **diagram-compatibility:** "Keep cameras + recorder → add WatchLog", with brand chips
  (Hikvision/Dahua validated; others "check").

Palette: `--field-dark` background, ice-blue accents, brand-gradient on the W node only. Export 1800×1000
+ a 2x. (If you truly want them as flat images, feed the same spec to the model, but expect label
cleanup — SVG is strongly recommended.)

## 7. Product screenshots
Not AI-generated. Standing plan: align the live portal to the navy/blue/violet system, then take
**literal** demo-tenant captures (`PRODUCT_CAPTURE_MANIFEST.md`). The current brand-aligned
design-target renders are placeholders and carry the one baked em dash — replaced at that step.

## 8. Icons — the fix (curated vector set, not AI)

Icons should **not** be image-generated: they must be pixel-consistent at 18–24px, share one grid and
stroke, scale cleanly, and take `currentColor`. Recommendation: adopt **Lucide** (MIT, ~1500 icons,
24px / 2px round-stroke, the de-facto premium-SaaS standard) as one family, inlined as SVG (same as
now, so no runtime cost). Keep the **WatchLog W mark** as-is (brand, not an icon).

Mapping (WatchLog concept → Lucide glyph):

| Concept | Lucide |
|---|---|
| camera / incident | `cctv` or `camera` |
| recorder / NVR | `hard-drive` / `server` |
| site PC / agent | `monitor` / `app-window` |
| local filtering / AI | `cpu` / `scan-eye` |
| outbound only | `arrow-up-from-line` / `share-2` |
| cloud / WatchLog | (W mark) |
| report / daily summary | `file-text` / `clipboard-list` |
| site health / alert | `activity` / `triangle-alert` |
| after hours | `moon` |
| clock / timezone | `clock` |
| sites / locations | `map-pin` / `map` |
| team / people | `users` |
| security / privacy | `shield-check` / `lock` |
| no access / cannot | `shield-off` / `eye-off` |
| WhatsApp | keep brand glyph | 
| email | `mail` |
| compatibility / check | `plug` / `check-circle-2` |
| warehouses | `warehouse` |
| retail | `store` |
| manufacturing | `factory` |
| schools | `graduation-cap` |
| offices | `building-2` |

Rules: one family everywhere; **badged** icons (tinted rounded square) for "system/node" contexts
(proof strip, trust grid, architecture); **bare** icons inside body cards; 1.75–2px stroke; never use
the W mark as a generic icon. Document the final set in `VISUAL_SYSTEM.md`.

---

## 9. Delivery checklist per asset
1. Generate at 2x, flat (no text) for photos/CCTV. 2. Sanity-check: on-brand grade? neutral (not
fear)? no garbled text? camera cue where relevant? negative space correct? 3. Drop into
`themes/watchlog/img/` and run `tools/build_site_images.py` (webp + mobile + fallback). 4. CCTV
overlays + captions are added by the component, never baked. 5. Diagrams + icons are SVG, not images.
