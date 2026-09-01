# WatchLog Portal — Gap Analysis

**Scope:** visual design, layout, communication. Read-only walkthrough of the live
demo/test tenant (`akss@watchlog.test`) across the five shipped screens: Overview,
Incidents, Reports, Team, Settings. Captured 2026-09-01.

**Benchmark:** the new marketing-site design direction (midnight/navy field, platform
blue `#1748D3` primary, WatchLog violet `#5B21FF` as a gradient/secondary accent, ice
`#72D4FF` on dark, cloud `#F4F7FB` surfaces; disciplined depth, type scale and spacing),
plus general SaaS craft (Verkada / Rhombus / Linear tier). The portal and the website
must read as one product. Today they do not.

---

## A. System-level gaps (every screen)

### A1. Brand and palette drift from the website
- The portal is **violet-primary**: the W mark, primary buttons and links are all bright
  purple. The website is **blue-primary**, with violet reserved for the gradient accent
  and ice for dark surfaces. Side by side they look like two different products.
- **Too many competing accents on one screen:** violet (brand), green (active), amber
  (cameras discovered / warning), red (offline / cancel), orange (the attention banner
  border). There is no single semantic scale (info / success / warning / critical).
- The mark is a flat violet glyph rather than the brand monogram used on the site.

### A2. Flat, monotonous layout
- Every screen is the same shape: a tiny uppercase grey **SECTION LABEL**, then one
  full-width near-black card holding a form or a table, then often a large empty-state
  box. No hierarchy, no rhythm, no density, no personality.
- Cards are oversized for their content (a single WhatsApp field stretched ~1000px wide;
  stat tiles with acres of padding). The product reads as empty and unfinished.
- **No depth system.** The website's cool surfaces, subtle borders, soft shadows and
  ambient glows are absent. Portal cards are flat rectangles with barely-visible edges on
  a flat black page.
- One full-width column stacked vertically throughout; no considered grid.

### A3. Typography and hierarchy
- Section labels are ~11px uppercase low-contrast grey on near-black: hard to read, weak
  as hierarchy.
- Giant numbers with tiny captions and nothing in between: no mid-level hierarchy.
- Timestamps are shown in a developer format (`8/31/2026, 10:16:49 PM`) rather than a
  humanised one (`Aug 31, 10:16 PM`).

### A4. Data and content quality (reads as broken or mock)
- The same site, **"AKSS Head Office (live test)", repeats on ~10+ rows** (one per agent),
  so it looks like duplicated or broken data. The real model (one site, many agents) is
  not expressed: agents should group under their site, and sites should be visually
  distinct from agents.
- Almost everything is **offline** and **"0/15 agents online"**, so the demo reads as a
  dead system. A demo tenant should present a healthy, realistic mix.
- Internal identifiers leak into the UI: `MKT-Awais`, `SM-HP`, `Recorder push`.
- **Broken image placeholders** in the Incidents "STILL" column (a literal broken-image
  icon with `alt` text showing). Stills are otherwise tiny, grainy and inconsistent
  (some `—`, some synthetic coloured frames).
- Raw enum values are shown to users: `line_crossing`, `tamper`, `person`, `motion`.
- **"22 things need attention"** expands into 22 near-identical repeated lines: alarm
  fatigue, near-zero signal. It should summarise ("6 agents offline across 1 site").

### A5. Em dashes throughout
- The website enforces **zero em dashes** (CI-gated). The portal is full of them:
  "Viewer — read only", "Change your plan — you pay…", "No recipients yet — add one…",
  "Starter — PKR 6,000/mo". Inconsistent with the brand voice.

### A6. Component polish
- Native unstyled `<select>` dropdowns: the Team **Role dropdown clips its text**
  ("Viewer — read o…").
- Status pills are ad hoc (grey owner, red offline, green active, amber discovered) with
  no shared pill component.
- Button hierarchy is muddled: violet primary next to red-outline "Cancel subscription"
  and violet-link "Talk to us", all at similar weight.

---

## B. Per-screen gaps

### Overview (`/dashboard`)
- The attention banner dominates the page with repetitive noise; it should be a compact,
  summarised health strip.
- Four flat stat tiles, no icons, no semantic colour (0/15 online should read as
  critical), and a grammar slip ("1 SITES").
- The Sites and Agents table repeats the site name on every agent row, with no grouping,
  everything offline, and unexplained event-count swings (437 then 0).
- No visual anchor: no activity chart, no site map, no incident thumbnails. The marketing
  Overview mockup had a chart and a site-health panel; the real screen is text and number
  boxes.

### Incidents (`/incidents`)
- Broken or missing stills; tiny thumbnails. The flagship "an incident, with the still
  from the moment" is not showcased.
- A large empty FILTER card for two dropdowns; the "19 incidents" count floats loose.
- Raw type enums; no inline incident-detail preview; a monotone table.
- Every row is the same repeated site.

### Reports (`/reports`)
- **The flagship deliverable is invisible.** There is no sample report and no preview of
  what a recipient actually receives. The screen is only a recipient form plus two empty
  states. The website sells "wake up to the useful part"; the product never shows it.
- Barren, oversized empty boxes; em dashes in the copy.

### Team (`/team`)
- Clipped Role dropdown; a single owner row; functionally fine but visually thin and, like
  the rest, mostly empty space.

### Settings (`/settings`)
- One overloaded page carrying billing, plan changes, sites and enrollment codes at once;
  billing and site management should be separated.
- The most colour-noisy screen (green, violet, amber, red together).
- Plan buttons carry em dashes; "Cancel subscription" is given alarming red at the same
  weight as the primary actions.

---

## C. Communication gaps
- **The tone is an alarm list, not calm daily visibility.** "Things need attention",
  "offline", "faults" dominate, so the product feels like a problem feed. The brand
  promise is the opposite: a short, reassuring morning read.
- **The core value is missing from the product:** a clean daily report is what the site
  sells, and it appears nowhere in the portal.
- System and developer language leaks to users (enum values, agent codenames, ISO-ish
  timestamps, "Recorder push").
- Empty states are functional but joyless: no guidance, no illustration, no next step
  beyond a bare sentence.

---

## D. Direction to close the gaps (high level, for a follow-on build)
1. **Re-skin to the website design system.** Blue-primary, violet as accent, ice on dark;
   one semantic colour scale; the cool-surface + subtle-border + soft-shadow card system;
   a consistent type scale; humanised timestamps; zero em dashes.
2. **Fix how data is presented.** Group agents under sites; ship a healthy, realistic demo
   dataset; hide internal identifiers; humanise enums and statuses.
3. **Give every screen a visual anchor and real hierarchy.** Overview: a health summary,
   an activity chart, recent incidents with real thumbnails. Incidents: a detail preview
   with proper stills. Reports: a live sample-report preview (the hero deliverable).
   Settings: split into Billing and Sites.
4. **Build a small component kit** (pill, card, select, button, stat, empty-state) styled
   to the system and used everywhere, so the product stops looking hand-assembled.
5. **Flip the voice** from alarm list to calm, once-a-day visibility, matching the site.

---

## E. Suggested priority order
1. Design-system re-skin (palette, type, surfaces, components) — the single biggest lift
   in perceived quality, and what makes the portal match the site.
2. Data-presentation fixes (grouping, humanising, healthy demo data) — kills the
   "broken / mock" impression.
3. The three anchor screens: Overview health view, Incidents detail with real stills,
   Reports sample-report preview.
4. Split Settings; tidy Team; polish empty states.
