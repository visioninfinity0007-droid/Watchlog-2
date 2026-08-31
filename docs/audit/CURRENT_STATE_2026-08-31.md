# WatchLog — Live System Audit

**Date:** 2026-08-31 · **Auditor:** Vision Infinity (automated live inspection)
**Method:** direct inspection of GitHub, Coolify API, the live Coolify host (SSH), the
Supabase Postgres database, PostgREST/Auth over HTTP, and the running services.
**Every assertion below is backed by evidence gathered at audit time — not from memory or docs.**

> Status vocabulary: **DELIVERED** (implemented + deployed + operational + tested +
> aligned to signed scope) · **PARTIAL** · **MISSING** · **BROKEN** · **UNVERIFIED** ·
> **CLIENT-DEPENDENT** · **OUT-OF-SCOPE**.
> `DELIVERED` is *not* claimed merely because code exists.

**No secret values appear in this document.** Credentials are described by name and status only.

---

## 0. Executive summary

WatchLog has a **solid, genuinely multi-tenant backend and three live services**, and its
**security isolation is verified** (the M4 isolation gate passes 9/9 live, no cross-tenant
leak was found). But measured against the **signed 4-milestone scope**, it is **not
acceptance-complete**:

- **M1's three headline gates fail or are undelivered:** the **YOLOv8n AI false-alarm filter
  is not in any shipped binary** (runtimes excluded at build, no model file exists — proven by
  a binary strings scan); **field validation fails** (the only real recorder produced **9 events
  across 3 minutes then stopped, synced 0 cameras**); and the **daily WhatsApp report is never
  scheduled and has never been sent** (0 delivery rows, no n8n workflow).
- **M3 (Switch billing) is NOT STARTED** — no gateway code, no payment tables, no webhook. SendGrid
  email exists but sends **plain text, not the contractual branded HTML**.
- **M4's installer is the wrong technology** (Inno Setup/PowerShell, shipped as a **ZIP**, not the
  contractual **NSIS**), **unsigned and untested**; the **required runbooks do not exist**.
- **No production domain** — everything is on throwaway `*.sslip.io`, baked into the installer and
  WordPress theme.

Extra engineering exists beyond scope (recorder-push mode, analytics-capability probe, per-tier
retention) — real value, but it **does not count toward contractual completion**.

**Four delivery scores** (§V): Engineering **~68%** · Live-deployment **~58%** ·
**Signed-scope ~37%** · Handoff **~28%**.

---

## A. Git / repository

| Fact | Value (evidence) |
|---|---|
| Repository | `github.com/Alkalid-security/Watchlog` (private) — GitHub API `private:true` |
| Default branch | `main` |
| HEAD (local == remote) | **`ce651dd5f3f639b0c1ef994725c9916958588fa0`** — verified by `git fetch` FETCH_HEAD == local HEAD, 0/0 divergence |
| Commits · tracked files | **39 commits · 208 files** · working tree clean on `main` |
| Latest commit | `ce651dd` "Docs: refresh PROJECT.md…" — 2026-08-31 22:26 +0500 |
| Tags / releases | **none** (0 tags) |
| Branches | only `main` (unprotected — GitHub `protected:false`) |
| Open PRs | **none ever** (GitHub `/pulls?state=all` → empty) |
| GitHub Actions / CI | **none** (0 workflows, 0 runs) |
| Branch protection | not enabled |
| GitHub webhooks | **none** → no push-triggered deploy (see §R) |
| Deploy keys | 1 — "Coolify deploy key (watchlog portal)", read-only |

**Repo tree (208 tracked files):** `prototype/` 60 (agent, drivers, discovery, spool, vision,
reporter, bridge, installer, supabase, tests, sim, viewer[retired]), `deploy/` 43 (WordPress),
`brand-assets/` 56, `portal/` 23 (Next.js), `03_Design/` 11 (docs), `design-tokens/` 8, `tools/` 4,
root 3.

**Component → path:** agent `prototype/agent/watchlog_agent.py` · drivers `prototype/agent/drivers/`
· discovery `prototype/agent/discover.py`,`wsdiscovery.py` · AI filter `prototype/agent/vision.py` ·
spool `prototype/agent/spool.py` · migrations `prototype/supabase/migrations/0001…0015` · reporter
`prototype/reporter/daily_report.py` · portal `portal/` · installer `prototype/installer/` · push
bridge `prototype/bridge/push_bridge.py` · WordPress `deploy/wordpress/` · tests `prototype/tests/` ·
deploy config `deploy/wordpress.coolify.yml`, `portal/Dockerfile`.

**Hygiene:** **0** TODO/FIXME/HACK/XXX in the tracked tree. `.gitignore` correctly excludes `.env`,
`*.pem`, `*.key`, `_memory/`, `AGENTS.md`, `router.md`, build output — appropriate because the repo
lives in the **client's** GitHub org. No secret-shaped file is tracked; `.env` was never committed
(`git log --all -- '**/.env'` empty). `.gitattributes` pins CRLF for installer scripts.
Minor: `0001_prototype_schema.sql:4` still self-labels **"THROWAWAY … NOT the delivered M1 schema"**
yet is the base of the live schema chain (stale label).

---

## B. Coolify (live)

Host `161.97.175.15:8000` (Coolify 4.1.2), `/api/health` = 200. **6 applications, 3 services.**
The 3 WatchLog apps (all `running:healthy`, all from `git@github.com:Alkalid-security/Watchlog.git`
@`main`, all real Let's Encrypt TLS):

| App | UUID | Build | Dir | Domain | Deployed commit (image tag) | Restarts |
|---|---|---|---|---|---|---|
| watchlog-portal-git | `u10fp0bsvkwjmj8kpty5zk40` | Dockerfile | `/portal` | https://watchlog.161.97.175.15.sslip.io | `33bebd4` (built 08-29) | 0 |
| watchlog-push-bridge | `ixi45m3km4kp8hrnv0qzh26x` | Dockerfile | `/prototype/bridge` | https://watchlog-push.161.97.175.15.sslip.io | `b6cfb59` | 0 |
| watchlog-website-git | `ez7677oub6mo1c2gwukaynuk` | compose | `/deploy` | https://watchlogsite.161.97.175.15.sslip.io | `5f9b990` | 0 |

**Deployment drift — NONE functionally.** Deployed commits are older than HEAD `ce651dd`, but
`git diff <deployed>..HEAD -- <build-dir>` is **empty** for all three and no commit after each
deployed commit touched its build directory. So each running container is byte-identical to HEAD for
its own source. (This does mean the containers were last built 08-29→08-31 and never redeployed since,
which is fine only because nothing in their paths changed.)

**Env vars (names only):** portal → `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
(buildtime, correct for static export). Bridge → `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (runtime).
Website → `WORDPRESS_DB_*`, `SERVICE_FQDN/URL_WORDPRESS`. **No missing required env** observed.

**Healthchecks:** `health_check_enabled=false` on all three (the "healthy" state is Docker's, not an
app healthcheck). **Restart counts 0** — no crash loops. **Runtime logs clean** — portal/wordpress show
only 30-second healthcheck traffic (200 / 301-to-https), no errors; bridge shows opportunistic bot
probes (`/.env`, `/.git/config`) which it answers with its harmless health string (see §F, not a leak).

**Other Coolify resources (not WatchLog, context only):** apps `AKSS Kimi Browser Automation`, `OCR`,
`alkhalid-security-portal`; services `evolution-api` (WhatsApp, healthy), `n8n-with-postgres-and-worker`
(healthy), `Alkhalid Website`. Host uptime 99 days, **load average ~11 (high)**.

---

## C. Marketing website (live HTTP)

`https://watchlogsite.161.97.175.15.sslip.io` — **HTTP 200**, Apache/2.4.67, PHP 8.3.31, WordPress.
http→https redirect works (302). First-byte ~5.5 s (WordPress on a loaded host).

- **SEO/social:** `<title>`, meta description, `og:type/title/description/image`, `<link rel=canonical>`,
  and JSON-LD **all present**. OG image served from the theme.
- **Pages present:** home + `features`, `how-it-works`, `pricing`, `setup`, `who-its-for`, `contact`,
  **`privacy`, `terms`** (legal pages exist).
- **CTAs point to the live portal:** `/signup` and `/login` link to `watchlog.161.97.175.15.sslip.io`.
  **No `href="#"`.**
- **404 behavior:** correct (random path → 404).
- **BROKEN (minor):** `robots.txt` advertises `Sitemap: …/wp-sitemap.xml`, but **`wp-sitemap.xml`
  returns 404** — the sitemap is disabled/blocked.
- **Domain:** everything is on `*.sslip.io` (29 internal sslip.io links on the homepage). **FINAL DOMAIN = MISSING.**

**WordPress:** version reported via generator/wp-json; PHP 8.3.31; custom theme `watchlog`. Content lives
in the MariaDB volume + uploads (persistent Docker volumes), **not reproducible from git** (theme is in
git; page content is DB-resident). `X-Powered-By: PHP/8.3.31` is exposed (minor hardening).

---

## D. Customer portal

**Code (Next.js 15, static export, nginx):** exactly **5 routes** — `/`, `/login`, `/signup`,
`/onboarding`, `/dashboard` (~640 LOC). **Production build RAN CLEANLY at audit time** (`npm run build`
→ exit 0, `out/` = 41 files, 6 route entries). Served over HTTPS with security headers
(`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `X-Robots-Tag:
noindex`). Auth gating is **client-side only** — real authorization is Postgres RLS (by design).
Data access is via **4 RPCs only** (`wl_my_tenant`, `wl_bootstrap_tenant`, `wl_portal_overview`,
`wl_portal_snapshot`); **no direct table reads**.

**Capability presence (UI):**

| Present | Partial | Absent |
|---|---|---|
| signup, login, onboarding, dashboard/overview, enrollment-code display, agent status, incidents (snapshots), event history | sites (read-only inline), NVR setup (delegated to agent), analytics (KPI tiles only) | **add-site, site detail, NVR connection test, cameras list, discovered cameras, reports, report-delivery history, recipients, channel preferences, capabilities display, team, invitations, roles, trial status, billing, subscription, payment history, profile/settings, recorder-push setup** |

**Installer download is a STUB** — `onboarding/page.js:110-117` fires a browser `alert("The signed
installer is not published yet…")`. **This dead-ends the self-serve onboarding.**

**Onboarding journey (as coded):** signup → (email confirm) → login → onboarding creates
tenant+site+code via `wl_bootstrap_tenant` → shows enrollment code → **[BLOCKED at installer download]**
→ user must obtain `watchlog-agent.exe` out-of-band, run it on a site PC, enter recorder credentials
**into the agent (not the portal)**, transcribe the code → dashboard updates. **Not self-service.**

---

## E. Supabase / database (live)

Project ref `oyvgubyxmjlijiczjona` · `https://oyvgubyxmjlijiczjona.supabase.co` ·
**PostgreSQL 17.6** · region ap-southeast-1 · free tier.

**13 tables** (RLS + row counts at audit time):

| Table | RLS | Policies | tenant_id | Rows |
|---|---|---|---|---|
| tenants | ON | 1 | — | 2 |
| sites | ON | 1 | Y | 2 |
| agents | ON | 1 | Y | 17 |
| cameras | ON | 1 | Y | 12 |
| enrollment_codes | ON | 1 | Y | 15 |
| events | ON | 1 | Y | **2070** |
| snapshots | ON | 1 | Y | **803** (~2 MB total) |
| memberships | ON | 1 | Y | 2 |
| invitations | ON | 1 | Y | 0 |
| report_recipients | ON | 1 | Y | 1 |
| report_deliveries | ON | 1 | Y | 0 |
| push_sources | ON | 1 | Y | 3 |
| schema_migrations | **OFF** | 0 | — | 9 |

**43 `wl_*` functions.** 41 are `SECURITY DEFINER`; **all SECURITY DEFINER functions pin
`search_path = public`** (no mutable-search-path issue). The 2 non-definer functions
(`wl_dedupe_key`, `wl_plan_retention_days`) are pure `immutable` helpers (no table access). One
view, `v_agent_fleet`, is `security_invoker=true` (safe — runs as caller).

**anon-executable functions** (must self-authenticate): the agent API (`wl_enroll`, `wl_heartbeat`,
`wl_ingest_events`, `wl_sync_cameras`, `wl_sync_capabilities` — authenticate by agent key inside),
`wl_ingest_push` (push-token auth), the onboarding trio (`wl_bootstrap_tenant`, `wl_add_site`,
`wl_issue_code` — **each verified to self-guard**: `auth.uid()`/`wl_is_member` checks), and pure
helpers. **None grants privileged access to anon** — confirmed by reading each body.

**Billing-authorization check (CRITICAL, per audit definition):** `wl_set_plan(p_plan, p_status)`
does `wl_require_role(['owner'])` then updates the caller's own `tenants.plan` and
`subscription_status` to any value — **a tenant owner can self-assign any plan / subscription status
with no payment, no Switch, no webhook, no admin gate.** Live evidence: the AKSS tenant is currently
`plan=starter, subscription_status=active`. Impact **today** is limited (the only plan-gated behavior
is snapshot-retention days), but **this must be locked to service-role/webhook before billing ships**.
See §F/§W.

**Migration ledger drift:** `schema_migrations` records only **0001–0009** (last applied 2026-08-28),
but **0010–0015 are live** (their tables/functions/policies all exist). Migrations 0010–0015 were
applied out-of-band and never logged — the ledger is **not authoritative**. Git has all 15 files; the
live *objects* match git, only the *ledger* is stale.

---

## F. RLS / tenant security (live tests)

**The M4 isolation gate (`prototype/tests/test_tenant_isolation.py`) — RAN AT AUDIT TIME against
`https://oyvgubyxmjlijiczjona.supabase.co`, read-only: 9/9 PASSED, exit 0.**

1. anon cannot call privileged functions (regression for the old `wl_fleet` leak) — all 9 denied ✅
2. anon cannot read any table directly — 8 tables returned nothing ✅
3. signed-in overview contains only the caller's tenant ✅
4. signed-in user cannot read the other tenant's rows from any table ✅
5. `wl_is_member` false for a foreign tenant ✅
6. user cannot fetch the other tenant's incident still ✅
7. agent API reachable but rejects invalid credentials ✅
8. destructive functions (`wl_prune_snapshots`) denied to anon **and** normal user ✅
9. publishable key alone yields no tenant data ✅

**Supplementary anon HTTP probes I ran (the 4 tables carrying tokens/PII that the gate does not
cover):** `push_sources`, `report_recipients`, `invitations`, `report_deliveries` — each returns
**HTTP 200 `[]`** to anon (empty). Their RLS policies are attached to the `public` role but the
`USING` clause is `wl_is_member(tenant_id)`, which is false for anon → 0 rows. Privileged RPCs
(`wl_push_sources`, `wl_set_plan`, `wl_issue_push_token`) return **401 permission denied** to anon.
The `v_agent_fleet` view returns `[]` to anon (security_invoker). **No cross-tenant leak found.**

**Hardening notes (not leaks):** (a) the 4 `public`-role policies should be tightened to
`authenticated` for consistency with the other 8 — today they rely solely on the `USING` clause;
(b) see §G for `schema_migrations`.

---

## G. Database advisors (advisor-equivalent)

> The official Supabase Security/Performance Advisor needs a Supabase **management/access token**,
> which is **not present** in the workspace. The following are the equivalent checks run directly
> against the catalog.

**Security**
- 🟠 **MEDIUM — `schema_migrations`: RLS disabled AND `anon` holds SELECT + INSERT.** Anon can read
  migration filenames/hashes and **insert rows** over PostgREST. No tenant data, but anon should have
  no write on any table. Fix: `REVOKE … FROM anon, public` (and/or enable RLS with no policy).
  (Supabase's Security Advisor flags this as "RLS disabled in public".)
- ✅ SECURITY DEFINER functions without `search_path`: **none**.
- ✅ SECURITY DEFINER views: **none** (`v_agent_fleet` is security_invoker).
- ✅ Extensions in `public`: **none**.

**Performance (LOW — negligible at current scale)**
- 12 foreign keys without a covering index (`agents_site_id_fkey`, `events_camera_id_fkey`,
  `snapshots_site_id_fkey`, `snapshots_camera_id_fkey`, `push_sources_*`, `enrollment_codes_*`,
  `invitations_*`, `report_recipients_site_id_fkey`). All tables are small; add indexes before scale.
- ✅ Every table has a primary key. 37 indexes total.

---

## H. Site agent

Python agent (`AGENT_VERSION 0.2.0-prototype`), PyInstaller one-file exe.

- **Enrollment / secret:** one-time code → `wl_enroll` → server mints `agent_key`; stored **plaintext**
  in `C:\ProgramData\WatchLog\agent_state.json` (chmod 0600 only on non-Windows — **no ACL hardening on
  Windows**). Key is SHA-256-hashed **server-side** (in `wl_enroll`, DB). Key masked in all logs.
- **NVR credentials:** stored **plaintext** in `watchlog.ini` (Program Files). File warns it holds the
  password. **No secret is ever logged** (verified); auth sent via Digest/Basic, never in URLs.
- **Architecture:** outbound-only for all cloud + NVR traffic (client-initiated long-poll for event
  streams). Transient UDP bind during WS-Discovery only (~4 s, receives replies to its own probe) —
  not a service listener. **No inbound path.**
- **Reliability:** SQLite spool (WAL, 200k-row cap) survives uplink loss; **at-least-once** delivery
  (ack after server commit); 2-layer dedup (30 s burst-collapse + server `dedupe_key`); heartbeat 60 s;
  snapshot rate-limited 1/camera/60 s, 2 MB cap; upload batched 200 / 4 MB / 15 s.
- **Gaps:** **no exponential backoff** (fixed 20 s / 15 s); **push-driver events are lost during
  agent/driver downtime** (no historical replay — only the mock polls an overlap window) — likely
  relevant to the SM-HP 3-minute gap; all real drivers hard-code `device_event_id=None` so server
  dedup falls back to site+channel+ts+type; Dahua uses agent capture time as `device_ts`; single-file
  5 MB log roll; `agent_state.json` (plaintext key) **survives uninstall**.
- **Startup:** collector thread self-heals; `run-agent.cmd` restarts 15 s after any exit; SYSTEM
  scheduled task `AtStartup`, RestartCount 999.

---

## I. NVR / hardware validation

**Definitive, from the live DB (agents × events × snapshots):**

| Host | Vendor/Model | Driver | Real? | Events | Snapshots | Window |
|---|---|---|---|---|---|---|
| **SM-HP** | **Dahua DH-XVR1B08-I** | dahua-cgi | **REAL** | **9** | **8** | 08-29 08:45→08:48 (**~3 min**) |
| MKT-Awais | Dahua NVR4208-8P-4KS2 | dahua-cgi | **SIM** (dahua_sim reports this exact model) | ~865 | ~780 | 08-25→08-31 |
| MKT-Awais | Hikvision DS-7608NI-K2/8P | hikvision-isapi | **SIM** (hikvision_sim) | 328 | 10 | 08-25 |
| MKT-Awais | MOCK-NVR-16CH | mock | **SIM** | ~864 | 0 | 08-24→08-25 |
| Recorder push | (virtual) | recorder-push | test | 4 | 2 | 08-31 |

**The only real recorder ever connected is SM-HP's Dahua DH-XVR1B08-I** (a model no simulator emits).
It produced **9 events + 8 snapshots over ~3 minutes, then stopped, and synced 0 cameras.** The 8
cameras on the AKSS site were synced from the **simulator**, not the real XVR. **Hikvision and ONVIF
have never touched real hardware** (all drivers self-declare `verified_against_hardware = False`).
ONLINE agents at audit time: **0**.

**M1 field requirement = 2 sites × 10 cameras.** Reality: **1 real client site** (AKSS Head Office),
**8 simulated cameras**, real XVR **0 cameras synced**, ~3 minutes of real data.

> **M1 FIELD GATE: 🔴 FAIL** — one recorder, 3 minutes, agent stopped, 0 cameras synced, no
> second site, no 10-camera site, no FP/FN tuning. Root-cause needs `C:\ProgramData\WatchLog\agent.log`
> from SM-HP (client-dependent).

---

## J. AI false-alarm filter

- **Implementation** (`vision.py`): classes **exactly person / car / motorcycle** (COCO 0/2/3 — matches
  contract), confidence 0.35, two backends (ultralytics/torch dev path; **`OnnxDetector` = the intended
  shipped path**), **fail-open** (any runtime/model/inference failure keeps the event) — well
  unit-tested (12/12 pass at audit time).
- 🔴 **SHIPPED IN EXE = NO.** Proven three ways: the PyInstaller build **excludes**
  `torch, ultralytics, onnxruntime, numpy, PIL` (`build_exe.ps1`, `watchlog-agent.spec`); **no model
  file exists** anywhere (`**/*.onnx`, `**/*.pt` → 0; `prototype/models/` empty + gitignored); a
  **binary strings scan** of the shipped `watchlog-agent.exe` (16,196,123 bytes) finds
  `onnxruntime`/`torch`/`yolov8` **zero times**. The shipped agent transports **every event unfiltered**.
- 🔴 **FIELD TUNING = NOT DONE.** The one vision test stubs the model and feeds a synthetic solid-colour
  JPEG; the shipped `OnnxDetector` has **zero test coverage**; fixtures are synthetic ("Nothing here is
  real footage"). No false-positive/false-negative measurement against real footage exists.

---

## K. Reporting / WhatsApp (M1: "daily WhatsApp via n8n")

- **Engine (`daily_report.py`) is well-built:** source `wl_daily_report`; **per-site timezone**;
  recipients in `report_recipients`; **idempotent** (partial unique index on sent + pre-send check +
  `on conflict do nothing`); delivery logged to `report_deliveries`; Evolution API (WhatsApp) integration
  present.
- 🔴 **NOT SCHEDULED, NOT via n8n, NEVER SENT.** There are **no n8n artifacts** in the repo; nothing
  schedules the report (pg_cron is used **only** for retention, not the report); it defaults to dry-run
  and must be run manually with `--send`. `report_deliveries` = **0 rows**. `report_recipients` = 1
  WhatsApp recipient (enabled).
- **Evolution/WhatsApp health (read-only, no message sent):** the Evolution API is reachable; **2
  instances — "Alkhalid Security" is `open` (connected)**, "Alkhalid" is `close`. **But these belong to
  the separate AKSS Security-Portal engagement** — WatchLog's reporter **borrows Evolution credentials
  from `../alkhalid-security-portal/.env.local`** (they are not in WatchLog's own env). **Cross-project
  credential coupling** — a delivery/independence risk.
- **SOW classification:** n8n architecture **NOT DELIVERED / DEVIATION**; daily automation **NOT
  DELIVERED**; the send logic itself **FUNCTIONALLY EQUIVALENT BUT DEVIATION** (correct, idempotent,
  tz-aware — just unscheduled, not via n8n, and never proven live).

---

## L. SendGrid (M3: branded HTML daily email)

**PARTIAL.** `Email` class posts to SendGrid `v3/mail/send` with Bearer auth, logs deliveries, and honors
the `report_recipients.channel` preference (whatsapp/email/both) at the data layer. **But it hard-codes
`content: [{type: "text/plain"}]` — there is no HTML template anywhere** → the contractual **branded HTML
email is not built.** It is also **unconfigured** (no key present → reports "unavailable", logs `skipped`).
No SendGrid domain-authentication/template plumbing.

---

## M. Switch billing (M3 — IN SIGNED SCOPE)

🔴 **NOT STARTED.** Repo-wide search finds **no** Switch merchant config, **no** checkout/payment-session
creation, **no** payment callback/webhook receiver (the only HTTP receiver is the recorder-push bridge),
**no** signature validation, **no** transaction/subscription/payment/webhook-event tables, **no**
trial-to-paid/renewal/cancellation logic, **no** portal billing UI. Only trial-state columns on `tenants`
and the owner-settable `wl_set_plan`. (No Safepay/Stripe either; "Switch/Safepay" appears once, in a
pricing planning note.) **Credentials missing ⇒ also CLIENT-DEPENDENT**, but the code itself is unbuilt.

---

## N. Installer (M4 — contract says NSIS)

- **Technology: Inno Setup + PowerShell — NOT NSIS.** `watchlog.iss` is Inno; there is **no `.nsi`
  anywhere**. A parallel `Install-WatchLog.ps1` is what actually ships. → **CONTRACT TECHNOLOGY MATCH = NO.**
- **Only built artifacts are ZIPs** (the `make_installer.ps1` fallback when `ISCC.exe` is absent):
  `dist-installer/WatchLog-Setup-*.zip` (4 of them). **No `Setup.exe`/`.msi` was ever compiled.** The
  branded Inno wizard has never been built. (The agent exe itself is built: `prototype/dist|ship/
  watchlog-agent.exe`, 16 MB, untracked/gitignored.)
- **Unsigned** (no signtool/cert) → **SmartScreen on every fresh PC** (the READ-ME tells users to click
  "Run anyway"). **YOLO model not bundled** → ships unfiltered (see §J).
- Autostart = **SYSTEM scheduled task** `AtStartup` (not an SCM service), RestartCount 999 + `run-agent.cmd`
  15 s restart loop + `powercfg` hardening. Program Files install; `%ProgramData%\WatchLog` preserved on
  uninstall. **Inconsistency:** the shipped PowerShell path **overwrites `watchlog.ini` on reinstall**
  (clobbers recorder config), while the Inno path preserves it.
- **INSTALLER CODE COMPLETE = PARTIAL · INSTALLER ACCEPTANCE COMPLETE = NO (not tested on
  Win10/11/reboot/logon/power-loss/crash/uninstall — no evidence) · CONTRACT TECHNOLOGY MATCH = NO.**

---

## O. Runbooks / handoff (M4)

🔴 **MISSING.** None of the M4-required runbooks exist as docs: **adding tenants manually, key
rotation, log access, webhook replay, NVR troubleshooting**. Tracked `.md` files are design/brand docs
+ `PROJECT.md` + `prototype/README.md` (a prototype runbook) + `03_Design/CONNECTIVITY.md`. Some
operational notes live in `_memory/` but that is **gitignored/internal** and deliberately out of the
client repo — not a client-facing handoff. Legal pages (privacy/terms) exist on the marketing site only.

---

## P. Credential / secret hygiene

**Tracked tree is clean** (§A): no `github_pat_`/`ghp_`/JWT/`SG.`/PEM/`service_role`/DSN-with-password
in any tracked file; `.env` never committed; only `.example` placeholders.

**Runtime secret inventory (names + status only, no values):**

| Where | Names | Status |
|---|---|---|
| `projects/watchlog/.env` | `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY` (**EMPTY**), `SUPABASE_DB_*`, `COOLIFY_URL`, `COOLIFY_API_TOKEN`, `WORDPRESS_*`, `PORTAL_DEMO_*` | present (secret key intentionally empty) |
| `portal/.env.local` | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | present (public by design) |
| Borrowed from AKSS portal project | `EVOLUTION_API_*`, `EVOLUTION_INSTANCE_NAME`, GitHub PAT | not in WatchLog's own env (coupling) |
| Absent everywhere | SendGrid key, Switch merchant creds | — |

**ROTATION REQUIRED (both were pasted in chat historically and are still valid):**
- **Coolify API token** — confirmed still valid at audit time (it authenticated the Coolify API; note it
  is stored **quote-wrapped** in `.env`, which will break naïve parsers).
- **Supabase DB password** — used at audit time; historically exposed.

Also flagged in §G: `schema_migrations` is anon-writable (integrity, not a value leak).

---

## Q. Domain / production URL

🔴 **DOMAIN = MISSING.** All properties are on `*.sslip.io`. The throwaway host is **baked into shippable
artifacts**: the Windows installer (`watchlog.iss:28` AppPublisherURL), the WordPress theme
(`functions.php:83`), and the WP provisioning script (`site-content.sh:353`). Moving to a real domain
(e.g. `watchlog.pk`) requires editing **Coolify domains, WP home/siteurl + canonical + OG URL, the
installer publisher/support URLs, the portal URL, Supabase Auth redirect URLs, the email sender domain,
future Switch callbacks, and the recorder-push endpoint** — not just DNS.

---

## R. Automation / deployment

- **AUTO DEPLOY = NOT COMPLETE.** GitHub repo has **no webhooks**; Coolify has webhook secrets ready but
  nothing calls them. Evidence: the bridge is deployed at `b6cfb59` while 4 later commits exist — a push
  did not redeploy it. Deploys are **manual** (Coolify API/UI).
- **Migrations are applied MANUALLY** (`apply_migrations.py` by hand); 0010–0015 were applied out-of-band
  and are **absent from the ledger** (§E). No automated migration step in any deploy.

---

## S. Tests / builds (run at audit time)

| Suite / build | Type | Result |
|---|---|---|
| `test_vision_filter.py` | local | **12 passed, 0 failed** |
| `test_push_bridge.py` | local | **5 passed, 0 failed** |
| `test_capabilities.py` | local (Dahua sim) | **3 passed, 0 failed** |
| `test_tenant_isolation.py` | **live HTTP (read-only)** | **9 passed, 0 failed** (M4 gate) |
| `test_team_and_trial.py` | live (writes+cleans) | **NOT RE-RUN** — mutates the DB; excluded under the audit's no-mutation rule (12 cases present) |
| `python -m compileall` (all prototype) | syntax | **exit 0** (all compile) |
| Portal `npm run build` | production | **exit 0**, static export, `out/` = 41 files |
| Installer `*.ps1` parse | syntax | **all OK**; `.iss` not machine-validated (no ISCC) |

**Total auto-verified this audit: 29 test cases passed (0 failed) + 2 clean builds + compile pass.**
`test_team_and_trial` (12 cases) was deliberately not executed to avoid production writes.

---

## T. Live data counts (audit time)

tenants **2** · sites **2** · agents **17** (recorder-push 3, dahua 7, hik 2, mock 3, null 2) ·
online agents **0** · cameras **12** · events **2070** · snapshots **803** (~2 MB) · push_sources **3** ·
memberships **2** · invitations **0** · report_recipients **1** · report_deliveries **0**
(**0 reports ever sent**).
**Real-hardware share:** ~9 events / 8 snapshots (SM-HP DH-XVR1B08-I). Everything else is
simulator/mock/dev + 4 recorder-push test events.

Tenants: **AKSS (prototype)** `plan=starter, status=active` (self-set via `wl_set_plan`); **Demo
Security Co** `plan=trial, status=trialing`. `trial_days=14` on both (default 14 ✓, not enforced).

---

## U. Contract gap matrix

See **`SOW_GAP_MATRIX.md`** for the full line-by-line matrix.

---

## V. Delivery percentages

Weighting rationale: the four scores answer four different questions. Out-of-scope extras
(recorder-push, capability probe, retention) are **excluded** from every contractual number and listed
as bonus only.

| # | Score | What it measures | Why |
|---|---|---|---|
| 1 | **~68%** | **Engineering implementation** | Most subsystems are coded: agent, drivers, sync, spool, portal, site, reporter engine, team/trial/retention/capabilities/push. Not coded: Switch (0), branded HTML email (0), runbooks (0), NSIS (wrong tech). |
| 2 | **~58%** | **Live-deployment readiness** | Portal/site/bridge/DB live + healthy, isolation passing, portal builds. But no domain, no auto-deploy, no healthchecks/monitoring, AI filter not shipped, report unscheduled. |
| 3 | **~37%** | **Signed-scope completion** | Milestone-weighted (25% each): M1 ~45% (agent/sync/dashboard built; AI-filter + field gate + WhatsApp-schedule all fail), M2 ~60% (multitenancy/isolation/site/registration; onboarding incomplete, team/reports/trial no UI), M3 ~10% (SendGrid text stub; Switch 0), M4 ~25% (isolation suite only; NSIS wrong, runbooks missing, handoff undone). |
| 4 | **~28%** | **Final handoff readiness** | Blocked on real-hardware validation, no domain, filter not shipped, billing absent, runbooks missing, secret rotation outstanding. |

---

## W. Blocker list

**P0 — blocks the next milestone (M1) acceptance**
1. **AI filter not shipped** — no model + runtimes excluded (`build_exe.ps1`, `watchlog-agent.spec`;
   `prototype/models/` empty). Verify fix: strings-scan a rebuilt exe for `onnxruntime` + a bundled
   `yolov8n.onnx`. *Not client-dependent.* ~1–2 days (rebuild + tune) + real footage (client).
2. **M1 field validation FAIL** — SM-HP produced 9 events/3 min, 0 cameras. Get
   `C:\ProgramData\WatchLog\agent.log` + Task-Scheduler state from SM-HP; fix the stop + `list_channels`
   on real XVR firmware. Verify: a real recorder streaming >1 h with cameras synced. **CLIENT-DEPENDENT.**
3. **Daily WhatsApp never scheduled/sent** — no n8n workflow, 0 deliveries. Verify: a scheduled job
   produces a `report_deliveries` row `status=sent`. (Needs go-ahead for one live send — client.)

**P1 — blocks final handoff**
4. **Switch billing NOT STARTED** (M3). Needs merchant creds (client) + full build.
5. **SendGrid branded HTML** missing (text/plain only) — build the HTML template + configure sender.
6. **NSIS installer** — wrong tech (Inno/zip), unsigned, untested. Decide NSIS vs documented Inno
   deviation; compile a real `Setup.exe`; test the boot/uninstall lifecycle.
7. **M4 runbooks missing** — write add-tenant / key-rotation / log-access / webhook-replay /
   NVR-troubleshooting.
8. **No production domain** — baked into installer + WP theme (`watchlog.iss:28`, `functions.php:83`,
   `site-content.sh:353`). **CLIENT-DEPENDENT** (domain purchase).
9. **Portal UI gaps** — team/reports/recipients/channel-prefs/trial/billing/analytics/capabilities have
   DB support but **no UI**; onboarding dead-ends at the installer-download stub (`onboarding/page.js:111`).

**P2 — production hardening**
10. `schema_migrations`: RLS off + **anon SELECT/INSERT** — REVOKE from anon/public.
11. **`wl_set_plan` billing-authorization gap** — owner self-sets plan/subscription_status; lock to
    service-role/webhook before billing.
12. Auto-deploy off (manual); migration-ledger drift (0010–0015 unlogged) — wire a webhook + reconcile.
13. Coolify healthchecks disabled; host load ~11.
14. Agent: plaintext `agent_key` survives uninstall; no exponential backoff; push-driver events lost on
    downtime; NVR password plaintext; tighten `%ProgramData%` ACLs.
15. Tighten the 4 `public`-role RLS policies to `authenticated`; add the 12 missing FK indexes; fix
    `wp-sitemap.xml` 404.

**P3 — optional / out-of-scope**
16. Recorder-push, capability probe, retention (bonus — keep, but they don't count toward the contract).
17. Code-signing certificate.
18. **Decouple WhatsApp/Evolution from the AKSS-portal project** (WatchLog currently borrows its creds).

---

## Security findings (summary)

- **CRITICAL (per audit definition):** billing-authorization gap — `wl_set_plan` lets a tenant owner
  self-assign plan/subscription_status (low live impact today; must be locked before billing).
- **ROTATION REQUIRED:** Coolify API token **and** Supabase DB password (both exposed in chat, both still
  valid).
- **MEDIUM:** `schema_migrations` anon SELECT+INSERT (RLS disabled in public).
- **No cross-tenant data leak found** — isolation gate 9/9 live, sensitive tables + view return empty to
  anon, privileged/destructive RPCs denied to anon.

---

## Client dependencies

SM-HP `agent.log` + Task-Scheduler state · a real Hikvision + a real ONVIF unit · selection of the 2nd
MVP site + 10 cameras/site · a production domain · **Switch merchant credentials** · a SendGrid account +
domain authentication · go-ahead for one live WhatsApp send.

---

*Generated by live inspection on 2026-08-31. Companion machine-readable data:
`CURRENT_STATE_2026-08-31.json`. Gap matrix: `SOW_GAP_MATRIX.md`.*
