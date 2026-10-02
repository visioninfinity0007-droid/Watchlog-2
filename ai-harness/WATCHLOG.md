# WatchLog AI Harness — The Map (always loaded)

This directory is the **product intelligence of WatchLog**. It is a structured, machine-readable
world that a small model (even a 4B) can reason inside safely. The model is replaceable; this
harness is not.

> The intelligence comes from: good site context + good incident definitions + good evidence +
> deterministic routing + governed tools + recorder knowledge + strong privacy boundaries —
> **not** from hoping a large model understands CCTV.

## Start here: route yourself (every AI, every time)

Any AI working on or inside WatchLog starts at this file and follows the one row that matches its
task. This applies to a customer-facing model, a vision reviewer, a report author, or a coding
assistant (Claude, ChatGPT/Codex, Copilot, Cursor, Gemini). Read the listed files **before** acting.
If nothing matches, say so and propose a new row; do not improvise.

| If you are… | Read, in order |
|---|---|
| **Answering a customer or tenant** (chat, WhatsApp, email) | `core/customer-language.md` → `core/customer-vocabulary.yaml` → `core/truth.md` → `site-types/<type>.yaml` → `tenants/<tenant>/context.yaml`. The chat function receives this automatically as the compiled brief. |
| **Changing customer portal UI, UX or customer-visible portal copy** | `core/customer-language.md` → `core/customer-vocabulary.yaml` → `core/truth.md` → `../03_Design/BRAND_GUIDELINES.md` → `../03_Design/DESIGN_STANDARDS.md`. Preserve owner-first information hierarchy and never expose internal implementation language. |
| **Writing or publishing any report** | `skills/restaurant-daily-business-report.md` (restaurants) → `tenants/<tenant>/reporting/methods/visual-snapshot-analysis.md` → `core/customer-vocabulary.yaml`. The database rewrites any leftover internal wording, but write it right the first time. |
| **Reviewing camera images** (vision worker, manual review) | `tenants/<tenant>/reporting/methods/visual-snapshot-analysis.md` → `site-types/<type>.yaml` camera roles → `core/customer-vocabulary.yaml` for every owner-visible sentence |
| **Counting people / visitors / diners** | `methods/people-counting.md` |
| **Recognising or profiling people** (staff, faces) | `methods/person-recognition.md` |
| **Analysing an incident clip** | `methods/incident-video-analysis.md` |
| **Setting up or changing a tenant** | `skills/tenant-intelligence-setup.md` → `tenants/README.md` (registry) |
| **Saying what a recorder can do** | `device-knowledge/` (never claim a capability the registry does not grade) |
| **Changing AI code, prompts or harness files** | this file → `CONTEXT.md` → then regenerate the compiled brief: `python prototype/scripts/compile_harness_brief.py` (CI fails if it is stale) |

## Every runtime AI and how it receives the harness

No AI runs outside the harness. Each runtime component receives it automatically, in code:

| Runtime AI | How the harness reaches it | Enforced by |
|---|---|---|
| Customer chat (`watchlog-ai`) | Compiled brief on every model call: core rules + customer vocabulary + site type + THIS tenant only. Every answer, card and suggestion passes the vocabulary filter. | `harness.ts`, `harness_brief.generated.ts`, governance tests |
| Cloud vision reviewer (`watchlog-vision-worker`) | Owner-text rules compiled into its system prompt; owner summaries written in customer language | `harness_rules.generated.ts` |
| Private vision worker (`prototype/vision_worker`) | Owner-text rules compiled into both prompts; the worker refuses to start without them | `harness_rules.generated.json` |
| Any report or visual-summary writer (job, worker, person or AI) | The database rewrites internal wording on every save and stamps the internal audit | migration 0141 triggers + `customer_vocabulary_rules` |
| Coding assistants on the repo | Repo-root `AGENTS.md` (plus `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.cursorrules`) route here first | repo root files |

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
7. **WatchLog's internal workings are never disclosed to a customer.** This covers chat, reports,
   WhatsApp/email, portal and website. Customers hear what was seen, when, and how certain it is.
   They never hear how WatchLog captures, samples, stores, processes or reviews it, nor the models,
   vendors or infrastructure behind it. The vocabulary is `core/customer-vocabulary.yaml`, enforced
   in code and in the database.
8. **One tenant never sees another.** A tenant's brief, context, evidence and examples are never shown
   to another tenant. Shared harness text names no tenant.

## Folder map

Status is explicit — the router must never depend on PLANNED paths. The runtime routing/egress/evidence
logic lives in code (`functions/watchlog-ai/providers/router.ts`), not in these files.

| Path | Purpose | Status |
|---|---|---|
| `WATCHLOG.md` | this map (Layer 1) | IMPLEMENTED |
| `CONTEXT.md` | task router notes (Layer 2) | IMPLEMENTED |
| `core/` | invariants — `truth.md`, `confidence.md`, `retention.yaml`, `customer-language.md`, `customer-vocabulary.yaml` | IMPLEMENTED (coverage/identity/evidence/actions notes PLANNED) |
| `methods/` | governed analysis processes — `people-counting.md`, `person-recognition.md`, `incident-video-analysis.md` | IMPLEMENTED as method; each states which steps are live vs PLANNED |
| `taxonomy/` | ontology primitives — observations, activities, entities, incident-families, severity | IMPLEMENTED |
| `schemas/` | JSON Schemas for machine actions — `incident.schema.json` | IMPLEMENTED (other schemas PLANNED) |
| `device-knowledge/` | 47-model Dahua/Hikvision capability registry | IMPLEMENTED |
| `site-types/` | per-vertical policy (camera roles, schedules, metric semantics, quality rules) | IMPLEMENTED (`office.yaml`, `restaurant.yaml`, `warehouse.yaml`, `factory.yaml`, `retail.yaml`; each with owner questions, modules, comparisons and per-metric evidence status) |
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
