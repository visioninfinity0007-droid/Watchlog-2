# WatchLog AI — Provider Routing

The router (`functions/watchlog-ai/providers/router.ts` + `routeChat` in `index.ts`) turns a
tenant/site-authorized request into a model call — or a deterministic answer — without ever letting the
model decide access, privacy, or actions. Provider config lives in the DB (`0105`/`0106`), so a
mode/provider/model change **takes effect with no redeployment**.

## Customer modes → providers

Customers see only **WatchLog Instant / Thinking / Hive**. Each mode resolves, at request time, to a
provider + model via `wl_ai_resolve_mode` (service-role only). Provider/model names are never shown to a
customer. Modes are configured in `/admin/ai` — see [ADMIN_CONFIGURATION](ADMIN_CONFIGURATION.md). The third layer is
set with its own audited RPC (`wl_ai_mode_set_tertiary`) so an existing 9-argument `wl_ai_mode_set` save
can never silently clear it.

## Decision order (per request)

1. **NO_MODEL** — `noModelIntent(prompt)`. Canonical health/status/coverage/count questions are answered
   from verified data with **no LLM call, no egress, and no evidence access**. (`route = no_model`.)
2. **Evidence** (model route only) — for an evidence-intent prompt, `retrieveEvidence` runs two-stage
   scoped retrieval (see [PRIVACY_AND_RETENTION](PRIVACY_AND_RETENTION.md)). A NO_MODEL query never reaches this.
3. **Resolve mode → candidates** — `buildCandidates` produces `[primary, fallback, tertiary]` from the DB
   (the tertiary layer is optional, added in `0109`), plus a legacy env-provider bridge **only** when the
   mode is entirely unconfigured (never masking a broken config). A layer's position buys it no extra
   permission: every candidate faces the same egress gate.
4. **Fail closed** — a structurally invalid resolved provider (missing endpoint/model/type) is dropped, not
   guessed; if the configured primary is invalid the outcome is `config_invalid`.
5. **Egress gate (after resolution)** — `egressAllowed(cfg, siteAllowsExternal, modeExternalAllowed)`. A
   LOCAL provider is always allowed; an EXTERNAL provider requires **both** the site to permit external
   processing (`wl_ai_site_egress`, tenant-owned, local-only by default) **and** the mode to permit egress.
   An admin-configured cloud model can never override a local-only site.
6. **Try each layer in order** — primary, then fallback, then tertiary; the first candidate that passes
   egress and returns valid output wins. A non-primary win is audited as `ai_fallback`, with
   `provider_name`/`candidates_tried` identifying which layer actually answered.
7. **Guided fallback (the floor)** — if all candidates are blocked or fail, return the verified-data
   `guided_fallback` (evidence-grounded when the question was an evidence query). Never a fabricated answer.

## Output safety

Every model output flows through `sanitizeResult`: JSON only, allow-listed card types and action kinds,
`url`/`command`/`script` stripped, navigation hrefs allow-listed, device changes forced onto the Site
Control `site_control_proposal` path. **The model never generates a raw Dahua/Hikvision command.**

## Audit

`wl_ai_log_route` records `mode, route (no_model|ai_primary|ai_fallback|guided_fallback), provider_id,
provider_name, model, used_fallback, egress, latency_ms, candidates_tried, tool_calls, outcome`. Readable
by platform admins only (`wl_ai_route_audit`). Provider/model identities live here and in Admin — never in
the customer response.

## Model independence

Adding a provider type = one adapter implementing the `Provider` interface + a `registry` switch case.
`ollama` and `openai_compat` ship today; `openai`/`anthropic`/`gemini` route through the OpenAI-compatible
adapter until native adapters land. No routing, policy, or safety code changes to add a model.
