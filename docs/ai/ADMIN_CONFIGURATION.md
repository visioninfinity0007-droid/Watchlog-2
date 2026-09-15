# WatchLog AI — Admin Configuration (`/admin/ai`)

Migration `0105` + `portal/app/admin/ai/page.js`. **Platform-owner only** (the tab is hidden otherwise;
the page redirects non-owners). Every change is audited and takes effect **without a deployment**.

## What an owner configures

- **Providers** — `wl_ai_provider_upsert`: name, type (`ollama`/`openai_compat`/`openai`/`anthropic`/
  `gemini`), endpoint, default model, capability flags, `privacy` (LOCAL/EXTERNAL), external-egress,
  timeout, max output, priority, cost class. Enable/disable (`wl_ai_provider_set_enabled`), rotate key
  (`wl_ai_provider_rotate_key`), delete (`wl_ai_provider_delete`). All mutations require a 4+ char reason.
- **Models** — optional per-provider model catalogue (`wl_ai_model_upsert`/`_delete`).
- **Modes** — `wl_ai_mode_set`: map WatchLog Instant/Thinking/Hive to a primary + fallback provider/model,
  and the per-mode external-egress allowance.
- **Route audit** — recent routing decisions (`wl_ai_route_audit`): mode, provider, model, egress,
  latency, outcome. Admin/audit visibility only.

## Key handling (Supabase Vault)

API keys are written **only** to Supabase Vault (`vault.create_secret`), referenced by id. The provider
row stores a masked `key_hint` (last-4) and `has_key`, never the key.

- Keys **never** return to the browser after saving, appear in a log, or enter a prompt.
- On read, `wl_ai_admin_config` masks everything; the API-key field in the UI is **write-only** (blank
  keeps the current key).
- The decrypted key is resolvable **only by the service role** (`wl_ai_resolve_provider`), used by the
  edge function at request time.
- If Vault is unavailable, a key write is **refused** ("secure secret storage (Vault) is not available") —
  there is no plaintext fallback. The UI surfaces this as a banner.

## Authorization model

All AI-config RPCs are `SECURITY DEFINER` with an in-body `wl_platform_require(['platform_owner'])` gate
(the `0029` platform-admin spine), and the tables have RLS with `anon`/`authenticated` revoked. A
non-owner is refused with `42501`. Mutations write `wl_platform_write_audit` with **no raw key** in the
before/after JSON.

## Proven (CI: `e2e_ai_providers_pg.py`)

owner creates a keyless provider (no key material in the row) · admin_config masks keys · non-owner is
refused · a key write without Vault is refused · the service role resolves the provider · an authenticated
user cannot resolve the key · a mode resolves to its provider/model · every mutation is audited with no raw
key.
