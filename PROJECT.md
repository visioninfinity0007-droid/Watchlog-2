# WatchLog — system reference

Verified against the live system on **29 August 2026**. Everything below
was read from git, the database, the Coolify API or an HTTP request at
the time of writing — none of it is from memory.

- **Repo** `github.com/Alkalid-security/Watchlog` (private), branch `main`
- **HEAD** `20e5716` · 26 commits · 184 tracked files · working tree clean
- **Client** Al-Khalid Security Services (AKSS), contact Salman Mehmood
- **Contract** PKR 100,000 across 4 milestones

---

## 1. What this is

A multi-tenant SaaS that reads the event log of CCTV recorders a customer
already owns, discards false alarms on site, and sends a daily summary.

The architecture decision everything else follows from: **the connection
only goes outward.** No port forwarding, no VPN, no inbound route. The
recorder's credentials never leave the customer's building, and video is
never uploaded — only event records and a single still per event that
passed the filter.

---

## 2. Tech stack

| Piece | Technology | Why |
|---|---|---|
| Site agent | Python 3, `requests` | Runs on any Windows PC on the customer's LAN. Frozen to one exe with PyInstaller. |
| False-alarm filter | YOLOv8n via `ultralytics` + Pillow | Runs **on the site machine**. Frames are never sent to a cloud model. |
| Recorder drivers | Hikvision ISAPI, Dahua CGI, ONVIF (SOAP + WS-Discovery) | The three protocol families covering most hardware sold in Pakistan. |
| Local spool | SQLite | Survives an internet outage; events send when the line returns. |
| Backend | Supabase (Postgres 15) | RLS with **no policies** plus `SECURITY DEFINER` functions — authorisation lives in Postgres, not in a server we would have to secure. |
| Portal | Next.js 15, static export, nginx | No Node server in production. The browser talks to Supabase directly. |
| Marketing site | WordPress 6 / PHP 8.3, custom theme | Client can edit copy without a developer. |
| Reporting | Evolution API (WhatsApp), SendGrid (email, unconfigured) | WhatsApp is a first-class channel in this market, not an afterthought. |
| Design tokens | Style Dictionary | One source → CSS, SCSS, JS, JSON. Site and portal cannot drift apart. |
| Hosting | Coolify 4.1.2 on `161.97.175.15`, Docker, Traefik, Let's Encrypt | All three apps build from this git repo. |

---

## 3. Live system

| Service | URL | Coolify resource | Builds from |
|---|---|---|---|
| Customer portal | https://watchlog.161.97.175.15.sslip.io | `watchlog-portal-git` | `/portal`, Dockerfile |
| Marketing site | https://watchlogsite.161.97.175.15.sslip.io | `watchlog-website-git` | `/deploy`, docker-compose |
| Recorder-push bridge | https://watchlog-push.161.97.175.15.sslip.io | `watchlog-push-bridge` | `/prototype/bridge`, Dockerfile |

All three `running:healthy`, all on `main`, all with real Let's Encrypt
certificates.

**Marketing site is production-ready** as of 29 Aug: working CTAs (every
button reaches the portal's /signup/ or /login/), favicon, meta, OG card,
JSON-LD, a differentiation ledger and an honest trust band, working mobile
nav. **Known gap:** auto-deploy webhooks are not enabled, so a push does
not yet trigger a rebuild — deploys are triggered through the Coolify API. And the viewer's data calls now fail by design: it read the
anon-accessible functions that were closed in migration 0010. It has now been retired.

**Recorder-push (PC-free) mode is live:** a site with no always-on PC can
have its recorder POST events straight to the bridge above (token per
site), which forwards to `wl_ingest_push`. Proven end to end with a
Hikvision alarm. Dahua/ONVIF push is model-dependent — validate per unit.

---

## 4. Database — Supabase project `oyvgubyxmjlijiczjona`

**12 tables**, **36 `wl_*` functions**, 12 migrations, all matching git.

| Table | Purpose |
|---|---|
| `tenants` | One customer. Carries plan and trial state. |
| `sites` | One physical location with one recorder. Has its own timezone. |
| `agents` | One installed program. Authenticates with a hashed key. |
| `enrollment_codes` | One-time codes, consumed on first run. |
| `cameras` | Discovered per site. |
| `events` | Three clocks: `device_ts`, `agent_ts`, `received_at`. Unique on `(tenant_id, dedupe_key)`. |
| `snapshots` | Incident stills as bytea, retention by plan. |
| `memberships` | Who belongs to which tenant, with a role. |
| `invitations` | Token-based, single-use, expiring. |
| `report_recipients` | Who gets the daily summary, and on which channel. |
| `report_deliveries` | Proof of delivery, and the idempotency rule. |
| `schema_migrations` | Supabase's own. |

Current data: 2 tenants, 2 sites, 10 agents, 8 cameras, 2,048 events,
786 snapshots.

### The security model, in one paragraph

Every table has RLS enabled with **no policy granting anon anything**.
The agent reaches the database only through `SECURITY DEFINER` functions
that authenticate it by agent key inside the function body — which is why
`wl_enroll`, `wl_heartbeat`, `wl_sync_cameras` and `wl_ingest_events`
remain granted to `anon` on purpose. Everything a human reads goes
through `wl_portal_*`, granted to `authenticated` only and scoped through
`wl_my_tenant()`.

**This was broken until 29 August.** `wl_fleet` and five other read
functions were anon-callable and returned every tenant's rows —
reproduced over HTTP with nothing but the public key. `wl_prune_snapshots`,
which deletes rows, was exposed the same way. Migration `0010` closed it.
The isolation gate in `prototype/tests/` is the regression test.

---

## 5. Repository layout — 184 files

```
prototype/          the agent, drivers, database, tests      45 files
  agent/            watchlog_agent.py, vision.py, drivers/, setup_wizard.py
  drivers/          hikvision.py, dahua.py, onvif_driver.py, mock.py
  supabase/         12 migrations, 0001 → 0012
  reporter/         daily_report.py
  tests/            3 suites
  viewer/           the prototype dashboard
  sim/              fake Hikvision and Dahua devices for offline work

portal/             Next.js 15 customer portal                23 files
deploy/             WordPress image, theme, compose           38 files
  wordpress/themes/watchlog/   the marketing site theme
brand-assets/       generated logo, icons, site photography   56 files
design-tokens/      Style Dictionary source                    8 files
03_Design/          guidelines, site map, pricing, prompts    10 files
tools/              asset builders                             3 files
```

**Not committed, deliberately:** `_memory/` (internal commercial and
security notes — this repo is in the client's GitHub org), `.env`, keys,
`node_modules`, build output, `_drop/` (raw supplied photography).

---

## 6. Commit history — this engagement

| Commit | What |
|---|---|
| `33bebd4` | Brought the whole project into the repo. It held only the agent; the portal and brand had no source of truth at all. |
| `6901e95` | Credential-free WordPress compose, so Coolify can build the site from git. |
| `da4f73c` | **Closed the anon cross-tenant leak.** |
| `5e3084c` | M4 tenant isolation QA gate, 9 cases. |
| `c209ad7` | M1 YOLOv8n false-alarm filtering, 12 tests. |
| `91e538c` | M1 daily WhatsApp summary with delivery records. |
| `b49a6c1` | M2 team management and trial state, 12 tests. |
| `288321f` | Built the marketing site instead of shipping stock WordPress. |
| `30a64b5` | Made the theme actually deploy — it was baked in but never reached the site. |
| `52451f9` | Designed the home page: icons, product visuals, composition. |
| `844d494` | Fixed a WCAG failure on the header button (2.91:1). |
| `8ee8c3f` | Designed the inner pages, six bespoke templates. |
| `c07c619` | Wired photography in, layout still standing without it. |
| `20e5716` | Added the 14 site photographs. |

---

## 7. Tests — all passing

```bash
python prototype/tests/test_tenant_isolation.py   # 9 cases, the M4 gate
python prototype/tests/test_vision_filter.py      # 12 cases
python prototype/tests/test_team_and_trial.py     # 12 cases
```

The isolation gate runs against the **live** deployment over HTTP as anon
and as a signed-in user, because a grant is only closed if the endpoint
says so. It refuses to run on a single-tenant database rather than
passing vacuously. **It was verified to fail**: re-granting `wl_fleet` to
anon turns case 1 red and exits non-zero.

---

## 8. Environment and credentials

**Values are not reproduced here.** They live in `projects/watchlog/.env`,
which is gitignored. This is the inventory of what exists and what each
thing is for.

### `projects/watchlog/.env` — 22 variables

| Variable | Purpose |
|---|---|
| `SUPABASE_PROJECT_REF` | `oyvgubyxmjlijiczjona` |
| `SUPABASE_URL` | REST and auth endpoint |
| `SUPABASE_PUBLISHABLE_KEY` | Public by design; baked into the browser bundle |
| `SUPABASE_SECRET_KEY` | **Present but EMPTY.** Never needed — direct Postgres access is strictly more powerful |
| `SUPABASE_DB_USER` / `_PASSWORD` / `_HOST` / `_PORT` / `_NAME` | Direct Postgres. Used for migrations, the reporter and test fixtures |
| `WORDPRESS_DB_NAME` / `_USER` / `_PASSWORD` / `_ROOT_PASSWORD` | Marketing site database |
| `WORDPRESS_HOST` / `_PORT` | Legacy from the localhost-only deployment |
| `WORDPRESS_ADMIN_USER` / `_EMAIL` / `_PASSWORD` | wp-admin login (`wladmin`) |
| `PORTAL_DEMO_EMAIL` / `_PASSWORD` | `demo@watchlog.test`, used by two test suites |
| `COOLIFY_URL` / `COOLIFY_API_TOKEN` | `161.97.175.15:8000`. **Pasted in chat — should be revoked** |

### `portal/.env.local` — 2 variables

`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`.
Build-time only; both are public by design. Coolify holds its own copy as
build arguments.

### Borrowed from other engagements

These are **not** in WatchLog's `.env` and are read from elsewhere in the
workspace. That is a boundary worth being deliberate about.

| What | Where it lives | Used for |
|---|---|---|
| `EVOLUTION_API_URL` / `_API_KEY` / `_INSTANCE_NAME` | `alkhalid-security-portal/.env.local` | WhatsApp delivery. Instance **"Alkhalid Security"**, number `923312103378`, state `open` |
| `AKSS_N8N_API_TOKEN` | same | n8n on the same host, not yet used |
| GitHub PAT | `alkhalid-security-portal/_memory/credentials.md` | Pushing this repo. Owner `Alkalid-security`, admin + push |

### Not present anywhere in the workspace

| Missing | Blocks |
|---|---|
| `SENDGRID_API_KEY` | M3 email reports. Code is written; records `skipped`, never `sent` |
| Switch merchant credentials | M3 billing. Cannot be built or tested |
| NVR credentials for the two MVP sites | **M1 sign-off.** No driver has met real hardware |

### SSH

`projects/alkhalid-security-portal/ak_key.pem` → `root@161.97.175.15`.

---

## 9. Milestone status

| | Status |
|---|---|
| **M1** MVP | Agent, drivers, filtering, sync, dashboard, WhatsApp summary all **built**. **Field validation not done** — no driver has met a real recorder. Not signable. |
| **M2** SaaS scaling | Multi-tenancy, onboarding, team management, trial tracking, marketing site all **built**. |
| **M3** Billing & email | SendGrid **written, unconfigured**. Switch **not started** — needs merchant credentials. |
| **M4** Polish & handoff | Isolation gate **built and passing**. NSIS installer and runbooks **not started**. Live-hardware handoff blocked with M1. |

---

## 10. Open items

**Needs the client**

1. NVR credentials and network access for both MVP sites — blocks M1
2. Three to five nights of real running, to tune the filter — blocks M1
3. Which two sites and which ten cameras — never specified
4. SendGrid API key — M3
5. Switch merchant credentials — M3
6. Permission to send one live WhatsApp test to `923312103378`

**Needs no one**

7. NSIS installer (M4)
8. Runbooks (M4)
9. Enable Coolify auto-deploy webhooks
10. Retire the viewer or move it to authenticated access

**Housekeeping**

11. **Revoke the Coolify API token** — it was pasted in chat
12. Rotate the Supabase database password — also pasted in chat
13. `ultralytics` pulls in torch (>1 GB), which makes a one-file installer
    impractical. Moving to `yolov8n.onnx` behind `onnxruntime` (~15 MB)
    would fix that; `vision.py` is the only file that changes
