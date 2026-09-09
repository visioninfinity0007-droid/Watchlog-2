# Runbook — branded-domain cutover (marketing / portal / downloads)

Moves the public surfaces off the placeholder `*.161.97.175.15.sslip.io` hosts onto a real branded
domain. The application code is already config-driven and carries no hardcoded hosts (removed
2026-09-01, `docs/production/DECISION_LOG.md`), and the portal Docker build now wires every
`NEXT_PUBLIC_*` (fixed 2026-09-09). The remaining work is **configuration**, gated on one external
dependency: a registered domain we control DNS for.

## BLOCKED ON (external — do not proceed until resolved)

1. **A registered root domain we control.** None exists in-repo. `_memory/BRAND_AND_PORTAL_PLAN.md`
   states plainly "No WatchLog domain exists," and `docs/production/CLIENT_DEPENDENCIES.md` lists the
   production domain + DNS as client-blocked (🔵).
2. **A decision: `.pk` vs `.io`.** The repo is inconsistent — `watchlog.pk` appears as the reporter
   email default and in planning notes; `watchlog.io` appears once in the proposal's "client to
   provide" checklist. Pick one.
3. **Portal subdomain decision.** The documented scheme (`_memory/BRAND_AND_PORTAL_PLAN.md`) is
   marketing = `watchlog.<domain>`, portal = `app.watchlog.<domain>`. The as-built sslip naming is the
   opposite (portal = `watchlog.*`, marketing = `watchlogsite.*`). Choose the target scheme below.

Everything past this point uses `<domain>` for the chosen root and assumes the documented `app.` scheme.

## Target routing

| Surface | Target host | Serves from |
|---|---|---|
| Marketing (WordPress) | `watchlog.<domain>` | Coolify `watchlog-website-git` (compose app), host `161.97.175.15` |
| Portal (Next static) | `app.watchlog.<domain>` | Coolify `watchlog-portal-git` (application) |
| Downloads | `watchlog.<domain>/downloads/watchlog/<ver>/…` (+ a stable `…/latest/…`) | the marketing WordPress volume |
| Recorder-push bridge | `push.watchlog.<domain>` (only if promoted from sandbox) | Coolify bridge app |

## DNS records to create (at the registrar / DNS provider — EXTERNAL)

```
watchlog.<domain>.         A     161.97.175.15
app.watchlog.<domain>.     A     161.97.175.15      # or CNAME -> watchlog.<domain>.
push.watchlog.<domain>.    A     161.97.175.15      # only if the bridge is promoted
```
If branded, authenticated email is wanted for the daily report, also add SPF/DKIM/(MX) for
`watchlog.<domain>` per the ESP (ties to the SendGrid dependency in `CLIENT_DEPENDENCIES.md`).

## TLS

Coolify/Traefik auto-issues Let's Encrypt certificates once DNS resolves to the host; set the FQDN on
each resource. **Caveat (Coolify 4.1.2):** a *compose service's* domain is settable only in the Coolify
**dashboard UI**, not via the API — the marketing site is a compose app, so set its domain there. The
portal and bridge are applications and accept `docker_compose_domains` via API.

## Repo / config changes (in this order, after DNS resolves)

1. **Portal build args** (Coolify `watchlog-portal-git` → build args; Next inlines at build, so a
   rebuild is required). The Dockerfile now passes these through:
   - `NEXT_PUBLIC_MARKETING_URL = https://watchlog.<domain>`
   - `NEXT_PUBLIC_INSTALLER_URL = https://watchlog.<domain>/downloads/watchlog/<ver>/WatchLog-Setup.exe`
   - `NEXT_PUBLIC_BILLING_URL`, `NEXT_PUBLIC_BILLING_PROVIDER` (unchanged unless billing moves)
   - `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (unchanged)
   Then redeploy and confirm the served bundle contains the new URLs.
2. **WordPress env** (Coolify `watchlog-website-git`): set `WATCHLOG_PORTAL_URL = https://app.watchlog.<domain>`
   (consumed by `deploy/wordpress/site-content.sh` → `functions.php watchlog_portal_base()`).
   `WP_HOME`/`WP_SITEURL` auto-follow the proxy FQDN — no DB edit needed.
3. **Reporter env** (Coolify reporter): `WATCHLOG_PORTAL_URL = https://app.watchlog.<domain>` and, if
   branded email is set up, `SENDGRID_FROM = reports@watchlog.<domain>`.
4. **Supabase Auth allow-list** (dashboard for `oyvgubyxmjlijiczjona`, NOT in repo): add the new portal
   origin `https://app.watchlog.<domain>` to GoTrue **Site URL + redirect allow-list**. Portal auth
   redirects are `location.origin`-relative, so this server-side allow-list is the only auth edit.
5. **Recorder re-pointing** (operational): each enrolled recorder-push device points at the bridge URL;
   switching that host means re-pointing devices. Skip unless the bridge domain changes.
6. **Docs cleanup** (cosmetic): ~40 references to the sslip hosts in `PROJECT.md`, runbooks, demo and
   audit docs. Non-functional; update opportunistically.

## Out of scope here

- **Installer binary** — the NSIS `AppPublisherURL`/support URL is a signed-release build-define, not a
  runtime config, and the installer is explicitly excluded from this work. Leave it; fold any publisher
  URL change into the next signed release. The installer already hardcodes no domain (a contract test
  asserts `watchlog.pk`/`watchlog.example` are absent from the NSIS).
- `/latest/` promotion and Authenticode signing — separate gated steps.

## Verify after cutover

- `curl -sI https://watchlog.<domain>/` and `https://app.watchlog.<domain>/` → `200`, valid TLS.
- Portal "Download for Windows" and "Talk to WatchLog" links point at the new hosts (view-source the
  served bundle, or `grep` the deployed HTML).
- A login round-trip on the portal succeeds (redirect allow-list correct).
- `https://watchlog.<domain>/downloads/watchlog/<ver>/WatchLog-Setup.exe` → `200` with the expected size.
