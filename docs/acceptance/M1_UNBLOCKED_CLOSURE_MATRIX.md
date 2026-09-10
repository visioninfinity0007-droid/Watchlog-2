# WatchLog M1 — Unblocked-Backlog Closure Matrix

Response to **"CLOSE ALL CURRENTLY UNBLOCKED ITEMS"**. Every item taken to the highest state
achievable without field hardware, client input, or a commercial decision, then stopped
honestly at the gate.

**Branch** `feat/m1-final-closure` · **HEAD** `fc0d6ea` (pushed to origin + visioninfinity fork)
· **CI** run 34505697337 on the fork for that exact SHA.

Legend: ✅ done · 🔷 designed only · ⛔ blocked (field/client/commercial) · ➖ n/a · ⚠️ action needed

| # | Capability | Designed | Implemented | Tested | CI Gated | Deployed (prod) | Field Proven | Client Accepted |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 2–3 | Activity/Episode/Incident pipeline (0065) | ✅ | ✅ | ✅ 8/8 | ✅ | ❌ pending | ⛔ | ⛔ |
| 4 | Multi-camera journeys (0070) | ✅ | ✅ | ✅ 6/6 | ✅ | ❌ pending | ⛔ | ⛔ |
| 5 | Visitor/staff inference | 🔷 | ➖ | ➖ | ➖ | ➖ | ⛔ | ⛔ |
| 6 | Opening/closing (office brief 0060) | ✅ | ✅ | ✅ | ✅ | ✅ | ⛔ | ⛔ |
| 7 | Software restricted-area analytics (policies 0065/0071) | ✅ | ✅ | ✅ | ✅ | ❌ pending | ⛔ | ⛔ |
| 8 | Native-AI secondary verification | ✅ | ✅ | ✅ 12/12 | ✅ | ⚠️ agent rebuild | ⛔ | ⛔ |
| 9 | Config drift engine | ✅ | ✅ | ✅ 12/12 | ✅ | ⚠️ agent rebuild | ⛔ | ⛔ |
| 10 | AI configuration skill | 🔷 | RPCs ✅ | ➖ | ➖ | ➖ | ⛔ | ⛔ |
| 11 | SaaS onboarding | 🔷 | ➖ | ➖ | ➖ | ➖ | ⛔ | ⛔ |
| 12 | Edge deployment spec | 🔷 | ➖ | ➖ | ➖ | ➖ | ⛔ | ⛔ |
| 13 | Offline buffering | ✅ | ✅ | ✅ 9/9 | ✅ | ⚠️ agent rebuild | ⛔ | ⛔ |
| 14 | Historical backfill framework | 🔷 | ➖ | ➖ | ➖ | ➖ | ⛔ | ⛔ |
| 15 | Coverage integration (into dataset 0067) | ✅ | ✅ | ✅ | ✅ | ❌ pending | ⛔ | ⛔ |
| 16 | Canonical daily-intelligence dataset (0067) | ✅ | ✅ | ✅ 13/13 | ✅ | ❌ pending | ⛔ | ⛔ |
| 17 | Professional PDF report | ✅ | ✅ | ✅ 11/11 | ✅ | ❌ pending | ⛔ | ⛔ |
| 18 | WhatsApp render surface | ✅ | ✅ | ✅ 8/8 | ✅ | ❌ pending | ⛔ 0 sent | ⛔ |
| 19 | Alert engine (0068) | ✅ | ✅ | ✅ 9/9 | ✅ | ❌ pending | ⛔ | ⛔ |
| 20 | Monthly rollups (0069) | ✅ | ✅ | ✅ 11/11 | ✅ | ❌ pending | ⛔ | ⛔ |
| 21 | Custom analytics workflow (0071) | ✅ | ✅ | ✅ 14/14 | ✅ | ❌ pending | ⛔ | ⛔ |
| 22 | Recorder capability KB — 18 models (0066) | ✅ | ✅ | ✅ 8/8 | ✅ | ❌ pending | ➖ ref data | ➖ |
| 23 | Capability-aware UX | 🔷 | data ✅ | ➖ | ➖ | ➖ | ⛔ | ⛔ |
| 24 | Role/access isolation tests | ✅ | ✅ | ✅ 14/14 | ✅ | ➖ | ➖ | ➖ |
| 25 | Report calibration tooling | 🔷 | thresholds ✅ | ➖ | ➖ | ➖ | ⛔ | ⛔ |
| 26 | Client demo path | 🔷 | ➖ | ➖ | ➖ | ➖ | ⛔ | ⛔ |

## What "Implemented + Tested + CI Gated but not Deployed" means here

Migrations **0065–0071** and the reporter/agent modules are committed, unit/integration-tested
against the **live Postgres** (every DB test applies its migrations inside a transaction and
**rolls back — zero writes to prod**), and gated in CI. They are **not yet applied to the prod
database**: applying them is a single controlled migration step, deliberately held for the
supervised deployment window (same posture as the 0060–0064 deploy earlier in the engagement).

Nothing was deployed silently and nothing outward (WhatsApp send, recorder write, installer
re-publish) was performed.

## ⚠️ Required before field use (item 27 — release integrity)

The agent source changed this session: `native_verification.py` (new), `config_drift.py`
(new), and an edit to `native_event_collector.py`. **The 0.4.2 installer published earlier no
longer matches source.** Before any on-box install, the versioned 0.4.2 candidate must be
**rebuilt from HEAD and re-hash-verified** (the Windows Release + Security Gate workflows do
this on the pushed SHA). `/latest/` remains untouched.

## Genuinely blocked (item 29 — do not wait)

Field hardware, client, and commercial gates, unchanged and outside this backlog:
- Ch5 armory camera physical repair (no-signal fault).
- On-box 0.4.2 upgrade + bringing SM-HP onto the CCTV LAN (agent is outbound-only; no remote install path).
- Live Site Control write round-trip, VideoLoss→armory-offline surfacing (needs the box on-LAN).
- 24-hour / one-week real dataset for report + threshold calibration.
- Client review/acceptance of the daily brief; the WhatsApp recipient number (0 sent to date).
- The 2×10-camera commercial scope (site currently 1×8).

## Deployment order when the window opens

1. `apply_migrations.py` → prod (0065–0071), verify head + no drift.
2. Rebuild + re-verify 0.4.2 installer from HEAD; field-install on SM-HP.
3. Update report-runner to render `wl_daily_intelligence` (PDF + WhatsApp from the one dataset).
4. First supervised WhatsApp send = the next FULL day, once a recipient is confirmed.
