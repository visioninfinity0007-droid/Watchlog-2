# WatchLog — Design Standards 1.0

The non-negotiables. Modelled on `projects/cargo-max/DESIGN_STANDARDS_4.md`,
which is specific enough to stop a site drifting into generic — this aims
for the same specificity.

Every value below exists as a token. Cite the token, not the number.

---

## Visual grammar

- **Slate establishes authority; teal marks live state, links and primary
  actions.** Neither is ever used to signal health or fault.
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
- Empty states say what will appear and when: "No incidents yet. The
  first will appear within a minute of anything moving."
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
