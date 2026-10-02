# WatchLog AI Harness — Task Router (Layer 2)

`WATCHLOG.md` (always loaded) holds the non-negotiables and the map. This file describes how a request
should route to the minimum context needed.

## What actually routes today (CODE, not these files)

The RUNTIME router is **`prototype/supabase/functions/watchlog-ai/providers/router.ts`** + `routeChat`
in `index.ts`. It does not read this YAML tree; it enforces routing in code:
- **NO_MODEL** for canonical health/status/coverage/count questions (`noModelIntent`) — answered from
  verified data, no LLM, no egress, no evidence access.
- **Mode → provider** resolution from the DB (`wl_ai_resolve_mode`), egress gate (`0106`), fail-closed
  config, configured fallback, and the verified-data `guided_fallback` floor.
- **Two-stage evidence retrieval** (`retrieveEvidence`) for evidence-intent prompts: compact index →
  load only the relevant event bundles (`wl_ai_evidence_index` / `wl_ai_evidence_bundle`, `0107`).
  For Yesterday / last working day / last service day, `wl_my_business_day_window` supplies the
  tenant-configured evidence boundary so raw evidence and reporting use the same window.

Authorized scope is always resolved by the **application** (`wl_my_tenant()` / `wl_assert_my_site`),
never by a model: `tenant → site → allowed cameras → allowed date/time → allowed event/incident IDs`.


## Live site/business context

The harness now includes reusable `site-types/office.yaml`, `restaurant.yaml`, `warehouse.yaml`, `factory.yaml` and `retail.yaml` policies. Tenant-specific stable context lives in tenant folders such as `tenants/chaiwala-chota-bukhari/context.yaml`, `tenants/al-khalid-main-site/context.yaml`, and `tenants/hasco-steel-head-office/context.yaml`. Each tenant folder can also hold a governed `reporting/` archive and visual-analysis method. The **runtime factual site/business context** remains live through `site_business_context` and `wl_ai_context`.

The Watch AI Edge Function injects a customer-safe `SITE OPERATING CONTEXT`
containing:

- site/business type;
- owner insight priorities;
- site-specific AI guidance;
- canonical camera roles from the production context.

Current verified production examples are documented in
`docs/production/CURRENT_LIVE_CONTEXT_2026-09-28.md`:

- Al-Khalid Security Services — office;
- HASCO Steel Head Office — office;
- Chai Wala - Chota Bukhari — restaurant.

Harness site-type/tenant files define semantics, not live facts. Do not infer that a configured semantic capability produced evidence. Counts, coverage, table calibration rows, camera IDs, observations and incidents must still come from governed runtime data.

### Vision-processing privacy boundary

The cloud `watchlog-vision-worker` is only eligible to process a site when that site explicitly allows external model egress. A tenant/site with external egress disabled may legitimately accumulate pending snapshot reviews even while the cloud worker is healthy. Do not change that privacy setting merely to clear a backlog. Those sites require the private/local worker path (WatchLog-controlled runtime such as local Ollama/Coolify) for visual processing without external image egress.

## Implemented harness content (safe to reference)

- `taxonomy/` — observations, activities, entities, incident-families, severity.
- `core/` — `truth.md`, `confidence.md`, `retention.yaml`, `customer-language.md`.
- `schemas/incident.schema.json`.
- `device-knowledge/` — 47-model recorder capability registry (see `DEVICE_KNOWLEDGE` doc).
- `site-types/restaurant.yaml` — reusable restaurant metric, camera-role, movable-table, reporting and analytics-quality policy.
- `site-types/office.yaml` — reusable office working-day, camera-role, reporting and coverage policy.
- `site-types/warehouse.yaml`, `factory.yaml`, `retail.yaml` — same structure; dock activity is not shipments, camera activity is not production output, entrance activity is not sales. Every site type carries `owner_questions`, modules, `comparison_metrics`, `prohibited_interpretations` and `metric_status` (implemented / derivable / requires_journey_logic / field_gated / external_data_required).
- `tenants/README.md` — registry of active tenant folders vs production sites, plus the test/empty/demo sites that must never be analysed as tenants.
- `tenants/*/context.yaml` — stable tenant/site overlays; live IDs/evidence remain database-owned.
- `tenants/*/reporting/` — governed daily report archive + reproducible analysis methods.
- `skills/tenant-intelligence-setup.md` — repeatable tenant setup/customization procedure.
- `core/customer-language.md` — natural customer-facing management language + report recommendation policy.

## PLANNED content (NOT yet implemented — do not depend on these)

The following are the Phase-2 incident-intelligence content layer and **do not exist yet**. The router
must not depend on them; they are the roadmap, not the runtime:

- `risk-profiles/` — risk overlays composed onto the five site types (PLANNED).
- `incidents/` — the micro-level incident catalogue (PLANNED).
- Additional `skills/` beyond the implemented tenant-intelligence setup skill, e.g. journey-correlation (PLANNED).
- `playbooks/` — per-intent playbooks e.g. site-health / investigate-incident / recorder-change (PLANNED).
- `models/` — model routing/qualification metadata (PLANNED).
- `evals/` — harness eval sets (the runnable benchmark lives at `tools/ai_eval/` today).
- `core/coverage.md`, `core/evidence.md`, `core/identity.md`, `core/actions.md` — additional core notes (PLANNED).

## Intent → PLANNED playbook (roadmap map, not wired yet)

| Intent (examples) | PLANNED playbook | Deterministic today? |
|---|---|---|
| "Is my CCTV working?" / camera or recorder health | `playbooks/site-health.yaml` | YES — NO_MODEL from `get_camera_health` |
| "What incidents today?" | `playbooks/today-summary.yaml` | YES — NO_MODEL count |
| "What happened around <area> last night?" | `playbooks/investigate-incident.yaml` | evidence two-stage (implemented in `router.ts`) |
| "Enable human/vehicle detection on camera X" | `playbooks/recorder-change.yaml` | Site Control propose→approve; **never** raw CGI |
| "Give me today's report" | tenant reporting contract is IMPLEMENTED under `tenants/*/reporting/`; a standalone `playbooks/daily-report.yaml` wrapper is still PLANNED | governed daily/period RPCs + report UI |

## Output discipline

Any embedded card / action / proposal is schema-validated (`schemas/*.json`) and passes the server-side
sanitizer (`sanitizeResult`) before the browser sees it. The model never emits a raw recorder command.
