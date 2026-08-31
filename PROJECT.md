# WatchLog — system reference

Verified against the live system on **31 August 2026** — read from git,
the database, the Coolify API and HTTP requests at the time of writing,
not from memory.

- **Repo** `github.com/Alkalid-security/Watchlog` (private), branch `main`
- **HEAD** `220cfb5` · 38 commits · 208 files · working tree clean
- **Client** Al-Khalid Security Services (AKSS), contact Salman Mehmood
- **Contract** PKR 100,000 across 4 milestones

---

## 1. What this is

A multi-tenant SaaS that reads the CCTV recorder a business already owns,
filters the noise, and reports what happened — a daily summary on
WhatsApp plus a portal.

The architecture decision everything follows from: **the connection only
goes outward.** No port forwarding, no VPN, no inbound route. The
recorder's credentials never leave the building, and video is never
uploaded — only event records and a still per event that passed the
filter.

There are now **two ways to connect a site**:

- **Agent** — a small program on a Windows PC on the site LAN. Full
  experience: on-site false-alarm filtering, capability detection.
- **Recorder-push (PC-free)** — for a site with no always-on PC, the
  recorder itself POSTs events to a cloud bridge. Still outbound-only.
  No edge filtering (the "lite" path).

---

## 2. Tech stack

| Piece | Technology | Why |
|---|---|---|
| Site agent | Python 3, `requests` | Runs on any Windows PC on the LAN. Frozen to a 16 MB exe with PyInstaller. |
| False-alarm filter | YOLOv8n via **onnxruntime** (torch-free), fails open | Runs on the site machine. No frame is sent to a cloud model. Shipped exe excludes it and reports everything until the model is added. |
| Recorder drivers | Hikvision ISAPI, Dahua CGI, ONVIF (SOAP + WS-Discovery) | The protocol families covering most hardware in Pakistan. |
| Capability probe | Read-only queries of each recorder's analytics config | Detects what analytics a recorder supports / has active. |
| Local spool | SQLite | Survives an internet outage; events send when the line returns. |
| Push bridge | Python stdlib service on Coolify | Translates a recorder's native alarm POST into `wl_ingest_push`. |
| Backend | Supabase (Postgres 15) | RLS with **no policies** + `SECURITY DEFINER` functions — authorisation lives in Postgres. |
| Retention | `pg_cron`, nightly | Per-tier snapshot pruning (7/30/90 days). |
| Portal | Next.js 15 static export, nginx | No Node server in production; the browser talks to Supabase. |
| Marketing site | WordPress 6 / PHP 8.3, custom theme | Client can edit copy without a developer. |
| Reporting | Evolution API (WhatsApp) live; SendGrid (email) unconfigured | WhatsApp is a first-class channel here. |
| Hosting | Coolify 4.1.2 on `161.97.175.15`, Docker, Traefik, Let's Encrypt | All three apps build from this git repo. |

---

## 3. Live system

| Service | URL | Coolify resource | Builds from |
|---|---|---|---|
| Customer portal | https://watchlog.161.97.175.15.sslip.io | `watchlog-portal-git` | `/portal`, Dockerfile |
| Marketing site | https://watchlogsite.161.97.175.15.sslip.io | `watchlog-website-git` | `/deploy`, docker-compose |
| Recorder-push bridge | https://watchlog-push.161.97.175.15.sslip.io | `watchlog-push-bridge` | `/prototype/bridge`, Dockerfile |

All three `running:healthy`, on `main`, with real Let's Encrypt certs.
The old agent viewer (:18080) has been **retired** (it read the anon
functions closed by migration 0010).

**Known gaps:** auto-deploy webhooks are not enabled, so a push does not
yet trigger a rebuild — deploys are triggered through the Coolify API.
The marketing site is on `sslip.io`, not a real domain.

---

## 4. Database — Supabase project `oyvgubyxmjlijiczjona`

**13 tables**, **43 `wl_*` functions**, 15 migrations, all matching git.

| Table | Purpose |
|---|---|
| `tenants` | One customer. Plan + trial state. |
| `sites` | One location + recorder. Timezone, and reported `capabilities`. |
| `agents` | One installed program (or a virtual "recorder-push" agent). |
| `enrollment_codes` | One-time codes, consumed on first run. |
| `cameras` | Discovered per site. |
| `events` | Three clocks; unique on `(tenant_id, dedupe_key)`. |
| `snapshots` | Incident stills (bytea), pruned by plan. |
| `memberships` | Who belongs to which tenant, with a role. |
| `invitations` | Token-based, single-use, expiring team invites. |
| `report_recipients` | Who gets the daily summary, per channel. |
| `report_deliveries` | Delivery proof + the idempotency rule. |
| `push_sources` | Per-site recorder-push token → virtual agent. |
| `schema_migrations` | Supabase's own. |

### The security model, in one paragraph

Every table has RLS enabled with **no policy granting anon anything**.
The agent (and the recorder-push path) reach the database only through
`SECURITY DEFINER` functions that authenticate by agent key / push token
inside the function body — which is why `wl_enroll`, `wl_heartbeat`,
`wl_sync_cameras`, `wl_ingest_events`, `wl_ingest_push` and
`wl_sync_capabilities` are granted to `anon`. Everything a human reads
goes through `wl_portal_*` / `wl_*`, granted to `authenticated` only and
scoped through `wl_my_tenant()`. An anon cross-tenant leak found on
29 Aug (`wl_fleet` returned every tenant's rows) was closed by migration
0010; the isolation gate in `prototype/tests/` is the regression test and
is proven to fail if it reopens.

---

## 5. Repository layout — 208 files

```
prototype/          agent, drivers, bridge, database, tests
  agent/            watchlog_agent.py, vision.py, drivers/, setup_wizard.py
  drivers/          hikvision.py, dahua.py, onvif_driver.py, mock.py
                    (probe, list_channels, stream_events, get_snapshot,
                     capabilities, + hikvision.configure_push)
  bridge/           push_bridge.py + Dockerfile  (recorder-push translator)
  reporter/         daily_report.py
  supabase/         15 migrations, 0001 → 0015
  installer/        Inno Setup + service registration + one-click launcher
  tests/            5 suites (below)
  sim/              Hikvision + Dahua protocol simulators
  viewer/           the retired prototype dashboard (kept in git, not deployed)

portal/             Next.js 15 customer portal
deploy/             WordPress image, theme, compose
brand-assets/       generated logo, icons, site photography
design-tokens/      Style Dictionary source
03_Design/          guidelines, site map, pricing, prompts, CONNECTIVITY.md
tools/              asset builders, make_installer.ps1
```

**Not committed, deliberately** (root `.gitignore`): `_memory/` (internal
commercial + security notes — this repo is in the client's GitHub org),
`.env`, keys, `node_modules`, build output, `dist-installer/`, `_drop/`.

---

## 6. Tests — all passing (44 cases)

```bash
python prototype/tests/test_tenant_isolation.py   # 9  — the M4 isolation gate
python prototype/tests/test_vision_filter.py      # 12 — false-alarm filter
python prototype/tests/test_team_and_trial.py     # 12 — team + trial
python prototype/tests/test_push_bridge.py        # 5  — recorder-push parser
python prototype/tests/test_capabilities.py       # 3  — analytics probe
```

The isolation gate runs against the live deployment over HTTP, refuses to
run on a single-tenant DB, and is verified to go red when a leak reopens.

---

## 7. What is built and proven

- **Connectivity, on real hardware.** The client's Dahua `DH-XVR1B08-I`
  (at `SM-HP`) enrolled with a one-time code and pushed 9 real events +
  8 snapshots. Outbound-only, no IP/port-forward.
- **Recorder-push (PC-free) mode, live.** Hikvision alarm → deployed
  bridge → `wl_ingest_push` → portal. Proven end to end.
- **Analytics capability probe.** Reads each recorder's supported/active
  analytics (Dahua proven vs sim; Hikvision written, unvalidated),
  reports to the portal. Read-only.
- **False-alarm filter** (ONNX YOLOv8n, fails open) — filtering logic
  proven; model not yet shipped in the exe.
- **Daily WhatsApp summary** — idempotent, delivery-logged; live instance
  verified. Not yet scheduled; no live send done.
- **Multi-tenant portal** — overview, health, incidents, events,
  analytics, team + roles, trial. Isolation enforced + gated.
- **Per-tier snapshot retention** — nightly via pg_cron, proven.
- **Branded installer** (Inno) + background service + power hardening —
  authored; needs Inno to compile + a real machine to test the boot cycle.
- **Marketing site** — 10 designed pages, real photography, SEO/OG,
  working CTAs.

---

## 8. Milestone status (against the signed scope)

| | Status |
|---|---|
| **M1** MVP | Agent, drivers, filtering, sync, dashboard, WhatsApp summary built; connectivity proven on real Dahua. **Field validation not signed** — the agent stopped after ~3 min on `SM-HP` and synced 0 cameras (needs the log). Filter model not yet shipped/tuned. |
| **M2** SaaS scaling | Multi-tenancy, onboarding, team management, trial, marketing site — built. Recorder-push adds a PC-free path. |
| **M3** Billing & email | SendGrid written, unconfigured. Switch **not started** — needs merchant creds. Per-tenant channel prefs built. |
| **M4** Polish & handoff | Isolation gate built + passing. Installer authored. Retention done. Runbooks + live-hardware handoff outstanding. |

---

## 9. Open items

**Needs the client**
1. `SM-HP` `agent.log` + Task Scheduler state — fix the 3-min stop + 0-camera sync. **Top blocker.**
2. One real Hikvision + one real ONVIF unit to prove those drivers.
3. A domain (watchlog.pk?) to leave sslip.io.
4. Go-ahead for one live WhatsApp test.
5. SendGrid + Switch credentials (M3).

**Needs no one (buildable now)**
6. Portal analytics screen (show capabilities + opt-in enable toggles).
7. NVR-log backfill (close the outage gap).
8. Portal recorder-push token screen + installer download loop.
9. Ship + tune the ONNX filter model.
10. Schedule the daily report (once the live-send OK is given).

**Housekeeping — do soon**
11. **Rotate the Coolify API token and the Supabase DB password** — both
    were pasted in chat.

**Paid scope expansion (new conversation)**
12. Live-vision AI tier; the "WatchLog Bridge" appliance (Linux/ARM build);
    code-signing certificate; billing.

---

## 10. Environment & credentials

Values are **not** reproduced here — they live in `projects/watchlog/.env`
(gitignored). Inventory only:

- `.env` — Supabase (URL, publishable + secret[empty] keys, DB creds),
  WordPress DB + admin, portal demo login, Coolify URL + API token.
- `portal/.env.local` — the two public `NEXT_PUBLIC_SUPABASE_*` values.
- **Borrowed from other engagements** (not in WatchLog's .env): Evolution
  API (WhatsApp) creds and the GitHub PAT live in
  `alkhalid-security-portal/`.
- **Absent everywhere:** SendGrid key, Switch merchant creds.
- SSH: `alkhalid-security-portal/ak_key.pem` → `root@161.97.175.15`.

Internal working notes, the full narrative history, the finalization plan,
and the AKSS preview login are in `_memory/` (STATE.md,
FINALIZATION_PLAN.md) — deliberately outside this repo.
