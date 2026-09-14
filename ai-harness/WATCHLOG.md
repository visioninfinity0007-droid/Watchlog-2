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

## Folder map

| Path | Purpose | Machine-readable |
|---|---|---|
| `WATCHLOG.md` | this map (Layer 1) | no |
| `CONTEXT.md` | task router (Layer 2): "what are you doing → load these" | no |
| `core/` | invariants: truth, tenant-isolation, evidence, coverage, identity, confidence, privacy, retention, actions | mixed (`retention.yaml` is runtime) |
| `taxonomy/` | the ontology primitives — observations, activities, entities, incident-families, severity | YAML |
| `site-types/` | per-vertical policy (normal/abnormal/critical, camera roles, schedules, sensitive areas) | YAML |
| `incidents/` | incident definitions grouped by family (reliability, security, safety, operations, loss-prevention, people, compliance) | YAML |
| `skills/` | AI skill manifests (object-detection, tracking, vision, face, reid, anpr, site-control, …) | YAML + tests |
| `playbooks/` | deterministic multi-step procedures the router runs | YAML |
| `schemas/` | JSON Schemas that validate model output for machine actions | JSON |
| `device-knowledge/` | Dahua/Hikvision capability registry (generated from the DB KB + research docs) | YAML |
| `models/` | provider/model routing, capabilities, privacy-routing (admin-configurable at runtime) | YAML |
| `evals/` | grounding, tenant-isolation, incident-classification, tool-selection, site-control, provider-routing | fixtures |

## Identity / ID scheme

- Tenants, sites, cameras, events, incidents are **UUIDs** owned by the WatchLog database. The
  harness never invents IDs. Runtime evidence is addressed as
  `tenant_<tenant_id>/site_<site_id>/YYYY-MM-DD/camera_<camera_id>/HH/event_<event_id>/`.
- **Only `is_configured` cameras are "cameras".** Disabled/empty recorder channels are never
  treated as monitored cameras (WatchLog camera truth: `cameras.is_configured`).

## Authority order (facts)

1. WatchLog database (governed RPCs) — the sole factual authority for sites/cameras/health/events/
   incidents/coverage/capability. Tools are thin, tenant-scoped passthroughs over these.
2. This harness — product definitions (ontology, site-types, incidents, device-knowledge).
3. The model — reasoning *within* 1 and 2, never outside them.
