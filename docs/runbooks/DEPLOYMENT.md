# Runbook — Deployment

Three services run on the Coolify host `161.97.175.15:8000`, all built from `main` of
`github.com/Alkalid-security/Watchlog` via a read-only deploy key.

| Service | Coolify app (uuid) | Build source | URL |
|---|---|---|---|
| Portal | `watchlog-portal-git` (`u10fp0bsvkwjmj8kpty5zk40`) | `/portal`, Dockerfile | https://watchlog.\<domain\> |
| Push bridge | `watchlog-push-bridge` (`ixi45m3km4kp8hrnv0qzh26x`) | `/prototype/bridge`, Dockerfile | https://watchlog-push.\<domain\> |
| Marketing site | `watchlog-website-git` (`ez7677oub6mo1c2gwukaynuk`) | `/deploy`, compose | https://watchlogsite.\<domain\> |

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
```

Confirm the running commit matches HEAD (drift check):
```bash
ssh -i projects/alkhalid-security-portal/ak_key.pem root@161.97.175.15 \
  "docker ps --format '{{.Names}} {{.Image}}' | grep -E 'u10fp0|ixi45m3|ez7677'"
```
The tag after the `:` is the deployed commit. Compare with `git rev-parse origin/main`.

## Environment variables

Set on each Coolify app (never in git):
- Portal (build-time): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`.
- Bridge (runtime): `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`.
- Site: `WORDPRESS_DB_*`, `SERVICE_FQDN_WORDPRESS`.
Changing a **build-time** var (portal) requires a redeploy to take effect.

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
