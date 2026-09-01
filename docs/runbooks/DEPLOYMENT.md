# Runbook — Deployment

Five services run on the Coolify host `161.97.175.15:8000`, all built from `main` of
`github.com/Alkalid-security/Watchlog` via a read-only deploy key.

| Service | Coolify app (uuid) | Build source | URL |
|---|---|---|---|
| Portal | `watchlog-portal-git` (`u10fp0bsvkwjmj8kpty5zk40`) | `/portal`, Dockerfile | https://watchlog.\<domain\> |
| Push bridge | `watchlog-push-bridge` (`ixi45m3km4kp8hrnv0qzh26x`) | `/prototype/bridge`, Dockerfile | https://watchlog-push.\<domain\> |
| Marketing site | `watchlog-website-git` (`ez7677oub6mo1c2gwukaynuk`) | `/deploy`, compose | https://watchlogsite.\<domain\> |
| Report runner | `watchlog-report-runner` (`jrnhfklw01a27mz9xm145fz2`) | `/prototype/reporter`, Dockerfile | https://watchlog-report.\<domain\> |
| Billing service | `watchlog-billing` (`b89g6v1kspqyub48adap4wol`) | `/prototype/billing`, Dockerfile | https://watchlog-billing.\<domain\> |

(During demo/staging these are `*.161.97.175.15.sslip.io`.)

---

## How a change reaches production

1. Merge to `main` (CI green — see below). **There is no auto-deploy** (no GitHub webhook is wired),
   so a push does **not** redeploy on its own.
2. Trigger a deploy:
   - **Coolify UI:** open the app → **Deploy**. or
   - **API:** `POST {COOLIFY_URL}/api/v1/deploy?uuid=<app-uuid>` with the `.env` token
     (`Authorization: Bearer $COOLIFY_API_TOKEN`).
3. Watch the deployment logs (UI → Deployments, or API). The container image is tagged with the git
   commit sha, so you can confirm what shipped.

## Verify a deploy (smoke)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://watchlog.<domain>/           # portal 200
curl -sS https://watchlog-push.<domain>/                                        # bridge: "WatchLog push bridge: OK"
curl -sS -o /dev/null -w '%{http_code}\n' https://watchlogsite.<domain>/        # site 200
curl -sS -o /dev/null -w '%{http_code}\n' https://watchlog-report.<domain>/     # report-runner 200
curl -sS -o /dev/null -w '%{http_code}\n' https://watchlog-billing.<domain>/    # billing 200
```

Confirm the running commit matches HEAD (drift check):
```bash
ssh -i projects/alkhalid-security-portal/ak_key.pem root@161.97.175.15 \
  "docker ps --format '{{.Names}} {{.Image}}' | grep -E 'u10fp0|ixi45m3|ez7677|jrnhfk|b89g6v'"
```
The tag after the `:` is the deployed commit. Compare with `git rev-parse origin/main`.

## Environment variables

Set on each Coolify app (never in git):
- Portal (build-time): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`,
  `NEXT_PUBLIC_MARKETING_URL` (for the "Enterprise — Talk to us" contact link).
- Bridge (runtime): `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`.
- Report runner (runtime): `SUPABASE_DB_*`, `WATCHLOG_PORTAL_URL` (report links — no sslip fallback in
  code), `REPORT_RUNNER_TOKEN`, `REPORT_SEND` (unset/false ⇒ dry-run), Evolution `EVOLUTION_*` (for real send).
- Billing (runtime): `SUPABASE_DB_*`, `BILLING_WEBHOOK_SECRET`, `BILLING_ALLOW_MOCK` (`true` only for the
  demo sandbox; a tenant user still cannot self-mark paid — the authoritative writer is ungranted).
- Site: `WORDPRESS_DB_*`, `SERVICE_FQDN_WORDPRESS`, `WATCHLOG_PORTAL_URL` (theme portal link; demo default
  is env-overridable).
Changing a **build-time** var (portal) requires a redeploy to take effect.

**Healthcheck gotcha:** portal (nginx) uses Coolify's HTTP healthcheck. The three python services
(report-runner, billing, and bridge's python image) run on `python:slim`, which has **no curl**, so
Coolify's curl-based probe would mark them unhealthy — those apps have Coolify's healthcheck **disabled**
and instead define a python-based Docker `HEALTHCHECK` in their Dockerfile. All five report `running:healthy`.

## CI gate

`.github/workflows/ci.yml` runs on every PR to `main` and every push: compile, secret scan, migration
lint, the four offline test suites, and the portal production build. Keep `main` green; merge only
green PRs. CI never touches production (the live/mutating suites are excluded).

## Notes / gotchas (learned the hard way)

- Coolify **ignores `external: true`** on compose volumes and creates its own — never assume a volume
  is shared.
- The WordPress entrypoint only seeds `/var/www/html` when empty; a theme change needs the container
  rebuilt, not just restarted.
- WordPress content (pages, uploads) lives in the DB + volume, **not** git — back it up separately
  (`BACKUP_RECOVERY.md`).
- Database migrations are a **separate, deliberate** step (`DATABASE_MIGRATIONS.md`) — deploying code
  does not apply them.

## Rollback

Redeploy the previous commit: in Coolify, deploy from a specific commit, or revert the merge on `main`
and redeploy. Because images are commit-tagged, the previous image is identifiable and re-deployable.
