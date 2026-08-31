# Runbook — Key / credential rotation

Rotate a credential without downtime: create the new one, move every consumer, verify, then
invalidate the old one. Never commit a value; every secret lives in `projects/watchlog/.env`
(gitignored) or in the provider's dashboard. `tools/secret_scan.py` (run in CI) fails the build if a
real secret is ever committed.

For each credential: **consumers → new → cutover → verify → invalidate → verify → record the date.**

---

## Supabase DB password
- **Consumers:** `prototype/supabase/apply_migrations.py`, `prototype/reporter/daily_report.py`, any
  ops psql session. Env name `SUPABASE_DB_PASSWORD`.
- **Rotate:** Supabase dashboard → Project → Database → *Reset database password*.
- **Cutover:** update `SUPABASE_DB_PASSWORD` in `.env`.
- **Verify:** `python prototype/supabase/apply_migrations.py --status` connects and lists migrations.
- The old password is invalidated by the reset itself. Record the date.

## Supabase publishable key
- Public by design (shipped in the portal bundle, the agent, the bridge). Rotate only if you must
  invalidate it. **Consumers:** portal build arg `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (Coolify),
  bridge env `SUPABASE_PUBLISHABLE_KEY` (Coolify), `.env`, every deployed agent.
- **Rotate:** Supabase → API keys → roll the publishable/anon key.
- **Cutover:** update Coolify env on `watchlog-portal-git` and `watchlog-push-bridge`, redeploy both;
  update `.env`; agents pick it up on next install. **Note:** this breaks already-installed agents
  until they are updated — treat as a coordinated release, not a routine rotation.
- The **secret** (service_role) key is intentionally unused and empty in `.env`; do not introduce it.

## Coolify API token
- **Consumers:** the deploy/inspect scripts in this repo. Env name `COOLIFY_API_TOKEN`.
- **Rotate:** Coolify → Keys & Tokens → API tokens → create new, delete old.
- **Cutover:** update `COOLIFY_API_TOKEN` in `.env`. Store it **unquoted** (a quoted value breaks
  `Bearer` auth — this bit us once).
- **Verify:** a `GET /api/v1/teams` with the new token returns 200.

## Evolution API key (WhatsApp)
- **Consumer:** `prototype/reporter/daily_report.py`. Env names `EVOLUTION_API_URL`,
  `EVOLUTION_API_KEY`, `WATCHLOG_WHATSAPP_INSTANCE`.
- **Rotate:** Evolution admin → regenerate the instance/global apikey.
- **Cutover:** put the new values in WatchLog's **own** `.env` (do not rely on the sibling
  `alkhalid-security-portal/.env.local` fallback — that coupling is being removed).
- **Verify:** `python prototype/reporter/daily_report.py` (dry run) prints `whatsapp ready — instance
  '<name>' is 'open'`.

## SendGrid API key
- **Consumer:** the reporter's `Email` channel. Env names `SENDGRID_API_KEY`, `SENDGRID_FROM`.
- **Rotate:** SendGrid → API Keys → create, then delete old. **Cutover:** update `.env`.
- **Verify:** dry run shows `email ready`; send one test to an internal address.

## Agent per-agent secret (`agent_key`)
- Minted server-side at enrollment, stored SHA-256-hashed in `agents.agent_key_hash`; the plaintext
  lives only in `C:\ProgramData\WatchLog\agent_state.json` on the site PC.
- **Rotation = re-enrolment:** issue a fresh code (`ADDING_TENANT.md` helper or the portal), then on
  the site PC delete `agent_state.json` and reinstall/re-run setup so the agent enrolls anew. The old
  agent row can be left (it simply goes offline) or removed by ops.

## GitHub Coolify deploy key
- Read-only deploy key on the repo pulls source into Coolify. **Rotate:** generate a new key in
  Coolify (Sources/private key), add the public half to the repo's Deploy keys, remove the old one.
- **Verify:** trigger a deploy of `watchlog-portal-git`; it clones successfully.

## GitHub PAT (used by tooling to push/PR)
- Not stored in this repo. Rotate in GitHub → Developer settings; update wherever the operator keeps
  it. Never write it into `.git/config` (`push -u` to a tokenized URL does this — push to a plain URL
  instead, or clean it afterward).

---

**After any rotation:** run `python prototype/tests/test_tenant_isolation.py` (still 9/9) and note the
rotation date + credential in your ops log.
