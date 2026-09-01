# Homepage — Visual QA & Rebuild Plan

Measured on the deployed homepage (main @ 8cce33a) before the design-director pass.
Widths measured: 1440, 390 (representative of the 1440/1280/1024/768/430/390 set — the grid
scales identically between them). `H` = rendered section height.

## Global findings (the deck problem, quantified)
- **Total page height: 14,581px @1440 / 15,171px @390.** Far too tall; reads as a vertical deck.
- **Uniform padding: every section padT=padB=132px @1440** (264px of air between every content
  block). No rhythm hierarchy. This is the #1 cause of "excessive empty space".
- **Two container widths: 1200px (`.wrap`) and 1280px (`.wrap-wide`)** used interchangeably.
  No single grid. → Establish ONE `--maxw:1240` container; breakouts documented.
- **Sections are passive** (eyebrow → heading → copy → screenshot → next). No interaction.
- **Em dashes** present throughout copy (removed site-wide this pass).

## Per-section (index, measured, issue → fix)

| # | Section | H@1440 / H@390 | content W | issue | fix / interaction | goal | CTA |
|---|---|---|---|---|---|---|---|
| 0 | Hero | 902 / 951 | 1280 | product proof small; dark dead space; CTA not dominant | enlarge composite (Overview dominant + incident + report + health tiles), slight container breakout, prominent primary CTA | Promise | Start free / See WatchLog in action |
| 1 | Proof strip | 226 / 264 | 1280 | reads like footer metadata | 3–4 bold proof items, larger, balanced | Proof | — |
| 2 | Problem | 670 / 770 | 1200 | floats on blank white; image small | cool off-white surface, bigger image, tighter pairing | Problem | — |
| 3 | Platform reveal | 1401 / 800 | 1280 | passive single screenshot | **interactive product explorer** (Overview/Incidents/Reports/Site Health tabs → screenshot + statement + metrics) | Product reveal | See the platform |
| 4 | "Three things" zig-zag | 1880 / 1924 | 1200 | tallest; deck-like zig-zag | **sticky capability story** (left sticky nav, right changing visual, scroll-synced); mobile accordion | 3 outcomes | per-item links |
| 5 | Local AI | 1489 / 1059 | 1280 | static diagram | **interactive AI filter demo** (select event → KEPT/FILTERED + reason) | Differentiation | — |
| 6 | How it works | 1392 / 1232 | 1200 | static architecture PNG (uses W-logo as node icons) | **semantic-icon architecture flow** w/ hover reveals; logo only for WatchLog | How it works | How WatchLog works |
| 7 | Daily ops | 757 / 864 | 1200 | static reports screenshot | **channel selector** (WhatsApp/Email/Both) updates delivery visual | Outcome | See reporting |
| 8 | Multi-site | 687 / 836 | 1200 | small topology | **site selector** (Head Office/Warehouse/secondary) → health/count/last/status | Multi-site value | — |
| 9 | Solutions | 1097 / 1871 | 1280 | awkward 3+2 grid | editorial: lead card + 4 secondary; hover zoom | Industry fit | per-tile |
| 10 | Security | 1386 / 969 | 1280 | duplicates the trust argument (also in §6/hero) | consolidate to ONE trust chapter; detail lives on /security/ | Trust | Explore security |
| 11 | Compatibility | 743 / 840 | 1200 | passive | conversion: "I know my recorder / I'm not sure" → label-photo path | Compatibility | Check my recorder |
| 12 | Pricing | 939 / 1356 | 1200 | bland cards, empty space | hierarchy + "how many cameras?" helper pointing to a plan | Commercial | Start free / View pricing |
| 13 | Final CTA | 544 / 455 | 1200 | flat | environment depth, conclusive composition | Conversion | Start free / Check my recorder |

## Rebuild direction
1. One container `--maxw:1240`, gutter `clamp(20px,5vw,40px)`; documented breakouts (hero visual, full-bleed CTA).
2. Rhythm tokens `--sp-xs/sm/md/lg` (48–64 / 72–88 / 96–120 / 128–160) used intentionally — tighter than the current flat 132.
3. Fluid type scale via clamp; body measure 48–68ch.
4. Interactive: product explorer, sticky story, AI demo, reporting selector, multi-site selector,
   compatibility toggle, pricing helper. All keyboard + aria + reduced-motion.
5. Depth: cool off-white, deep navy, subtle blue/violet glows, 1px low-contrast borders, ambient
   shadows behind product UI; avoid card overuse.
6. Consolidate the two security tellings into one chapter. Remove all em dashes.

## Post-rebuild live QA (this file updated with after-measurements)
_(to be filled after deploy: full-page captures + measurements at 1440/1280/1024/768/430/390)_
