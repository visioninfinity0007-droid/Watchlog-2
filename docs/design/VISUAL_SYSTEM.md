# WatchLog — Visual System (website revamp)

The digital system around the existing WatchLog mark. The logo/mark do not change.
This drives `style.css` (tokens + primitives), `home.css`, `pages.css`.

## 1. Colour

| Token | Value | Use |
|---|---|---|
| `--ink` | `#07111F` | Midnight. Darkest background; deepest sections, footer. |
| `--navy` | `#0B1D3A` | Deep navy. Primary dark-section base. |
| `--navy-2` | `#102A52` | Raised navy. Cards/panels on dark. |
| `--blue` | `#1748D3` | Platform blue. Primary CTA on light, links, focus. |
| `--blue-600` | `#1039AE` | Pressed / hover for platform blue. |
| `--violet` | `#5B21FF` | WatchLog violet. Brand accent; gradient partner. |
| `--ice` | `#72D4FF` | Ice blue. Eyebrows + accents ON DARK only (fails contrast on white). |
| `--cloud` | `#F4F7FB` | Light section background. |
| `--white` | `#FFFFFF` | Light section alt / cards on light. |
| `--ink-900` | `#0B1522` | Headings on light. |
| `--slate-600`| `#475569` | Body text on light. |
| `--slate-500`| `#64748B` | Muted text on light. |

Text on dark: heading `#FFFFFF`; body `rgba(255,255,255,.74)`; muted `rgba(255,255,255,.55)`.
Card on dark: fill `rgba(255,255,255,.04)`; hairline `rgba(255,255,255,.09)`.

**Brand gradient** (mark, primary buttons, accents): `linear-gradient(135deg,#5B21FF 0%,#1748D3 100%)`.
**Dark section field** (matches the supplied diagrams so they blend seamlessly):
`radial-gradient(120% 120% at 82% 0%, #12294F 0%, #0B1D3A 46%, #07111F 100%)`.

Rule: blue/navy creates DEPTH, not decoration. Dark is reserved for the conceptual peaks —
hero, platform reveal, local AI, security, final CTA. `--ice` is a dark-mode accent only.
Status colours (green/amber/red) keep their product meaning and are never the brand accent.

## 2. Typography

Display + text: **Geist** (loaded), Inter fallback. One family, weight does the work.

| Role | Size (fluid) | Weight | Tracking | Leading |
|---|---|---|---|---|
| Hero H1 | `clamp(2.6rem,5.6vw,4.6rem)` | 700 | -0.03em | 1.02 |
| H2 | `clamp(2rem,3.6vw,3.1rem)` | 700 | -0.025em | 1.06 |
| H3 | `clamp(1.3rem,2vw,1.75rem)` | 600 | -0.02em | 1.15 |
| Eyebrow | `.8rem` | 600 | .14em UPPER | 1 |
| Lead | `clamp(1.1rem,1.4vw,1.3rem)` | 400 | -0.01em | 1.55 |
| Body | `1.0625rem` | 400 | 0 | 1.65 |
| Small | `.875rem` | 500 | 0 | 1.5 |

Paragraphs ≤ ~3 lines desktop (~60ch max). Headlines short, specific, no buzzwords.

## 3. Layout & rhythm

- Container: content `1200px`, wide `1280px`; gutter `clamp(20px,5vw,40px)`.
- Section vertical padding: `clamp(72px,10vw,132px)`.
- Grid: 12-col mental model; most splits are 6/6 or 7/5 (product-led).
- Whitespace is intentional structure, not filler. Prefer air + hairlines over boxes.

## 4. Section background rhythm (home)

Deliberate alternation, dark at the conceptual peaks:

1. Hero — **DARK** (field + hero photo edge)
2. Proof strip — **DARK** (thin band, continues hero)
3. Problem/context — **LIGHT**
4. Platform reveal — **DARK** (deep, product-launch)
5. Three capabilities — **LIGHT** (alternating rows)
6. Local AI — **DARK / violet**
7. How it works — **LIGHT**
8. Daily operations — **LIGHT** (tinted)
9. Multi-site — **DARK**
10. Solutions — **LIGHT** (editorial image tiles)
11. Security — **DARK** (deep navy)
12. Compatibility — **LIGHT**
13. Pricing/trial — **LIGHT**
14. Final CTA — **DARK** (full-bleed)

## 5. Components

- **Buttons.** `.btn-primary` platform-blue fill, white text, radius 10px, weight 600, subtle
  shadow; hover lifts 1px + darkens. `.btn-ghost` (on dark) 1px `rgba(255,255,255,.28)` border,
  white text, transparent → `rgba(255,255,255,.06)` on hover. `.btn-secondary` (on light) navy
  text + `#CBD5E1` border. Min height 44px (tap target).
- **Eyebrow.** Uppercase label; `--blue` on light, `--ice` on dark.
- **Product frame.** Screenshots sit in a faux app chrome: rounded 14px, hairline, top bar with
  three dots, soft shadow (`0 30px 80px -30px rgba(3,10,25,.6)`) + a faint brand glow on dark.
  Makes captures read as product, never "image in a card".
- **Cards.** Minimal. On dark: `rgba(255,255,255,.04)` fill + `.09` hairline. On light: `#fff`
  fill + `#E7EDF5` hairline + soft shadow. Avoid stacking many bordered cards; alternate
  image/text editorial rows for capability storytelling instead.
- **Trust chip.** Pill with a check glyph; used under hero CTAs and in proof strips.
- **Stat.** Large number (count-up once) + label; for after-hours/health metrics.

## 6. Motion

150–350ms, ease `cubic-bezier(.2,.6,.2,1)`. Allowed: reveal-on-scroll (opacity + 12px rise),
tab/screenshot cross-fade, card hover lift, count-up (once), gentle solution-image scale (1.03).
Everything wrapped in `@media (prefers-reduced-motion: reduce)` → no transform/opacity animation.
No parallax, scroll-hijack, particles, video backgrounds, or cursor-follow.

## 7. Accessibility

Semantic headings (one H1/page), visible focus ring (`2px --blue` on light / `--ice` on dark,
`outline-offset:2px`), 44px targets, alt text on meaningful images + empty alt on decorative,
`aria-expanded`/`aria-controls` on menu + accordions, contrast AA on both surfaces
(ice/`#72D4FF` only on dark; body-on-dark ≥ 4.5:1).

## 8. Performance

Custom lightweight theme — no builder/React/slider. Fonts `display=swap`. Images via `<picture>`
WebP + `-sm` mobile source + JPG/PNG fallback (`tools/build_site_images.py`), `loading="lazy"`
except the hero composite (`fetchpriority="high"`). All CSS hand-written, split home/pages.
