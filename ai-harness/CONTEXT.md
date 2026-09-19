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

Authorized scope is always resolved by the **application** (`wl_my_tenant()` / `wl_assert_my_site`),
never by a model: `tenant → site → allowed cameras → allowed date/time → allowed event/incident IDs`.

## Implemented harness content (safe to reference)

- `taxonomy/` — observations, activities, entities, incident-families, severity.
- `core/` — `truth.md`, `confidence.md`, `retention.yaml` (mirrors `0107`).
- `schemas/incident.schema.json`.
- `device-knowledge/` — 47-model recorder capability registry (see `DEVICE_KNOWLEDGE` doc).

## PLANNED content (NOT yet implemented — do not depend on these)

The following are the Phase-2 incident-intelligence content layer and **do not exist yet**. The router
must not depend on them; they are the roadmap, not the runtime:

- `site-types/`, `risk-profiles/` — site-type + risk-overlay definitions (PLANNED).
- `incidents/` — the micro-level incident catalogue (PLANNED).
- `skills/` — reusable skills e.g. journey-correlation (PLANNED).
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
| "Give me today's report" | `playbooks/daily-report.yaml` | frozen snapshot RPC |

## Output discipline

Any embedded card / action / proposal is schema-validated (`schemas/*.json`) and passes the server-side
sanitizer (`sanitizeResult`) before the browser sees it. The model never emits a raw recorder command.
