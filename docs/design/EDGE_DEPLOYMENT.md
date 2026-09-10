# WatchLog Edge Deployment — local-compute deployment model

**Status:** design spec (item 12) · **Author:** Vision Infinity engineering · **Date:** 2026-09-10

> WatchLog's on-site compute is the **Site Agent**: a small outbound-only program that sits on the
> customer LAN, reads the recorder the business already owns, filters noise locally, spools through
> outages, and reports verified results to the cloud. This document specifies the three ways that
> compute is deployed, as a real, buildable hardware/OS/network spec — not a roadmap paragraph.

The architecture decision everything follows from (PROJECT.md, `03_Design/CONNECTIVITY.md`):
**the connection only goes outward.** No inbound port, no port-forward, no VPN. The recorder
credential never leaves the building (machine-scoped DPAPI), and video is never uploaded — only
event records, a still per event that passed the filter, and non-video health/coverage metadata.

There is also a PC-free **recorder-push (lite)** path (recorder POSTs to the cloud bridge,
`wl_ingest_push`) for sites that cannot host any local compute. It has **no** edge filtering, no
capability probe and no Site Control, so it is out of scope here — this document is the
local-compute (Agent) model. Deploy the lite path only when none of the three modes below is possible.

---

## 1. Three deployment modes

| | **A — Existing always-on PC** | **B — WatchLog Edge appliance** | **C — Existing on-prem server** |
|---|---|---|---|
| What it is | The Agent installed on a Windows PC the site already leaves on (reception/back-office/DVR-adjacent PC) | A dedicated fanless mini-PC we supply, pre-provisioned, single-purpose | The Agent installed as one more service on an existing site/branch server or NVR-workstation |
| Best for | SMB with one always-on machine; the default and cheapest | Sites with no reliable always-on PC, or where a single-purpose, sealed box is wanted | Multi-site orgs with server rooms / an existing Windows Server or hypervisor |
| Ownership | Customer hardware | WatchLog-supplied (BOM below) | Customer hardware / IT-managed |
| Today | **Supported** (this is what runs on the client's `SM-HP` today) | **Hardware profile defined; Linux image is a GAP (see §4)** | **Supported on Windows Server; container/Linux image is a GAP** |
| Isolation | Shares the host with the user's apps | Fully isolated, single-tenant box | Shares the host; run under its own SYSTEM task / service account |

All three run the **same** Agent code (`prototype/agent/watchlog_agent.py` + `drivers/` + `vision.py` +
`spool.py`); they differ only in the host and how it is provisioned. Nothing about the cloud contract,
enrollment, Site Control command plane or reporting changes between modes.

## 2. Minimum & recommended hardware spec

The false-alarm filter (YOLOv8n via onnxruntime, `prototype/agent/vision.py::OnnxDetector`) runs
**per event-still, not on a continuous video stream** — a DVR emits events at human, not frame, rates,
and inference is serialised behind a lock — so CPU-only inference is adequate. Sizing:

| Resource | Minimum | Recommended | Notes |
|---|---|---|---|
| CPU | x86-64, 2 cores, **AVX2** | 4 cores (Intel N100/i3-8th-gen+ / Ryzen 3+) | onnxruntime CPU EP wants AVX2; N100-class mini-PC is the target for Mode B |
| RAM | 4 GB | 8 GB | Frozen exe is ~16 MB, but onnxruntime + numpy + PIL resident set is ~250-400 MB; 8 GB leaves headroom on a shared Mode-A/C host |
| Storage (free) | 5 GB | 20 GB SSD | Install ~200 MB (exe + onnxruntime + ~12 MB `yolov8n.onnx`); the rest is the spool + logs (§5) |
| OS | Windows 10/11 or Server 2019+ (x64) | Same, SSD-backed | Agent is Windows-only today (§4) |
| Network | 1 outbound Internet path + LAN route to the recorder | Wired to the same switch/VLAN as the NVR | See §3 |
| Power | — | UPS on Mode B and the NVR | Keeps the observer alive through brownouts |

**GPU / NPU.** Not required and **not currently wired**: the shipped exe pins
`providers=["CPUExecutionProvider"]` (`vision.py`). A GPU (onnxruntime-gpu / CUDA) or an Intel NPU
(OpenVINO / DirectML execution provider) becomes worthwhile only for the future **live-vision tier**
(PROJECT.md §9 item 12) or very high channel counts. Selecting a non-CPU execution provider is
**PROPOSED — not built**; until then, spec CPU for the filter and treat any accelerator as optional.

**Mode B reference BOM:** Intel N100/N95 fanless mini-PC, 8 GB RAM, 128-256 GB NVMe, dual-band + GbE,
internal eMMC/SSD, no display needed. Sealed, single-purpose, auto-login SYSTEM task (§6).

## 3. Network requirements (OUTBOUND-ONLY — non-negotiable)

```
   [ Site LAN ]                                   [ Internet ]
  recorder (Dahua/Hik/ONVIF)  <--LAN only--  WatchLog Agent  --outbound 443-->  Supabase (cloud RPCs)
     no inbound to the box                     (never binds a listening socket)   report runner / portal
```

- **Outbound, to the cloud:** HTTPS/443 to the Supabase project (all `wl_*` RPCs: `wl_enroll`,
  `wl_heartbeat`, `wl_ingest_events`, `wl_report_health`, `wl_agent_claim_command`, ...). Optional
  outbound NTP/123 for host clock. That is the entire outward surface.
- **LAN, to the recorder:** the Agent reaches the recorder on the LAN only — Dahua CGI (HTTP/80),
  Hikvision ISAPI (HTTP/80 or HTTPS/443), ONVIF (SOAP over HTTP + WS-Discovery UDP/3702), snapshot
  via the same CGI/ISAPI path. The recorder needs **no** route to the Internet.
- **NO inbound anything:** no port-forward, no DDNS, no VPN, no reverse tunnel. The Agent "never binds
  a socket" — it is a client to both the recorder and the cloud. This is what lets the recorder
  credential stay on the box and the site refuse all inbound exposure.
- **Firewall ask to the customer's IT:** allow the box outbound 443 (and 123). Nothing else. If
  egress is locked down, allowlist the Supabase host; do **not** ask for any inbound rule.

Site Control (`docs/design/SITE_CONTROL_API.md`) preserves this exactly: callers send *intent* to the
cloud queue; the Agent **polls outbound** and performs the recorder action locally. No external caller
ever receives the recorder credential, and no inbound port is opened to run it.

## 4. Windows-vs-Linux direction (state of the port)

- **Today: Windows only.** The Agent is frozen with PyInstaller to a ~16 MB Windows exe; auto-start,
  power hardening, the DPAPI credential seal and the transactional upgrade are all Windows-specific
  (`register-service.ps1`, `wl-upgrade.ps1`, machine-scoped DPAPI). Modes A and C ship on Windows now;
  Mode B ships as a **Windows** mini-PC today.
- **Linux/ARM port: GAP — PROPOSED, not built.** A Linux Edge image (systemd unit instead of the
  scheduled task, an OS keystore/`secret-tool`/TPM instead of DPAPI, a Linux upgrade path instead of
  `wl-upgrade.ps1`) is the paid "WatchLog Bridge appliance (Linux/ARM build)" line in PROJECT.md §9
  item 12. The driver layer (`drivers/hikvision.py`, `dahua.py`, `onvif_driver.py`) and `spool.py` are
  already OS-neutral Python, so the port is host-integration work, not a rewrite — but until it is
  built and gated, **Mode B is a Windows appliance**, and any "Linux Edge" claim is aspirational.

## 5. Local AI storage & buffer sizing (the spool)

Durable offline buffering already exists (`prototype/agent/spool.py`, an SQLite/WAL queue) — the Edge
box does not lose events during an Internet or cloud outage:

- **Spool:** SQLite/WAL, **200k-event cap, oldest-drop**, at-least-once `take`/`ack`-after-commit,
  stills stored **inline** with their event, **4 MB batch cap** per upload. On reconnect it replays;
  server-side dedupe (`wl_dedupe_key`, `unique(tenant_id, dedupe_key)`) absorbs re-sends so nothing
  double-counts.
- **Sizing:** a filtered event carries one JPEG still (~10-40 KB). A full 200k-event spool is therefore
  on the order of a few GB worst-case; **budget 5 GB free minimum, 20 GB recommended** so the spool,
  the agent log under `ProgramData\WatchLog`, and the model never contend. Put it on an SSD — WAL on a
  spinning/eMMC disk under a burst is the main IO risk.
- **AI model storage:** `yolov8n.onnx` (~12 MB) ships beside the exe and is loaded once. If the model
  is absent the filter **fails open** (every event reported, logged once) — the box still functions,
  it just does not filter. The only image that ever leaves the building is a still that already passed
  the filter; frames are never sent to a cloud model.
- **What the spool does NOT cover:** if the box itself is **off/asleep/not scheduled**, no observation
  happened — that window is reported as a coverage gap (§7), not buffered. The spool covers
  *agent-running, cloud-unavailable*, not *agent-not-running*.

## 6. Auto-start (SYSTEM scheduled task at boot)

`prototype/installer/register-service.ps1` (run elevated by the installer after enrollment) makes the
Agent a boot-time service on every mode:

- **Scheduled task `"WatchLog Agent"`**, principal **SYSTEM**, `RunLevel Highest`, trigger **AtStartup**.
  Action is a hidden-window PowerShell running the `run-agent.ps1` launcher, which unwraps the
  machine-scoped **DPAPI** recorder credential into a process-only env var, captures output to
  `ProgramData\WatchLog`, and relaunches the agent if it exits.
- **Power hardening** (so an "observer" PC does not sleep): `powercfg` disables standby / hibernate /
  disk timeout on AC and turns hibernate off. Mode B ships with these locked; on Modes A/C we set them
  but the customer's own power policy can override — call it out at install.
- **Registration is verified:** the script waits for the task to reach **Running** and throws if it
  does not, so a silent auto-start failure cannot pass as a successful install.

## 7. Crash / reboot / resume recovery

Recovery is layered so no single failure disconnects a site:

| Failure | What happens |
|---|---|
| Agent process crashes | Task settings `RestartCount 999`, `RestartInterval 1 min`, `ExecutionTimeLimit 0` restart it; `run-agent.ps1` also relaunches on unexpected exit; a single-instance guard prevents duplicates |
| Host reboot / power-cut return | `AtStartup` SYSTEM trigger + `StartWhenAvailable` + `AllowStartIfOnBatteries` bring the Agent back with no login |
| Cloud/Internet outage | Agent keeps monitoring; events + stills spool locally and replay idempotently on reconnect (§5) |
| Host asleep / off / stalled | On the next run the Agent detects the **wall-clock jump** and reports the missed window via `wl_report_coverage_gap` (mig `0062`) with cause `observation_gap` → the day's brief says "site not monitored (agent not running)" instead of pretending it watched |
| Upgrade fails mid-flight | `wl-upgrade.ps1` rolls back to the previous binary and restarts it — a failed upgrade leaves WatchLog running on the OLD version, never disconnected (§9) |

Honest limit: from the cloud, "PC off" and "PC on, Internet down" are the **same** `AGENT_UNREACHABLE`
until local evidence reconciles (see `OPERATIONAL_INTELLIGENCE_ARCHITECTURE.md` §7). The Edge box does
not claim to have watched a period it could not.

## 8. Remote health & monitoring coverage

The box is observable from the cloud without any inbound access:

- **Heartbeat:** `wl_heartbeat` every **60 s** (`HEARTBEAT_SECONDS`); `agents.last_seen_at` drives
  liveness. A server-side watchdog opens/closes `agent_unreachable_intervals` when heartbeats lapse.
- **Layered health:** `wl_report_health` (mig `0044`) and `wl_report_camera_health` (`0045`/`0046`)
  carry recorder connectivity/auth and per-channel camera state; the portal renders it via
  `wl_site_health_details` (`0033`) — an honest per-layer resolved state, never a single "Online".
- **Monitoring coverage:** `wl_report_coverage_gap` + `wl_site_coverage_report` (`0062`) union
  server-unreachable + reconciled-unverified + agent-attributed gaps (`agent_coverage_gaps`) so
  coverage % never double-counts and never exceeds wall-clock. The daily brief exposes it as
  `monitoring_coverage` (`wl_office_brief`, `0060`/`0062`).
- **Recorder truth via Site Control (READ):** `inspect_recorder` / `get_video_loss_state` etc. through
  `wl_site_command_enqueue` → `wl_agent_claim_command` → `site_control.execute_read` →
  `wl_agent_complete_command` → `wl_site_command_result` (mig `0063`, `prototype/agent/site_control.py`)
  let support or the portal "Diagnose site" inspect the recorder with no login and no inbound port.

## 9. Update model (transactional installer)

Upgrades are delegated to `prototype/installer/nsis/wl-upgrade.ps1` so an installer can **never** report
success unless the new Agent is actually installed and running. Staged, ordered, reversible:

1. **preflight** — stop the task, stop *only* the exact `watchlog-agent.exe` under the install dir,
   wait (bounded) for it to exit, verify the binary is unlocked, back it up (`.wlbak`). Non-zero exit
   ⇒ do not overwrite (old runtime preserved).
2. **verify-version** — after staging, require the on-disk file **ProductVersion** *and* the runtime
   `--version` to both equal the expected release (rejects a binary that was not actually replaced).
3. **commit** — after the task is (re)registered and started, confirm exactly **one** instance is
   running and stays alive, re-check the version, then drop the backup.
4. **rollback** — on any failure, restore the backed-up binary and restart the previous agent.

It touches processes/files/version strings only — it reads or logs **no** agent key, enrollment code
or recorder password. This mirrors the Site Control write discipline (read → apply → read-back →
verify → rollback). Current state: the transactional path is gated `53/53 + 10/10 live upgrade`
(`M1_PREDEPLOY_REVIEW.md` §7); the `0.4.1 → 0.4.2` field upgrade on `SM-HP` is the pending on-site proof.

## 10. Deployment checklist (per site, any mode)

1. Confirm the host meets §2 (or ship a Mode-B box) and has an outbound path + LAN route to the recorder (§3).
2. Run the branded installer: recorder detect/credential (DPAPI-sealed) → one-time enrollment code
   (`enrollment_codes` → `wl_enroll`) → `register-service.ps1` (task + power hardening) → verify Running.
3. Confirm first heartbeat, camera discovery (`wl_sync_cameras`) and capability probe
   (`wl_sync_capabilities`) land in the portal.
4. Drop `yolov8n.onnx` beside the exe (or confirm it shipped) so filtering is on, not fail-open.
5. From the portal, run Site Control READ `inspect_recorder` to prove cloud → agent → recorder → result
   with no inbound port.
6. Record the box in the fleet; monitoring coverage and Site Health now report it continuously.
