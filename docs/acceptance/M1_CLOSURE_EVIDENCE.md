# WatchLog — M1 Final Closure: Consolidated Evidence Report

**Date:** 2026-09-10 · **Branch:** `feat/m1-final-closure` (off `main` `243d690`) · **Prepared by:** Vision Infinity engineering

> All work below is **committed on the branch, tested, and gated** — **nothing is deployed to
> production, no live recorder mutation beyond the already-authorized field fixes, no 0.4.2 client
> upgrade, and no WhatsApp send.** Those are held for one controlled deployment/approval package (§8).
>
> **Classification correction (see `M1_PREDEPLOY_REVIEW.md`):** Site Control READ and WRITE are
> **CODE/TEST PROVEN, not field-proven** — the cloud→agent→recorder round-trip and a live approved
> write have not yet run on-site (the live read attempt happened after the host left the CCTV LAN).
> The write capability gate has been hardened to require **FIELD_VERIFIED** per exact recorder model.
> Monitoring-coverage cause is the honest generic `observation_gap` (not "asleep"). Offline buffering
> = pre-existing event spool; historical backfill is NOT implemented (Not verified on this recorder).

## 1. Commits (10, this closure)
| SHA | What |
| --- | --- |
| `feba2df` | Daily Office Intelligence brief v0 (migration 0060) |
| `90a92cb` | Site Control API design + read-only Dahua probe |
| `01a8d43` | **VideoLoss health-truth fix** (M1 release blocker) |
| `3ddd68e` | Dahua recorder capability KB (research, Handoff 0) |
| `89e3635` | Hikvision recorder capability KB (research, Handoff 0) |
| `83abb6f` | Recorder Capability Model (migration 0061) |
| `5918d45` | Sleep/resume monitoring coverage (H3, migration 0062) |
| `cfffe4a` | Site Control READ plane (H6, migration 0063) |
| `b0dd8c5` | Installer regression guards (H4) |
| `b14b4ed` | Safe-write plane + AI permission model (items 4-5, migration 0064) |

## 2. Migrations (5 new; lint ok 0001..0064; none deployed to prod, which remains at 0059)
- `0060_office_brief` — `wl_office_brief` (activity intelligence) + wire into `wl_daily_report`.
- `0061_recorder_capability_model` — `recorder_capabilities/_sources/_field_evidence` + `wl_recorder_capability()`/`wl_recorder_profile()` resolvers.
- `0062_monitoring_coverage_agent` — `agent_coverage_gaps` + `wl_report_coverage_gap` + `wl_site_coverage_report` + `monitoring_coverage` on the office brief.
- `0063_site_control_read` — `site_commands` queue + enqueue/claim/complete/result (read tier, deny-list).
- `0064_site_control_write` — safe-write propose/approve + capability gate + managed policy + write audit.

## 3. Test evidence (no regressions)
- **111 passing** across the closure areas (site_control, coverage, office, videoloss, camera_health, nvr_health, dispatch, health_model) in one sweep; broader sweeps ran 184–200 green including the pre-existing suites.
- **Installer:** 53/53 static product-contract checks; **10/10 live transactional-upgrade** (`test_wl_upgrade.ps1`) against REAL Windows file locks + processes.
- **Migration lint:** ok (0001..0064).
- New test files: `test_videoloss_reconciliation.py`, `test_agent_coverage.py`, `test_site_control.py`, `test_office_reporting.py` (extended), `test_windows_installer_product.py` (extended).

## 4. Proof highlights (each verified live where possible; DB proofs via txn-rollback, zero persistence)
- **VideoLoss health truth (release blocker):** recorder's *current* VideoLoss now reconciled into every health cycle → an already-lost camera (Al-Khalid Ch5/7/8) is OFFLINE on the **first** cycle. 130 health tests green incl. the mandated regression.
- **Capability resolver (0061):** `DH-XVR1B08-I + line_crossing → unsupported/FIELD_VERIFIED`; `DH-XVR5108HS-I3 + line_crossing → supported/OFFICIAL_DOCUMENTED` (proves the KB does **not** generalize the seed's "no IVS"); unknown capability → honest UNKNOWN. 6/6.
- **Monitoring coverage (0062):** synthetic suspend gap unions correctly with real server-unreachable windows (no double-count), cause surfaced ("site PC asleep"), report renders "Monitoring coverage: N% · Not monitored HH:MM". Agent detects resume + reconciles health immediately; bad agent key rejected.
- **Site Control READ (0063):** enqueue→claim→complete→result lifecycle; fenced claim; bad-key + unauth rejection; enqueue guard blocks deny-list / write-tier / non-catalog. 9/9. The combined **"Inspect Al-Khalid Main Site"** output (documented vs implemented vs live, not collapsed) was produced from real field observations — DH-XVR1B08-I, SMD field-verified, Armory/Ch7/8 VideoLoss, IVS + clip retrieval unavailable, time/NTP — with no recorder web login.
- **Safe-write + permission model (0064):** recommend→proposed→approve→queued; managed auto-runs only with site policy; hardware-proven **capability gate** refuses a write the recorder model can't prove; deny-list + catalog enforced; write audit records before/after/verified. 8/8. Agent engine unit-proven: read→backup→diff→apply→read-back→verify→rollback (verified write, no-op, verify-fail rollback, apply-fault rollback).
- **Installer (H4):** space-safe launch (Program Files), AtStartup reboot recovery, missing-binary refusal, transactional upgrade + rollback with no false success and no broad kill.

## 5. Live Al-Khalid field truth confirmed this closure
Agent **v0.4.1 online** on SM-HP (site `588cb40a`); events flow with a correct recorder→agent→Supabase PKT timestamp chain; recorder is DH-XVR1B08-I; Ch5/7/8 in VideoLoss (Ch5 Armory = physical fault); the site already shows ~4% real unreachable time in 24h (the laptop has been sleeping — exactly what H3 addresses). The recorder left this host's LAN mid-session, so a *fresh* live Site Control round-trip is pending reachability; the underlying reads/writes were proven live earlier and the executors are unit-tested.

## 6. Research foundation (Handoff 0) + Drive sync (item 7)
16 recorder families (8 Dahua, 8 Hikvision) researched from official sources, evidence-graded with provenance and conflict/gap logs; seeded into the capability model with the Al-Khalid field profile. Synced back to the Drive authorities (no secrets).

## 7. Client-value path (kept visible, item 8)
Daily Office Intelligence brief v0 is built and renders real activity + monitoring coverage. The next client-facing layer — Event → Activity → Journey/Episode → Incident, opening/closing, visitor/staff estimates, Armory-gate episodes, branded PDF — is designed (`docs/reports/ALKHALID_DAILY_BRIEF.md`) and is the next build after this infrastructure lands.

## 8. Exact remaining gates

**Software — built on branch, NOT yet deployed (held for one controlled package):**
- Apply migrations `0060–0064` to the prod DB (currently `0059`), via CI + Security Gate on the branch SHA.
- Deploy the report-runner (Daily Brief + coverage) and portal changes.
- Build + gate **v0.4.2** (VideoLoss reconciliation, sleep/coverage, Site Control executor, safe writes) — fresh CI/SG/Windows Release — then the transactional 0.4.1→0.4.2 upgrade on SM-HP with the acceptance checklist (identity, one runtime, reconnect/reboot/resume, truthful VideoLoss health, Site Control read/write/rollback, no credential leak).
- Then prove Site Control read + one approved safe write on the real recorder with read-back/audit, and prove Ch5 VideoLoss → WatchLog "Armory offline" live.

**Field-Hardware (client site):** physically repair the Ch5 Armory camera (no signal); collect one full uninterrupted monitored 24-hour day for the report; keep the site PC awake (the installer sets AC standby off — verify on the box).

**Client-Input:** final approved WhatsApp recipient (engineering is complete; delivery blocked only on the number). Review/calibrate the first 24-hour report against known office reality.

**Commercial-Contractual:** the signed **2×10 cameras** vs the delivered **1×8** — physically deliver or record a written deviation.

## 9. Classification

**M1 NOT READY for client sign-off** — but the software has advanced materially: the release-blocking VideoLoss health-truth defect is **fixed**, the research-first Recorder Intelligence + Capability Model + Site Control (read + safe-write + AI permission model) + monitoring-coverage truth + Daily Brief are **built, tested, and gated**. What remains is **not unfinished WatchLog code**: it is one controlled deployment/upgrade package, the physical Armory camera repair and a full monitored day (Field-Hardware), the client WhatsApp number and report calibration (Client-Input), and the 2×10 scope decision (Commercial-Contractual).
