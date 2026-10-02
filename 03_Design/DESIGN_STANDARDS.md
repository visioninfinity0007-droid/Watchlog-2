# WatchLog — Design Standards 1.0

The non-negotiables. Modelled on `projects/cargo-max/DESIGN_STANDARDS_4.md`,
which is specific enough to stop a site drifting into generic — this aims
for the same specificity.

Every value below exists as a token. Cite the token, not the number.

---

## Visual grammar

- **Ink and neutral surfaces establish authority; deep violet marks the WatchLog brand, links and
  primary actions.** Violet is never used to signal health or fault.
- **Green, amber and red mean healthy, attention and fault. Nothing
  else.** They never appear as decoration, background wash, or brand
  colour.
- Typography carries hierarchy. Cards are used only for discrete,
  comparable items — a site, a camera, an incident. Never for prose.
- Surfaces use visible hairlines and restrained elevation, not heavy
  shadow. This is an instrument panel, not a consumer app.
- The distinctive motif is **the timeline**: events on a horizontal axis,
  a still image attached to a moment. It recurs in the logo mark, the
  activity chart, and the incident strip.


## Owner portal hierarchy

The customer portal is designed for a business owner who may spend only seconds in it.

1. **Home:** state what matters now before presenting navigation or analysis.
2. **Attention:** collect important security, monitoring and management items that merit review.
3. **Insights:** explain measured business activity and comparisons only when the underlying data supports them.
4. **Reports:** present completed management summaries and their delivery history.
5. **Ask WatchLog:** answer specific questions from governed facts and link back to supporting information.
6. **More:** hold cameras/evidence, system health, configuration, team, setup/support and account administration.

The first customer viewport should answer as many of these as the available facts permit:
- Does anything need my attention?
- What happened?
- What changed?
- Can I trust the monitoring picture?

"What changed?" must come from a governed comparable period, not ad hoc arithmetic over raw detections.
Only show a directional change when both periods have enough observed service/working days to support the
comparison. Missing or unverified periods are never filled with zero. When comparison evidence is too thin,
say that a reliable comparison is not ready yet or omit the change block.

Do not fill a dashboard grid with unsupported or decorative KPIs. If a figure is not ready, omit it or say
why it is unavailable. AI is a primary capability, not the prerequisite for receiving value.

## Owner portal shell (Signal Ledger)

The owner portal is built from one shared system: `portal/app/owner/owner.css` (classes `ow-*`) and
`portal/app/owner/ui.js` (components). Pages compose it; they do not restyle it.

- **Three zones on desktop:** left navigation | main management workspace | right intelligence
  rail. The rail is sticky, holds compact decision context (coverage ledger, healthy cameras,
  attention counts, the strongest supported change, latest report, contextual Ask WatchLog) as
  rows — never a second column of large cards. Below 1240px the rail becomes a compact owner
  summary strip at the top of the page (2–4 facts), not a long stacked list.
- **Page order:** compact header (site + one-line title) → one decisive conclusion (`Lead`) →
  the important exception or change → supporting analytics → action → evidence/detail behind
  `Details`. No hero blocks, no explanatory introductions.
- **Copy budget:** titles one line; section headings 3–7 words; at most one short sentence of
  section note; findings are a title plus one sentence.
- **Coverage ledger:** the Signal Ledger coverage component — "23h 02m verified · 58 min could not be
  verified" — built from the governed coverage classes (live / recovered / unverified seconds), or the
  verified percentage when only a ratio exists, and "Not verified yet" when there is nothing. The strip
  is a proportion (verified, then could-not-be-verified); it never implies *when* coverage was missing
  unless the data provides the time positions.
- **Charts:** bars with direct labels; one question per chart, written above it; not-observed days
  or hours are hatched gaps; comparisons are current (violet) against previous (grey) with a
  qualifier stating how many days were observed in each period.
- **Status words:** every coloured mark carries a word (`Status`). Business changes use the neutral
  violet marker, not green/red — green/amber/red stay reserved for health, attention and fault.

## Layout

- Marketing max width `layout.max-width` (1280px). Dashboard
  `layout.max-width-app` (1440px) — tables of sites and cameras need the
  room.
- Section rhythm `layout.section-gap` — clamp(72px, 9vw, 120px).
- Long-form paragraphs stay within `layout.measure` (68ch).
- Each homepage section has one narrative purpose and hands deliberately
  to the next. No alternating background bands for their own sake.
- Mobile recomposes to a single column in reading order. Dense
  collections become scroll-snapped horizontal stories rather than
  endless vertical stacks.
- Radius grammar is explicit: `radius.control` (6px) for buttons and
  inputs, `radius.surface` (10px) for cards and panels, `radius.pill`
  for status pills **and nothing else**.

## Type

- One hero-scale headline per page (`font.size.5xl`). More than one and
  neither is a hero.
- Headlines: `font.weight.bold`, `font.leading.tight`,
  `font.tracking.tight`.
- Body: `font.leading.normal`, never below `font.size.sm`.
- Eyebrows and small labels: uppercase, `font.tracking.wide`,
  `font.size.xs`.
- **All live figures use tabular numerals.** Non-negotiable — proportional
  digits shift width as they update and the dashboard appears to flicker.
- Timestamps, IDs and IP addresses use `font.family.mono`.

## Iconography

The portal uses one canonical icon component: `portal/app/icons.js` → `PortalIcon`.

- Outline icons only: 24px view box, rounded joins/caps, approximately 1.8px stroke.
- Navigation renders at 17–18px beside a text label. An icon never replaces the label for a primary customer action.
- Icons use `currentColor`; they inherit neutral text or the violet active state. They do not introduce new colours.
- Status is never communicated by an arbitrary icon alone. Status keeps its written word plus the green/amber/red/unknown system.
- Do not mix Lucide, Heroicons, emoji, filled glyphs and custom SVGs page-by-page. Add a new semantic drawing to `PortalIcon` only when the existing vocabulary cannot express it.
- The current vocabulary covers Home, Attention, Insights, Reports, Ask WatchLog, Cameras & Evidence, System Health, Camera Settings, Saved Video, Team, Setup & Support, Account, Sites and More.

## Portal typography

- **Geist is the portal face. Inter is the fallback.** Do not add a second decorative/display font to the customer portal.
- Owner-facing headings use tight tracking and restrained weight; the product should feel precise rather than promotional.
- Live figures use tabular numerals.
- Technical monospace is reserved for identifiers/timestamps where character-by-character reading is genuinely useful; never use monospace to make customer UI look “technical”.

## Chart system

- One primary data series uses WatchLog violet. Comparison/baseline series use neutral greys.
- Green, amber and red are reserved for health/attention/fault states, not ordinary chart series.
- Never use rainbow categorical palettes unless categories cannot be distinguished by label/position and the extra colours are semantically necessary.
- Missing or unverified coverage is a gap/unknown state, never a zero-value point.
- Every chart must answer one business question. Prefer direct labels and short explanatory text over legends that force decoding.
- No 3D charts, decorative gradients, glowing lines or animated “AI” effects.

## Motion

- One easing curve everywhere: `motion.ease`.
- Controls `motion.duration.control` (160ms); surfaces
  `motion.duration.surface` (220ms); section reveal
  `motion.duration.reveal` (420ms).
- Reveal translation never exceeds `motion.translate` (8px), and never
  animates from zero opacity — content must be readable if JS fails.
- `prefers-reduced-motion` disables all non-essential animation.
- **Nothing in the dashboard blinks, pulses or flashes.** Ever. It is
  read for hours.

## Status system

| State | Token | Word shown |
|---|---|---|
| Healthy / online | `color.status-ok` | "online" |
| Attention | `color.status-warn` | "stale", "silent 26h" |
| Fault | `color.status-bad` | "offline", "video loss" |
| Unknown | `color.muted` | "never seen" |

Colour never appears without its word. A pill is
`radius.pill`, the state colour at ~14% opacity as background, the state
colour at full strength as text.

## Data display

- Every count is a real count. No rounded-up vanity figures.
- Every timestamp shows the site's local time, and says so.
- Where device and server clocks differ, show the skew rather than hiding
  it — the disagreement is information.
- Empty states state only what the available evidence supports: "No incident episodes are recorded for the verified period." If coverage is incomplete, say so beside the empty state.
- Simulated or demonstration data is labelled in the interface itself,
  not only in the docs.

## Trust and conversion

- The first screen states what it does with the cameras you already own.
  That is the whole differentiator; it goes above the fold.
- A real screenshot appears before any feature list.
- Setup effort is stated honestly and early — "about ten minutes, on a PC
  at the site".
- Requirements are stated *before* sign-up, not discovered during it:
  a Windows PC on the same network as the recorder, and the recorder's
  password. Hiding this produces refunds.
- One primary call to action per page. Support routes (WhatsApp, phone)
  are contextual, never competing.
- No claim about detection accuracy appears anywhere until it has been
  measured on real footage.

## Responsive

- Breakpoints: 480, 768, 1024, 1280.
- Tables become stacked records below 768, never horizontally scrolling
  full tables.
- Touch targets minimum 44px.
- The incident strip is horizontally scroll-snapped on mobile.

## Things that are banned

- Exclamation marks in product copy.
- Red as anything other than a fault.
- Blinking, pulsing or auto-playing anything.
- Stock imagery of intruders, padlocks, shields or glowing globes.
- Unlabelled demonstration data.
- Any accuracy or reliability percentage that has not been measured.
