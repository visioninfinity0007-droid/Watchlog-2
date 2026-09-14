# WatchLog AI Harness — Task Router (Layer 2)

`WATCHLOG.md` (always loaded) holds the non-negotiables and the map. This file routes a request to
the minimum context needed. **Load only what the task needs** — a 4B model has a small window.

Every task first resolves authorized scope from the **application** (never the model):
`tenant → site → allowed cameras → allowed date/time → allowed event/incident IDs`. If scope is not
authorized, stop — do not ask the model to widen it.

## Route by intent

| Intent (examples) | Playbook | Also load |
|---|---|---|
| "Is my CCTV working?" / camera or recorder health | `playbooks/site-health.yaml` | tools: `get_site_health`, `get_camera_health`; `core/coverage.md` |
| "What incidents today?" / "security summary" | `playbooks/today-summary.yaml` → `daily-review.yaml` | tools: `get_daily_intelligence`, `get_incidents`; site-type file |
| "Analyze these event snapshots" | `playbooks/analyze-event.yaml` | `schemas/analysis_result` ; a vision-capable route; `core/evidence.md` |
| "What happened around <area> last night?" | `playbooks/investigate-incident.yaml` | site-type `site-types/<type>.yaml`; camera roles; two-stage retrieval |
| "Trace this person across cameras" | `playbooks/multi-camera-investigation.yaml` (EXPENSIVE, Hive) | `skills/journey-correlation`; `core/identity.md`, `core/confidence.md` |
| "What can this recorder support?" | `playbooks/recorder-change.yaml` (inspect only) | `device-knowledge/<vendor>/…`; tool `search_device_capabilities` |
| "Enable human/vehicle detection on camera X" | `playbooks/recorder-change.yaml` | tool `propose_site_control_change`; `core/actions.md`; **never** raw CGI |
| "Give me today's report" | `playbooks/daily-report.yaml` | tool `get_report` (frozen snapshot); `core/coverage.md` |

## Deterministic-first (many intents need NO model)

Resolve with structured data before invoking any model (mirrors `models/routing.yaml`):
- "Is camera 3 online?" → `get_camera_health` → **NO_MODEL**, answer from JSON.
- "How many incidents today?" → `get_daily_intelligence` count → NO_MODEL.
- A model is used to *summarize / classify / propose* over facts already fetched — never to fetch.

## Per-tenant scoping (loaded automatically)

When a tenant is resolved, its profile and site-type policy load via path-scoped rules; the model
sees only that tenant's facts. See `WATCHLOG.md` → Non-negotiables #1 (tenant isolation is
enforced by the RPC layer's `wl_my_tenant()` / `wl_assert_my_site`, not by this routing).

## Output discipline

Machine actions (tool requests, site-control proposals, incident candidates) MUST validate against
`schemas/*.json`. A natural-language answer to a customer may be prose, but any embedded card /
action / proposal is schema-validated and passes the server-side sanitizer before the browser sees it.
