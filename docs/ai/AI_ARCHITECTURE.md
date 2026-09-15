# WatchLog AI — Architecture

WatchLog AI is a **model-independent, governed** intelligence layer. The design principle throughout:

> **The model is never the security boundary.** Every authorization, privacy, retention, and action
> decision is made in Postgres (SECURITY DEFINER RPCs) or the edge function — never by a model. A model
> only performs inference over messages the application has already assembled and authorized.

## Components

| Layer | Where | Responsibility |
|---|---|---|
| **Customer AI portal** | `portal/app/ai/` | AI-first chat surface (tenant-scoped). Holds only the publishable key. |
| **Edge gateway** | `prototype/supabase/functions/watchlog-ai/index.ts` | Authenticates the user, resolves tenant/site, gathers deterministic tools, runs the router, writes the assistant message + route audit. |
| **Provider abstraction** | `functions/watchlog-ai/providers/` | `Provider` interface + `ollama` / `openai_compat` adapters + `registry` + the deterministic `router`. Swappable models behind one seam. |
| **Secure model config** | migration `0105` + `/admin/ai` | Providers/models/modes in the DB; API keys in Supabase Vault; owner-gated, audited. |
| **Routing policy** | migration `0106` + `router.ts` | Per-site egress policy + route audit. |
| **Evidence workspace** | migration `0107` | Scoped, encrypted, TTL'd evidence with two-stage retrieval + retention + deletion audit. |
| **Device knowledge** | `ai-harness/device-knowledge/` + `0061/0066/0077` | 47-model, evidence-graded recorder capability registry (honest-unknown). |
| **Ontology / harness** | `ai-harness/` | Incident taxonomy, coverage/confidence classes, schemas, playbooks. |

## Request flow (edge function)

1. **Authenticate** the Supabase user token (`sb.auth.getUser()`); reject otherwise.
2. **Resolve tenant/site** — `wl_my_tenant`, and every data RPC is RLS/`wl_assert_my_site`-scoped. A user can only ever reach their own tenant's sites.
3. **Rate-limit** (`wl_ai_record_usage`), create/verify the conversation (site-match guarded), append the user message.
4. **Gather deterministic tools** (`gatherTools`) — health/coverage/daily-intelligence/report/analytics via governed read RPCs.
5. **Route** (`routeChat`) — see [PROVIDER_ROUTING](PROVIDER_ROUTING.md):
   - `NO_MODEL` for canonical status questions (no LLM, no egress, no evidence);
   - else resolve the mode → provider(s) from the DB, load scoped evidence for evidence questions, apply the egress gate, try primary then fallback, and fall back to verified-data `guided_fallback`.
6. **Sanitize** every model output (`sanitizeResult`) — strips `url`/`command`/`script`, allow-lists card/action kinds, forces device changes through the Site Control approval path. **The model can never emit a raw recorder command.**
7. **Audit** the route (`wl_ai_log_route`): mode, provider, model, primary/fallback, egress, latency, tool calls, outcome — visible to platform admins only, never returned to the browser.
8. **Persist** the assistant message (service-role authored, `0102`).

## Invariants (enforced, not aspirational)

- API keys never reach the browser, logs, or a prompt — Vault only, resolvable solely by the service role.
- Provider/model identities are never shown to a customer — only Admin + the route audit see them.
- A local-only site's prompt/evidence can never be sent to an external model, whatever an admin configures.
- Capability/coverage/identity verdicts are authoritative; `UNKNOWN` ≠ unsupported; `OFFICIAL_DOCUMENTED` ≠ `FIELD_VERIFIED`.
- Retention TTLs are server-set and un-extendable by a model; deletion is automatic, idempotent, audited.

## Testing

Every AI migration has a real-Postgres e2e in the CI `integration` job (`e2e_ai_providers_pg` 0105,
`e2e_ai_routing_pg` 0106, `e2e_ai_evidence_pg` 0107). The `edge-ai` job runs `deno check` + the router
policy unit tests. Model accuracy is measured separately — see [TEST_REPORT](TEST_REPORT.md) — and a
model is **never** auto-promoted to a customer mode; a human configures it after reading the report.
