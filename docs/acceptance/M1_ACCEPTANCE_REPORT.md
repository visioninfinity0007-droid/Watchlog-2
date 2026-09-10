# WatchLog — Milestone 1 Acceptance Report

**Date:** 2026-09-10 · **Prepared by:** Vision Infinity (engineering) · **Client:** Al-Khalid Security (AKSS)

> Business classification: **M1 NOT READY for client sign-off** — engineering is complete, gated
> and (portal/server) deployed, but the **physical field acceptance** (final agent installed on the
> site PC, live camera→WhatsApp event, and the two-site / 10-camera scale) is **not yet demonstrated**
> and cannot be from the office. Exact blockers are in §7.

---

## 1. Release identity (the one final RC)

The two verified lines of work were reconciled into a single release candidate without losing either:
the **portal customer-language** rewrite (was canonical `main`) and the **transactional-upgrade
installer + 0.4.1 agent-runtime crash-loop fix**.

| Item | Value |
| --- | --- |
| Final release SHA | `5f6783de4e7acee2c975e1ee9f00c1e87239d42d` (branch `release/m1-final`) |
| Version | `0.4.1` (0.4.0 was a non-starting crash loop — **never ship 0.4.0**) |
| CI run (fresh, this SHA) | `34451145648` — all jobs green |
| Windows Security Gate (fresh, this SHA) | `34451159184` — all jobs green |
| Windows Release / RC build (fresh, this SHA) | `34451161335` — green (`file=0.4.1 runtime=0.4.1`; verify-version passes at 0.4.1, rejects 9.9.9) |
| `watchlog-agent.exe` SHA-256 | `47F1AFCA684E07B67B24E5ECF41C6AA4291B7A5A8FD1D242A45A6CBE8A6EBA6E` |
| `watchlog-setup-ui.exe` SHA-256 | `51368E4D53C4CCE173ADDC22C73A3CD33C4F19057BB4E93D071D03C99E4127B7` |
| `WatchLog-Setup.exe` (installer) SHA-256 | `8FC4BC33B441E4949ECB9BB26DCBFA160A34B9C7049ACDB32F54B4A67256C88D` |

Old green runs were **not** reused as final-release evidence — CI/SG/WR were re-run against this exact
SHA. Field-upgrade procedure: [RC_0.4.1_FIELD_UPGRADE.md](../runbooks/RC_0.4.1_FIELD_UPGRADE.md).

## 2. Deployed production state (proven, not assumed)

| Layer | State |
| --- | --- |
| Canonical GitHub `main` | `5f6783d` on origin (`Alkalid-security/Watchlog`) + fork mirror (FF from `f851698`) |
| Coolify portal | container image tag = 5f6783de4e7acee2c975e1ee9f00c1e87239d42d, `running:healthy`, `unless-stopped`, single container |
| Supabase | migration head `0059`, 0 drift; feature gates OFF (Operations/Archive/Multi-agent) |
| Portal ↔ backend | all 82 portal RPCs present in production |
| Customer installer URL | single production value `…/0.3.4-rc8/WatchLog-Setup.exe` (duplicate preview entry removed); **not** 0.4.x, `/latest/` not promoted |
| Live site agent | `ca375509…` **v0.3.4**, last seen **2026-09-08 13:47 UTC** (offline) |

## 3. Contractual requirement → evidence matrix

Only evidence-proven items are marked PASS. "PARTIAL" = implemented + gated but not field-proven.

| # | M1 contractual item | Evidence | Status |
| --- | --- | --- | --- |
| 1 | **Windows Site Agent** | 0.4.1 RC built + gated (installer `8FC4BC33B441E4949ECB9BB26DCBFA160A34B9C7049ACDB32F54B4A67256C88D`); live 0.3.4 agent running as SYSTEM scheduled task on SM-HP; transactional self-recovering upgrade (`wl-upgrade.ps1`, 10/10 tests) | **PASS (code/CI)**; **PARTIAL (field)** — final 0.4.1 not yet installed on site |
| 2 | **Dahua/Hikvision direct polling, no port-forwarding** | `drivers/dahua.py`, `drivers/hikvision.py`; agent is outbound-only ("no port forwarding, no VPN, no inbound connection ever; never binds a socket"); live agent polls Dahua `DH-XVR1B08-I` on the site LAN and syncs to Supabase over HTTPS | **PASS** (live: 538 events from the Dahua recorder) |
| 3 | **Local YOLOv8n filtering** | `prototype/models/yolov8n.onnx`; `vision.classify_event` keeps person/car/motorcycle, discards the rest; packaged AI self-test gates every build; live 0.3.4 agent applies it | **PASS** (packaged + live) |
| 4 | **Secure Supabase sync** | recorder credential in machine-scoped DPAPI (DACL SYSTEM+Admins, self-test proven); RLS on every table; agent authenticates with a split agent key; HTTPS outbound only; Windows Security Gate green | **PASS** |
| 5 | **Internal Next.js dashboard** | portal deployed on Coolify (image tag 5f6783de4e7acee2c975e1ee9f00c1e87239d42d), 11/11 routes HTTP 200; Overview/Site-Health/Incidents/Operations/Executive/Archive/Analytics/Reports/Settings/Control-Room | **PASS (deployed)** |
| 6 | **Two MVP sites × 10 cameras each** | production has ONE live client site ("Main site" `588cb40a`, tenant `1c1ccbac`) with **8 cameras**; the only other populated site is VI's internal "AKSS Head Office (live test)" (8 cameras). No 2×10 configuration exists. | **DEVIATION — see §6** |
| 7 | **Daily WhatsApp summary** | 1 WhatsApp recipient configured + enabled for the client tenant (`92333***`); Evolution API + report runner + n8n wired; **but `report_deliveries` is empty — 0 messages ever delivered** | **PARTIAL — pipeline configured, never delivered end-to-end** |
| 8 | **SaaS platform-admin login** | fixed: the operator account `awais.envision@gmail.com` is now `platform_owner`; `wl_platform_me()` resolves the role → root router redirects to `/admin/` (authz path, no frontend bypass) | **PASS (data/logic)**; live login = 30-sec operator check |

## 4. Field-test results (0.3.4 → 0.4.1 upgrade)

**Not executed.** The controlled field upgrade requires access to the site PC (`SM-HP`), which is
**offline since 2026-09-08** and not remotely reachable from the office (the agent connects outbound
only; there is no inbound path). The upgrade + its full acceptance checklist (same identity, one
process, runtime 0.4.1, heartbeat, camera sync, NVR polling, Phase-A health, local-AI flow, reboot
recovery, rollback) are specified in the runbook and must be performed on-site with the PC online.
The transactional mechanism itself is proven (10/10 real-Windows lock/rollback tests + CI).

## 5. WhatsApp end-to-end evidence

**Not produced.** A genuine event chain (physical camera → recorder → agent/YOLO → Supabase →
report → n8n → WhatsApp) cannot be produced now: the agent is offline (no fresh event), and sending
a WhatsApp to the client's live number is an outward action that needs explicit approval and the
agent online for authenticity. `report_deliveries` shows zero historical deliveries, so the
summary→n8n→WhatsApp leg has never completed. This is a required field-acceptance step.

## 6. Deviations (require written client acceptance)

1. **Scale: 2 sites × 10 cameras.** Delivered: **1 live client site × 8 cameras** (Dahua 8-channel
   `DH-XVR1B08-I` at the Al-Khalid main site). There is no second 10-camera client site and no
   10-camera site. **Client decision required:** accept the delivered single-site / 8-camera MVP for
   M1, or schedule the second site + camera expansion as a condition of M1 sign-off.
2. **Daily WhatsApp summary** configured but never delivered end-to-end (0 deliveries) — needs one
   supervised live delivery to the approved recipient to accept.
3. **Platform-admin hygiene (non-blocking):** an orphan typo account `awais.envison@gmail.com` still
   holds `platform_owner`; and the **client account `salman.alkhalid@gmail.com` holds `platform_owner`**
   (cross-tenant SaaS access) — recommend review/removal for tenant isolation.

## 7. Exact blockers to "M1 READY"

1. **Bring the site PC online** and run the controlled **0.3.4 → 0.4.1** field upgrade per the runbook;
   capture the acceptance checklist with timestamps.
2. **One live WhatsApp acceptance event** to the approved recipient (with approval), capturing the
   n8n execution + WhatsApp receipt + `report_deliveries` row.
3. **2×10-camera commitment**: physically demonstrate, or obtain written client acceptance of the
   delivered single-site/8-camera MVP as the M1 deviation.

None of these are code/CI blockers — the software is complete, gated and (portal/server) deployed.
They are physical/field/commercial acceptance steps that require the site and the client.

## 8. Client acceptance

- [ ] Client accepts the delivered M1 scope, **or** records the §6 deviations in writing.
- [ ] Field upgrade to 0.4.1 completed on the site PC (checklist attached).
- [ ] One daily WhatsApp summary received and confirmed by the client.
- [ ] Platform-admin access reviewed (operator retained; client cross-tenant access decision recorded).

Signed (client): ________________________   Date: ____________

Signed (Vision Infinity): ________________   Date: ____________
