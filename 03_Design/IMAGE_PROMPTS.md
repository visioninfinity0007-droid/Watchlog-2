# WatchLog — image asset prompts

One asset per prompt. Each states the filename, format, exact pixel size
and background it must have. **Save them under the exact filename given**
— the theme looks for these names and will use them automatically.

Save to: `deploy/wordpress/themes/watchlog/img/`

---

## Read this before generating anything

**Paste this line into every prompt.** The CCTV category's stock imagery
is almost entirely the thing our brand guidelines ban, and generators
default straight to it:

> Negative: no hooded figures, no burglars, no crowbars, no smashed glass,
> no glowing padlock or shield icons, no blue holographic "cyber" overlay,
> no red alarm lighting, no night-vision green wash, no text, no logos, no
> brand names, no watermarks, no people looking at screens and pointing.

**Three rules that matter more than the prompts:**

1. **No brand names on hardware.** Generators will happily invent a
   "Hikvision" logo onto a recorder. That is a trademark problem and it
   will be wrong. Every hardware prompt below says unbranded; check the
   result and reject anything with lettering on it.
2. **Faces and number plates must be blurred** in anything resembling
   camera footage — this is a rule in the brand guidelines, not a
   preference.
3. **Anything that could be mistaken for a customer's real footage gets
   labelled as an example.** The theme already prints that caption; do not
   remove it.

**Where this is likely to go wrong:** generated photography of security
equipment tends to come back either dramatised or subtly fake — cables
going nowhere, impossible mounts, invented ports. Prefer a real photograph
if you can take one. Assets 2, 3 and 7 you could shoot on a phone in ten
minutes at any AKSS client site, and it would beat anything generated.

---

# Section 1 — Hero

### Asset 1: `hero-premises.jpg`

**Format** JPG · **Size** 2400 × 1350 px (16:9) · **Background** photographic, no transparency

> A wide, calm photograph of an ordinary commercial building in Pakistan at
> dusk — a light-industrial warehouse or a distribution yard with a boundary
> wall and a closed metal gate. Warm low sun just gone, sky deep blue,
> a few sodium lights beginning to glow. A single small white bullet CCTV
> camera on a plain wall bracket in the upper right, unbranded, in focus but
> not the subject. Nobody in frame. No vehicles moving. Quiet, ordinary,
> well-kept — the feeling is "an evening where nothing is happening",
> not menace. Documentary photography, natural colour, wide angle, deep
> depth of field, no lens flare, no vignette.
> Negative: [paste the negative line]

**Why this one:** it is the only place on the site where photography earns
its keep. It says "this is your building" before a word is read. It sits
behind the hero at low opacity, so keep the left third visually quiet —
the headline goes there.

---

# Section 2 — Compatibility strip

### Asset 2: `recorder-shelf.jpg`

**Format** JPG · **Size** 1600 × 1000 px (8:5) · **Background** photographic

> A close, honest photograph of a small black CCTV recorder box sitting on a
> shelf in a store cupboard, alongside a router and a coil of cable. Slightly
> dusty. Two blue LEDs lit on the front. Unbranded — no lettering, no logos,
> no model numbers anywhere. Overhead fluorescent light, slightly cool,
> ordinary and unglamorous. Shot at a natural angle from standing height, as
> a person would see it. Sharp, realistic, documentary.
> Negative: [paste the negative line]

**Why:** the product's whole premise is "the box in the cupboard nobody
looks at". Showing it plainly is more persuasive than any diagram.

---

# Section 3 — The problem

### Asset 3: `unwatched-monitor.jpg`

**Format** JPG · **Size** 1600 × 1200 px (4:3) · **Background** photographic

> An empty office at night. A CCTV monitor on a desk showing a four-camera
> grid, screen glowing softly, the only light in the room. The chair in front
> of it is empty and pushed back. Everything else dark and still. The camera
> views on screen are deliberately unreadable — soft, out of focus, no
> discernible people, no faces, no number plates. Shot from a few metres
> back so the empty chair and the lit screen are both in frame. Cool colour
> temperature, natural, no drama.
> Negative: [paste the negative line] — and no green night-vision tint on
> the monitor.

**Why:** the empty chair is the whole argument. Recording is not watching.

---

# Section 4 — What arrives

### Assets 4a–4c: three example incident stills

These illustrate what an incident still actually looks like. They must
read as CCTV frames, not as photographs.

**Format** JPG · **Size** 1280 × 720 px each · **Background** photographic

#### `still-gate.jpg`
> A frame as it would appear from a fixed CCTV camera mounted high on a wall,
> looking down at a metal factory gate at night, lit by a single overhead
> lamp. One person walking through the gate, seen from above and behind, face
> not visible and heavily blurred. Slightly soft, mild sensor noise, wide
> angle with a little barrel distortion, flat contrast. Nothing dramatic
> happening — a person simply walking.
> Negative: [paste the negative line]

#### `still-vehicle.jpg`
> A fixed overhead CCTV frame of a loading bay at night. A small delivery van
> parked with its rear doors open. Number plate fully blurred and
> unreadable. No people. Concrete, roller shutter, sodium lighting. Slightly
> soft, mild noise, flat contrast, wide angle.
> Negative: [paste the negative line]

#### `still-empty.jpg`
> A fixed overhead CCTV frame of an empty yard at night, wet concrete
> reflecting a single lamp, rain falling lightly. Completely empty — no
> people, no vehicles, no movement. Slightly soft, mild noise, flat contrast.
> This is what a false alarm looks like.
> Negative: [paste the negative line]

**Why three:** the first two show what gets reported. The third shows what
gets discarded on site — it is the false-alarm filter made visible, and
that is a genuine differentiator nobody else on the site explains.

---

# Section 5 — How it works

### Asset 5: `site-pc.jpg`

**Format** JPG · **Size** 1600 × 1000 px (8:5) · **Background** photographic

> An ordinary beige office desktop PC under a desk in a small business back
> office, network cable plugged in, dusty, clearly a few years old. Daylight
> from a window off frame. Nobody present. Realistic and unremarkable —
> the point is that it is nothing special.
> Negative: [paste the negative line]

**Why:** "you need a PC at the site" is the requirement most likely to lose
a sale. A picture of a boring old desktop makes it feel small, which it is.

---

# Section 6 — Compatibility / what is not supported

### Asset 6: `recorder-label.jpg`

**Format** JPG · **Size** 1400 × 1000 px (7:5) · **Background** photographic

> A close-up photograph of the printed sticker label on the underside of a
> small CCTV recorder, held at an angle in one hand under indoor light. The
> label shows a barcode and rows of small printed text that are deliberately
> too soft to read. No brand name, no legible model number, no logo. Realistic,
> shallow depth of field, natural skin tone on the hand.
> Negative: [paste the negative line]

**Why:** the page tells people "send us a photo of the label". Showing
exactly which label, and how to hold it, removes the guesswork.

---

# Section 7 — What it is not

No image. This section is deliberately plain text on dark — putting a
picture next to "we are not a guard service" weakens it.

---

# Section 9 — Close

### Asset 7: `close-dusk.jpg`

**Format** JPG · **Size** 2400 × 900 px (8:3 letterbox) · **Background** photographic

> A wide letterbox photograph of a quiet business street at dawn in Pakistan,
> shutters still down, empty pavement, soft blue-grey early light before
> sunrise. Utterly calm and ordinary. No people, no vehicles, no cameras
> visible. Shot from standing height, natural colour, wide angle.
> Negative: [paste the negative line]

**Why:** the section says "find out what your cameras have been seeing".
Dawn closes the loop — the report arrives at 7am, and this is 7am.

---

# /who-its-for — one per segment

**Format** JPG · **Size** 1200 × 800 px (3:2) each · **Background** photographic
Daylight, no people in frame, ordinary and undramatic in every case.

| File | Prompt |
|---|---|
| `seg-warehouse.jpg` | Interior of a mid-sized warehouse in daylight, pallet racking, concrete floor, roller shutter door partly open, no people, no forklifts moving. Natural light, wide angle, realistic. |
| `seg-retail.jpg` | A small retail shop interior in daylight, shelves stocked, counter empty, shutter open to the street. No people, no readable signage or brand names. Natural light. |
| `seg-school.jpg` | An empty school corridor or courtyard in daylight, plain painted walls, closed classroom doors. No people, no signage. Calm and clean. |
| `seg-factory.jpg` | A light-industrial workshop floor in daylight, machinery at rest, tools on a bench, no people. Realistic, no sparks, no motion. |
| `seg-office.jpg` | A small ordinary office in daylight, a few desks, empty chairs, no people, no screens showing anything readable. Natural light from a window. |

Negative for all five: [paste the negative line]

---

# Social and system

### Asset 8: `og-card.png`

**Format** PNG · **Size** 1200 × 630 px exactly · **Background** solid `#0B0B0F`

> A flat, minimal social share card on a solid near-black background
> (#0B0B0F). Empty. No text, no logo, no illustration — only a very subtle
> deep violet radial glow (#5B21FF) bleeding in from the upper left corner
> at low intensity, fading to nothing by the centre. Clean, no noise, no
> texture, no vignette.
> Negative: [paste the negative line]

**Important:** generate it **empty**. I will typeset the wordmark and
headline over it in code, using the real font and the real monogram. A
generator writing "WatchLog" itself will get the letterforms, spacing and
mark wrong — that is exactly how the earlier logo attempts failed.

---

## Summary — 16 files

| # | File | Size | Section |
|---|---|---|---|
| 1 | `hero-premises.jpg` | 2400×1350 | Hero |
| 2 | `recorder-shelf.jpg` | 1600×1000 | Compatibility strip |
| 3 | `unwatched-monitor.jpg` | 1600×1200 | The problem |
| 4 | `still-gate.jpg` | 1280×720 | What arrives |
| 5 | `still-vehicle.jpg` | 1280×720 | What arrives |
| 6 | `still-empty.jpg` | 1280×720 | What arrives |
| 7 | `site-pc.jpg` | 1600×1000 | How it works |
| 8 | `recorder-label.jpg` | 1400×1000 | Compatibility |
| 9 | `close-dusk.jpg` | 2400×900 | Close |
| 10–14 | `seg-*.jpg` ×5 | 1200×800 | Who it's for |
| 15 | `og-card.png` | 1200×630 | Social |

Drop them into `deploy/wordpress/themes/watchlog/img/` under exactly these
names. I will wire each one in, add responsive sizes and `loading="lazy"`,
and check that text over any image still meets its contrast minimum — an
image behind a headline is the most common way a site quietly fails
accessibility.
