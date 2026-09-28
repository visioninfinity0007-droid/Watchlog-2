# WatchLog AI Harness — The Map (always loaded)

This directory is the **product intelligence of WatchLog**. It is a structured, machine-readable
world that a small model (even a 4B) can reason inside safely. The model is replaceable; this
harness is not.

> The intelligence comes from: good site context + good incident definitions + good evidence +
> deterministic routing + governed tools + recorder knowledge + strong privacy boundaries —
> **not** from hoping a large model understands CCTV.

## Non-negotiables (never overridden by a model)

1. **The model is NOT the security boundary.** Tenant/site/camera access, recorder credentials,
   data egress, NVR writes, admin authorization, retention, approvals, and evidence access are
   deterministic application controls enforced in Postgres RPCs and this harness — never by the LLM.
   The model may only: classify intent, extract entities, pick from *permitted* tools, reason from
   *provided* facts, summarize evidence, and *propose* permitted actions.
2. **Coverage truth.** Every claim about a time window is scoped to `LIVE` / `RECOVERED` /
   `UNVERIFIED` coverage. Unmonitored time is never presented as "nothing happened".
3. **Capability truth.** What a recorder can do is read from the device-knowledge registry with an
   evidence class (`FIELD_VERIFIED` > `OFFICIAL_DOCUMENTED` > `IMPLEMENTED_UNVERIFIED` >
   `UNSUPPORTED` > `UNKNOWN`). Never claim a native capability the model exists for is present.
4. **Semantic layers do not collapse.** `Observation → Activity → Journey/Episode → Incident →
   Evidence → Report`. A person detected is not an incident. A vehicle is not suspicious. A
   correlated journey is not an identity.
5. **Unknown stays Unknown.** No invented people, counts, times, health, identity, or capability.
6. **Business-day windows stay consistent.** When a tenant defines a working/service day, reporting,
   visual summaries and raw evidence retrieval must use the same database-resolved window. Never mix
   midnight-to-midnight calendar evidence into a last-working-day or overnight-service-day answer.

## Folder map

Status is explicit — the router must never depend on PLANNED paths. The runtime routing/egress/evidence
logic lives in code (`functions/watchlog-ai/providers/router.ts`), not in these files.

| Path | Purpose | Status |
|---|---|---|
| `WATCHLOG.md` | this map (Layer 1) | IMPLEMENTED |
| `CONTEXT.md` | task router notes (Layer 2) | IMPLEMENTED |
| `core/` | invariants — `truth.md`, `confidence.md`, `retention.yaml`, `customer-language.md` | IMPLEMENTED (coverage/identity/evidence/actions notes PLANNED) |
| `taxonomy/` | ontology primitives — observations, activities, entities, incident-families, severity | IMPLEMENTED |
| `schemas/` | JSON Schemas for machine actions — `incident.schema.json` | IMPLEMENTED (other schemas PLANNED) |
| `device-knowledge/` | 47-model Dahua/Hikvision capability registry | IMPLEMENTED |
| `site-types/` | per-vertical policy (camera roles, schedules, metric semantics, quality rules) | IMPLEMENTED (`restaurant.yaml`, `office.yaml`) |
| `tenants/` | stable tenant/site context folders; each can contain `context.yaml` + governed `reporting/` archive/methods; never duplicates live IDs/evidence | IMPLEMENTED (Chai Wala, Al-Khalid Main site, HASCO Steel Head Office) |
| `skills/` | repeatable harness procedures for tenant intelligence/report setup | IMPLEMENTED (`tenant-intelligence-setup.md`) |
| `risk-profiles/` | risk overlays (e.g. high-security, armory) composed onto a site type | **PLANNED** |
| `incidents/` | the micro-level incident catalogue grouped by family | **PLANNED** |
| `playbooks/` | additional deterministic multi-step per-intent procedures | **PLANNED** (tenant reporting method/skill is already implemented under `tenants/*/reporting/` + `skills/`) |
| `models/` | provider/model routing/qualification metadata | **PLANNED** (routing is enforced in `router.ts` today) |
| `evals/` | harness eval sets | **PLANNED** (runnable benchmark lives at `tools/ai_eval/`) |

## Identity / ID scheme

- Tenants, sites, cameras, events, incidents are **UUIDs** owned by the WatchLog database. The
  harness never invents IDs. Runtime evidence is addressed as
  `tenant_<tenant_id>/site_<site_id>/YYYY-MM-DD/camera_<camera_id>/HH/event_<event_id>/`.
- **Only `is_configured` cameras are "cameras".** Disabled/empty recorder channels are never
  treated as monitored cameras (WatchLog camera truth: `cameras.is_configured`).

## Authority order (facts)

1. WatchLog database (governed RPCs) — the sole factual authority for sites/cameras/health/events/
   incidents/coverage/capability. Tools are thin, tenant-scoped passthroughs over these.
2. This harness — product definitions, site-type/tenant semantics, reporting methods and governed tenant setup skills; incidents remain PLANNED.
3. The model — reasoning *within* 1 and 2, never outside them.
