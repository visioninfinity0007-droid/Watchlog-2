# WatchLog Operational Intelligence — Architecture & Gap Analysis

Status: DESIGN / GAP ANALYSIS. Architecture direction approved; **implementation not authorized** — Phase
A begins only on explicit sign-off. RC8 (`72647a1`, installer `C7848FE5…`) is the frozen field-proven
baseline and must not be rebuilt for this work.

Product principle (the spine of every decision):
> WatchLog continuously verifies the customer's CCTV estate is working, identifies the security events
> that actually matter, and turns surveillance activity into evidence-backed operational reports. Not
> vanity analytics, not detection spam, not ambiguous "online", not continuous video upload, not reports
> nobody acts on.

Locked product hierarchy (drives §11, §17, §19):
1. **Surveillance Reliability** — Is WatchLog monitoring? Is the recorder reachable? Is it recording?
   Which cameras are actually usable?
2. **Security Intelligence** — What meaningful security activity happened?
3. **Incidents** — What requires review/action?
4. **Evidence** — Still + bounded footage + reason/audit.
5. **Management Reporting** — What needs attention, what failed, what happened, and how complete is the
   evidence?

Every capability is labelled **EXISTS** / **PARTIAL** / **MISSING** / **PENDING‑MERGE** with the
current-code citation. Facts come from a read-only audit of the working tree (migrations `0001`–`0039`,
`prototype/agent/*`, `portal/app/*`, `prototype/reporter/*`), the OPEN PR #36 branch (fetched read-only to
ref `pr36`, head `5899604`), and a read-only production read of the live Al-Khalid RC8 site on 2026-09-07.

---

## 1. Current-state audit (what the live RC8 site produced)

Read-only production snapshot, 2026-09-07 (~44 min after enrolment):
- 8/8 cameras enrolled (channels 1–8), Dahua **DH-XVR1B08-I** (`dahua-cgi`).
- 51 events (person=50, motion=1) across channels 1/2/4/6; channels 3/5/7/8 produced **zero** events —
  **quiet, not proven offline** (there is no health probe to tell the difference — §8).
- **0 duplicate events** (51/51 distinct `dedupe_key`) — server-side dedupe is working well.
- 32 stills, 321 KB total, stored as bytea in Postgres.
- **Liveness has been intermittent.** The agent's `last_seen_at` stopped at 11:23 UTC, then advanced once
  to 11:43 and went stale again; no new events since 11:23. With `HEARTBEAT_SECONDS = 60` this is **not**
  the continuous cadence a fully-online agent would show. **We treat this strictly as *observed
  intermittent cloud reachability/liveness* — the cause (PC state, LAN, link) is NOT diagnosable from the
  cloud and is deliberately left undetermined pending local evidence. This is exactly why the health model
  must distinguish a server-derived `AGENT_UNREACHABLE` (cause unknown) from any diagnosed cause (§5–§7).**
  A read-only overnight sampler (`~/watchlog_field_forensic/overnight_observation.log`) records the
  liveness/event/still series without touching the site.
- No `incidents` table, no footage tables in production (only `push_sources` beyond the core set).

Takeaway: the pipeline works (recorder → discovery → enrolment → 8 cameras → events → stills), but
WatchLog cannot yet answer *"are my cameras actually working, and is the recorder recording?"* — it has no
camera-health probe, no recording/storage signal, and no stored health state.

## 2. What already EXISTS (foundations — build on, do not rebuild)

- **Durable offline buffering** — `spool.py` SQLite/WAL queue (200k cap, oldest-drop), at-least-once
  `take`/`ack`-after-commit (`watchlog_agent.py:538-541`), stills inline with events, 4 MB batch cap. **EXISTS.**
- **Server-side dedupe** — `wl_dedupe_key()` (`0007:34`), `unique(tenant_id, dedupe_key)`. **EXISTS.**
- **On-device analytics engine** — line-crossing (+direction), zone entry, zone dwell, occupancy,
  after-hours schedule activity, centroid tracker TTL 8s (`analytics.py:311-431`). **EXISTS** (the real evaluator).
- **On-device AI false-alarm filter** — YOLOv8 keeps person/car/motorcycle, fail-open
  (`watchlog_agent.py:475-485`). **EXISTS.**
- **Auth breaker** — interruptible 5/15/30-min backoff, wakes on credential change (`watchlog_agent.py:386-421`). **EXISTS.**
- **Event stills** — capture (all drivers), inline `wl_ingest_events`, `snapshots` bytea one-per-event,
  plan retention + nightly pg-cron prune (`0007`, `0014`). **EXISTS.**
- **Native recorder-AI drivers + Dahua clip export** — `NativeDahuaDriver`/`NativeHikvisionDriver` and a
  working `get_clip`→`.dav` (`loadfile.cgi`). **PENDING‑MERGE** in PR #36 (§16), not on `main`.
- **Analytics authoring** — Studio (draw line/zone on a requested still, class, schedule, dwell, sample
  rate, "also create an incident") + schedule presets. **EXISTS.**
- **Daily report** — `watchlog-report-runner` (`prototype/reporter/serve.py`), n8n cron 07:15,
  WhatsApp+Email, idempotent, entitlement-gated. **EXISTS.**
- **Derived portal health read-model** — Site-Health shows per-camera `activity_state`, agent liveness,
  equipment faults (`0033`, `0009`, `site-health/page.js`). **EXISTS** but limited (§3, §7).

## 3. What is PARTIAL

- **NVR failure classification** — the running agent detects auth-vs-other and backs off, and Setup has a
  rich classifier (`setup_backend._classify_exception`, `_classify_camera_sync`), but the running agent
  **transmits none of it** — the 60s heartbeat is liveness-only (`watchlog_agent.py:551`). **PARTIAL.**
- **Rule schema** — `monitoring_rules` supports schedule, geometry, direction, dwell, sample_seconds,
  severity, `promote_incident`; **no confidence threshold, no cooldown, no generic actions**
  (`0024:40-74`, `0030:22`). **PARTIAL.**
- **Portal camera "health"** — `activity_state` is derived from `max(events.device_ts)`, which its own
  migration says is last-event time, not a camera heartbeat, so a healthy-but-quiet camera reads "silent"
  (`0033:12-14`). **PARTIAL.**
- **Config-still retention** — `camera_config_snapshots` has no age-out (bounded 1/camera). **PARTIAL.**
- **Report after-hours** — hard-coded 08:00–19:00, ignores the customer's `monitoring_schedules`
  (`0027:62-67`). **PARTIAL.**

## 4. What is MISSING or PENDING‑MERGE

- **Camera-health probing** — no periodic per-camera liveness/freshness/latency probe; the only camera
  signals are event-driven stills (never test a quiet camera) + NVR-pushed video_loss/tamper. RTSP/stream,
  frame-freshness, staleness, latency: **MISSING** (`analytics.py:259-260` punts health to "platform-level").
- **NVR recording/storage health** — no read of HDD/storage or recording status; a snapshot proves an
  image, not that the NVR is recording it. **MISSING.**
- **Camera inventory vs. health distinction** — no signal that a channel was removed/disabled on the
  recorder as opposed to a present-but-down camera. **MISSING.**
- **Stored health state / uptime / outages / coverage** — no table records inventory/health/recording
  state, outage windows, uptime %, monitoring coverage, or unverified intervals; everything is derived at
  read from `last_seen_at` + `events`. **MISSING.**
- **Server-side agent watchdog** — agent-offline is only inferred at portal query time; there is no
  independent server process that opens/closes an agent-unreachable interval. **MISSING.**
- **Layered health transmission** — the agent never sends a per-layer health snapshot; the portal can't
  diagnose the responsible layer. **MISSING.**
- **Incident lifecycle** — "incident" is a read-only query (`wl_incidents()`) over events+snapshots; no
  status/review/assignment/acknowledgement, and operational faults are not separated from security
  incidents. **MISSING.**
- **Event→incident intelligence** — no confidence gating, no cooldown/refractory, no session grouping, no
  severity routing, no customer review feedback. **MISSING.**
- **Analytics that answer a question** — no trends, baselines, comparisons, or anomaly thresholds; all
  figures are flat window counts. **MISSING.**
- **Weekly / management report; exception-first daily brief with data-completeness.** **MISSING.**
- **Native NVR AI + on-demand incident footage (PR #36)** — **PENDING‑MERGE**: OPEN, mergeable PR
  `Alkalid-security/Watchlog#36` (head `5899604`, base `main`) adds migrations `0040`+`0041`, tables
  `incident_clip_requests`/`incident_clip_chunks`, the clip-request RPCs, agent `incident_evidence.py`, and
  the native AI drivers. Not on `main`/production yet. New migrations here start at **0042** (§20/§21).

---

## 5. Camera state model — INVENTORY and HEALTH are two independent axes (PROPOSED)

A configured channel whose camera was removed from the recorder is fundamentally different from a present
camera that is down. Track both, plus recording, separately.

**`inventory_state` — does the channel exist/enabled on the recorder?** (from channel enumeration + enable flag)
| Value | Meaning |
|---|---|
| `PRESENT` | channel exists and is enabled on the recorder |
| `MISSING` | recorder no longer lists this channel (camera/channel removed) |
| `DISABLED` | channel exists but is disabled on the recorder |
| `UNKNOWN` | cannot enumerate (NVR unreachable/auth-failed, or agent unreachable) |

**`health_state` — of a `PRESENT` channel, can it deliver usable video?** (from the hybrid detector §8)
| Value | Entry condition |
|---|---|
| `OPERATIONAL` | agent reachable + NVR reachable+authed + channel PRESENT + a recent probe returned a valid, fresh image within the OK window |
| `DEGRADED` | probes mostly succeed but with a meaningful failure rate / stale (frozen) frames / high latency over a rolling window |
| `OFFLINE` | PRESENT but K consecutive probe failures / native video-loss while the NVR itself is reachable+authed |
| `UNKNOWN` | an upper layer is down (agent `AGENT_UNREACHABLE`, or NVR unreachable/auth-failed), OR inventory is `MISSING`/`DISABLED` |

**`recording_state` — is the NVR actually recording this channel?** (amendment: a JPEG ≠ recording)
`RECORDING` / `NOT_RECORDING` / `STORAGE_FAULT` / `UNKNOWN`. Read from the vendor storage/record API where
exposed (Dahua storageManager/recordMode; Hik ISAPI Storage/recordStatus); where the API is not available
or not validated, it is explicitly `UNKNOWN` — WatchLog never infers "recording" from a snapshot. An
HDD/storage failure or recording-disabled while snapshots still work is a serious surveillance failure and
must surface at Reliability level.

Anti-flap transitions (tune on field data): `OPERATIONAL→OFFLINE` after **K=3** consecutive probe failures
(or one native video-loss); `OFFLINE→OPERATIONAL` (RECOVERED) after **J=2** consecutive good probes;
`→DEGRADED` when the rolling-window failure ratio is between ~10% and the OFFLINE threshold, or on
stale-frame/high-latency, leaving after **M** clean probes. Any upper-layer outage forces dependent
cameras to `UNKNOWN` (never `OFFLINE`) and freezes camera-availability accounting while accruing
monitoring‑coverage debt (§9). Every transition writes a row with a `reason_code`
(`OK|PROBE_TIMEOUT|PROBE_HTTP_ERROR|STALE_FRAME|HIGH_LATENCY|VIDEO_LOSS|CHANNEL_MISSING|CHANNEL_DISABLED|NVR_UNREACHABLE|NVR_AUTH_FAILED|STORAGE_FAULT|RECORDING_DISABLED|AGENT_UNREACHABLE`).

## 6. Layered health model — `Agent → NVR connectivity/auth → NVR recording/storage → Camera/channel → Analytics` (PROPOSED)

Cardinal rule: **assess each layer independently, attribute a fault to the lowest layer actually down, and
everything below an outage is `UNKNOWN`, never `OFFLINE`.**

| Layer | Healthy signal | Source / today |
|---|---|---|
| Site Agent (liveness) | heartbeat within TTL | `agents.last_seen_at`; **AGENT state is SERVER-DERIVED by a watchdog** (§9) — an offline agent cannot report itself offline |
| NVR connectivity/auth | agent reached + authed the recorder | agent detects today, **not transmitted** (PARTIAL) |
| NVR recording/storage | HDD healthy + recording on | **MISSING** — read where API exposes it, else `UNKNOWN` |
| Camera/channel | recent good probe / no native video-loss (of a `PRESENT` channel) | **MISSING** |
| Analytics | detector loaded + sampling ok | computed locally (`analytics_status.json`), **not sent** |

Resolved states the model must produce (never a single "Online"):
- Agent watchdog fires → `AGENT_UNREACHABLE` (**cause unknown from the cloud**); NVR + cameras `UNKNOWN`.
- Agent online, NVR unreachable → `AGENT_ONLINE` + `NVR_UNREACHABLE`; cameras `UNKNOWN (recorder)`.
- Agent online, NVR auth failed → `AGENT_ONLINE` + `NVR_AUTH_FAILED`; cameras `UNKNOWN (credentials)` —
  **do not tell the customer a camera is broken; the recorder login needs fixing.**
- Agent + NVR reachable, recording off/HDD fault → cameras may be `OPERATIONAL` but `recording_state` flags
  `NOT_RECORDING`/`STORAGE_FAULT` (a Reliability fault).
- Channel 7 fails while NVR healthy → camera 7 `OFFLINE/DEGRADED`, cameras 1–6/8 `OPERATIONAL`.
- Channel removed on recorder → inventory `MISSING` (not `OFFLINE`).

## 7. Connectivity / machine-moved behaviour (Cases A–E)

| Case | Situation | Truthful state | Today | Gap |
|---|---|---|---|---|
| A | PC powered off | server watchdog opens `AGENT_UNREACHABLE` (**cause unknown**); cameras `UNKNOWN` | agent liveness EXISTS; portal derives offline >30m; cameras render "silent" | server watchdog + UNKNOWN attribution MISSING |
| B | PC on, internet lost | same `AGENT_UNREACHABLE` from the cloud; **locally**: NVR monitoring continues, events buffered, auto-reconnect + reconcile | spool buffers events+stills; at-least-once reconcile; dedupe absorbs re-sends — **EXISTS** | only cloud-visible difference from A is later local evidence; health transitions must reconcile (§9/§18) |
| C | PC on, off the CCTV LAN | `AGENT_ONLINE` + `NVR_UNREACHABLE`; cameras `UNKNOWN (recorder)` | NVR-unreachable detected in logs only | not transmitted / not distinguished — MISSING |
| D | NVR reachable, creds invalid | `AGENT_ONLINE` + `NVR_AUTH_FAILED`; cameras unverifiable | auth breaker detects + backs off (log only) | not transmitted — MISSING |
| E | one channel fails | NVR healthy; camera X offline/degraded; others healthy | no per-channel probe | MISSING |

**A vs B cannot be distinguished from the cloud** — both are `AGENT_UNREACHABLE (cause unknown)` until local
evidence (spooled logs/transitions on reconnect) tells us which. The live site is currently exhibiting
intermittent liveness (§1) and is therefore reported exactly this way — undiagnosed.

## 8. Health-probing strategy — hybrid, bounded, vendor-authenticated (PROPOSED)

- **Native fault events first (immediate).** Consume the recorder's own `video_loss`/camera-disconnect
  (and, with PR #36, native AI) events for an immediate `OFFLINE` signal — the drivers already parse these.
  This is faster and cheaper than polling.
- **Bounded snapshot probe to CONFIRM + cover quiet cameras.** Reuse `driver.get_snapshot()` (HTTP
  CGI/ISAPI, already field-proven) on a bounded schedule to confirm availability and to test cameras that
  emit no events. A valid JPEG within a timeout = channel up.
- **Vendor-specific authenticated health/storage endpoint** for NVR connectivity/auth and
  recording/storage state — NOT a generic reachability ping. One recorder-level check per cycle explains a
  whole-recorder outage without probing 8 channels.
- **Freshness / latency (Phase-B refinement):** hash consecutive probe frames (frozen-frame →
  `STALE_FRAME→DEGRADED`); record probe RTT (sustained high → `HIGH_LATENCY→DEGRADED`).
- **Bounded load (protect the recorder/network):** default probe interval **120–300 s per camera**,
  jittered, with a max-concurrent cap and a fair round-robin (mirror `FairSampler`); recorder-level check
  before per-channel probes; all intervals config-driven. Probing must stay far below live-stream load.
- **Never infer health from events** — a quiet camera is healthy; only probes/native-faults move state.

## 9. Health data model + persistence (PROPOSED)

**Local (agent):** run the inventory/health/recording state machines **on the agent**, and persist
**state transitions + current state + periodic checkpoints** locally — **not** every raw probe, and **not**
through the event spool (raw probes are ephemeral and must not compete with evidence events). During a
cloud outage, retain transitions locally and **reconcile them on reconnect** (the outage ledger is the
product). Transmit via a new RPC **`wl_report_health(agent_id, agent_key, snapshot)`** carrying the layered
current state + any accumulated transitions/checkpoints.

**Cloud tables (migration 0042+, all SECURITY DEFINER, RLS-sealed, agent-key authed):**
- `camera_inventory` (current `inventory_state`/reason/updated_at per camera) + `camera_inventory_transitions`.
- `camera_health` (current `health_state`, `recording_state`, reason_code, last_probe_at, last_ok_at,
  outage_started_at, latency_ms, consecutive_failures) + `camera_health_transitions` (append-only outage ledger).
- `nvr_health` (per site: `nvr_reachable`, `nvr_auth_ok`, `recording_state`, `storage_state`, last_ok_at)
  + `nvr_health_transitions`.
- `agent_unreachable_intervals` (server-derived: opened/closed by a watchdog scanning `last_seen_at` vs a
  TTL; `started_at`, `ended_at`, `cause = unknown` unless later local evidence sets it).
- `monitoring_coverage` / `unverified_intervals` (per site + per camera: windows WatchLog could not observe
  — from agent-unreachable, nvr-unreachable, auth-failed, and cloud-link gaps).
- `operational_faults` (fault lifecycle `open|acknowledged|resolved`, kind, layer, camera/site, opened_at,
  resolved_at) — **DISTINCT from security incidents** (§10).

**Server watchdog:** a scheduled function (pg_cron where available; otherwise an app-side ticker) opens an
`agent_unreachable_intervals` row when `now()-last_seen_at > TTL` and closes it on the next heartbeat;
while open, dependent NVR/camera health reads as `UNKNOWN` and `unverified_intervals` accrues.

**Dual availability (read RPCs compute from the ledgers):** report BOTH
- **Camera verified availability** = up-time ÷ **monitored** time (excludes unverified windows), and
- **Monitoring coverage** = monitored time ÷ wall-clock.
Never claim 100% availability for a period WatchLog was offline. Per camera also expose: current state,
last successful check, outage start/duration, uptime % 24h/7d/30d over monitored time, total downtime,
outage count, longest outage, degraded time, last recovery, recorder/channel identity, reason_code,
recording_state, and the coverage/unverified figure for the window.

Customer output this enables: "Cameras operational: 6/8 · Camera 4: 99.2% of monitored time, 3
interruptions · Monitoring coverage 94.6% (1h 18m unverified) · Camera 7: offline since 02:16 · Camera 8:
degraded · Camera 9: removed from recorder."

## 10. Raw detection → event → incident, and faults vs incidents (PROPOSED)

```
raw frame detection (YOLO)              [EXISTS: vision.py]
  → confidence gate (per-rule min)      [MISSING: only global ~0.35]
  → tracker dedup (centroid TTL)        [EXISTS: analytics.py]
  → measurement / event                 [EXISTS: analytic_events / events]
  → rule evaluation (schedule/zone/dwell/direction/duration + confidence + cooldown)  [PARTIAL→BUILD]
  → session grouping                    [MISSING]
  → SECURITY INCIDENT (if severity/promote) with lifecycle   [PARTIAL: promote flag only]
  → evidence: still now / bounded clip (D2)  [still EXISTS, clip via PR #36]
  → notification / routing by severity  [MISSING]
  → customer review feedback            [MISSING → BUILD]
```
Two DISTINCT domain records feeding one **"Needs Attention"** view: **operational faults** (§9 — NVR/camera
outage, recording/storage fault) and **security incidents** (after-hours intrusion, restricted-zone, etc.).
They have different lifecycles and must not be conflated. Thousands of near-identical detections must never
become thousands of customer events: enforce **confidence gate + cooldown/refractory + session grouping**
before anything is surfaced. **Customer review feedback** (useful / false-positive / duplicate-noise /
expected-activity) is captured on events/incidents and feeds per-customer rule tuning.

## 11. Useful analytics catalogue (strict usefulness review)

**A. Surveillance Reliability — "Can I trust my CCTV?"** (highest value; mostly to BUILD)
- cameras operational/degraded/offline (`X/8`), inventory MISSING/DISABLED, **recording/storage state** —
  the #1 answer — **MISSING**. BUILD (§5/§9).
- camera verified availability % + **monitoring coverage %**, outages, longest outage, recurring pattern —
  **MISSING**. BUILD.
- NVR uptime/recording, agent uptime — **PARTIAL/MISSING**. BUILD from §9.

**B. Security Intelligence — "What happened?"**
- after-hours person/vehicle **with attribution (camera + time band + session)** — **PARTIAL** → upgrade to
  intelligence (§10). restricted-zone / line-crossing — **EXISTS** (agent rules). KEEP.
- incidents requiring review + evidence still/clip — **PARTIAL** (promote flag → event; no lifecycle). BUILD lifecycle.
- raw people/vehicle counts — **EXISTS but low-value raw**; DEMOTE to trend inputs (§12), never headline.

**C. Management Reporting — "What must the manager know?"**
- surveillance availability %, coverage %, cameras operational, per-camera downtime, recording status,
  after-hours events, incidents, incidents-requiring-review, agent uptime — **MOSTLY MISSING** (depends on A
  + §10). BUILD exception-first with completeness (§17).

## 12. Analytics to avoid / demote / fix

- **Never build:** vanity dashboards, chart walls, raw detection dumps, a single "Online" indicator that
  hides the layer at fault.
- **Demote to internal/aggregate:** raw visitor/vehicle/zone/checkout counts as headline figures — keep as
  trend inputs; `checkout_peak` stays labelled "people present, not sales."
- **Fix:** report after-hours window → use the customer schedule (not 08–19); `severity` must actually
  route/gate (today stored and ignored).
- **Guard:** enforce confidence + cooldown + grouping + review feedback before anything becomes a customer
  event.

## 13. Rule-engine architecture — extend, don't replace (PROPOSED)

Keep agent-side evaluation (`analytics.py` works and keeps video on-site). Extend schema + engine with the
missing primitives. **WHEN** (detection type / camera-offline / nvr-offline / recording-off / repeated
degradation / no-expected-activity / occupancy threshold) · **IF** (camera(s), schedule, zone/geometry,
direction, min duration/dwell, **min confidence [NEW]**, **cooldown/refractory [NEW]**, repetition) ·
**THEN** (create security incident by severity, save still, **`extract_footage` [D2, opt-in]**, notify by
severity, tag/suppress). Health rule types (`camera_offline`, `nvr_offline`, `recording_off`,
`camera_degraded`) evaluate **server-side** off the §9 health stream (no video needed). Three tiers:
predefined WatchLog templates (§14) · customer-authored (Studio, today) · advanced/custom needing a
capability we lack (e.g. ANPR, face — explicitly out of scope).

## 14. Predefined analytics templates (PROPOSED)
After-hours person at boundary · vehicle outside delivery hours · person in restricted zone ·
crowd/occupancy threshold · camera offline > N min · NVR offline · NVR not recording / storage fault ·
repeated camera degradation · no expected activity during an operational window. Each a `monitoring_rules`
row with sensible defaults (schedule, confidence, cooldown, severity, actions) enabled in one click.

## 15. Custom analytics model (PROPOSED)
Studio rules compile to the same `monitoring_rules` schema (geometry on a config still, class, schedule,
dwell, confidence, cooldown, actions). Anything needing an unavailable capability is flagged "requires
WatchLog engineering / not supported" rather than failing silently.

## 16. Incident-footage architecture — PR #36 (D1) + opt-in automation (D2) (PROPOSED)

Normal path (EXISTS): detection → event metadata → still (bytea) → WatchLog.

**D1 — manual on-demand footage: EXISTS as PENDING‑MERGE in PR #36.** Owner/Admin selects an incident and
requests footage (default 10 s-pre / 20 s-post). Lifecycle `Queued→Retrieving→Ready` via
`wl_request_incident_clip` / `wl_incident_clip_status` / `wl_incident_clip_chunk` (customer) and
`wl_agent_claim_clip_requests` / `wl_agent_upload_clip_chunk` / `wl_agent_complete_clip` /
`wl_agent_fail_clip` (agent); agent `incident_evidence.py` `footage_worker` calls `driver.get_clip()`,
SHA-256s, and uploads in **chunks** (`incident_clip_chunks`). Bounded: explicit request only, ≤ 60 s,
≤ 32 MiB, 24 h temporary access (`wl_prune_incident_clips`), byte+SHA-256 manifest match (fail-closed),
recorder creds never in the cloud, original recording stays on the recorder. Dahua returns native `.dav`.
**D1 work = fetch/merge-reconcile PR #36 and field-prove it; do not re-author 0040/0041 or a parallel
footage schema.**

**D2 — automatic, opt-in, rule-driven footage.** Add `extract_footage` as a rule action (§13) that calls
PR #36's `wl_request_incident_clip` when a matching incident fires — allowed **only when an Owner/Admin has
explicitly enabled that action for that rule**, with window/retention/storage configured in advance. This
delivers "configure a rule → incident occurs → bounded footage auto-extracted" while preserving the
privacy boundary (explicit opt-in per rule; same bounds and audit as D1).

Storage: the PR #36 pilot stores clips as **chunked DB rows**; object storage + signed URLs is a **future
optimization**, not the pilot architecture. Cross-vendor caveats to design for: Dahua vs Hik playback APIs,
firmware differences (Dahua may return a segment larger than requested — "GO WITH LIMITATIONS"),
recorder-local timezone/timestamp alignment, clip-availability delay, per-clip storage cost, and an
`Unsupported` state that never fabricates a clip or exposes an RTSP credential URL.

## 17. Reporting architecture — exception-first, completeness-aware (PROPOSED; UX later)

Lead with *"what requires my attention?"* then drill down. Reuse the runner + delivery.
- **Daily operational brief** (restructure today's report): Reliability (X/8 operational, recording status,
  worst downtime, **monitoring coverage**), Security (incidents requiring review, after-hours by
  camera/band, unusual events), System (agent uptime, NVR availability), Notable activity (a spike vs
  baseline). After-hours uses the customer schedule.
- **Weekly management report** (NEW), **site-health report** (NEW), **incident report** (NEW: one incident,
  still + clip, rule, timeline, reason/audit).
- **Every report exposes data completeness** — monitoring coverage, unverified periods, camera-health gaps
  — so a precise-looking report can't rest on incomplete observation.
- Reports pull from §9 (health/coverage) + §10 (faults + incidents); the reporting layer already works.

## 18. Offline buffering / recovery analysis
- Events + stills: durable spool, at-least-once, dedupe-safe reconcile — **EXISTS** (`spool.py`); no change
  for Case B events.
- Health: the agent keeps its state machine + **transitions/checkpoints locally** and **reconciles them on
  reconnect** — health is NOT pushed through the event spool. A cloud-link gap must be attributed to
  monitoring coverage (§9), not fabricated as a camera outage.
- Analytics measurements: separate spool — **EXISTS**.

## 19. Portal UX information hierarchy (PROPOSED — exception-first; UX built in Phase F)
Ordered by the locked product hierarchy:
1. Reliability status line: `AGENT · NVR (reach/auth/recording) · CAMERAS (operational X/8, inventory) ·
   COVERAGE%` — an honest resolved state per layer, never one "Online".
2. Needs Attention — UNION of **operational faults** (offline/degraded/recording/agent/NVR) and **security
   incidents to review**.
3. Availability — per-camera verified availability % + monitoring coverage + outage timeline.
4. Security activity — after-hours/zone events with attribution; raw counts demoted to trends.
5. Drill-down: camera → timeline → event → incident → still → clip.

## 20. Database / schema changes required (PROPOSED, migration 0042+)
- **Phase A (health foundation):** `camera_inventory` (+ transitions), `camera_health` (health_state +
  recording_state, + transitions), `nvr_health` (connectivity/auth + recording/storage, + transitions),
  `agent_unreachable_intervals` (server-derived), `monitoring_coverage` / `unverified_intervals`,
  `operational_faults` (lifecycle). Ingest RPC `wl_report_health`; read RPCs computing verified
  availability + monitoring coverage from the ledgers; server watchdog function.
- **Phase B/C:** extend `monitoring_rules` (`confidence_min`, `cooldown_seconds`, `actions jsonb` /
  `rule_actions`); `review_feedback`; server-side health-rule evaluation.
- **Phase C/D2:** `extract_footage` rule action reusing PR #36's `wl_request_incident_clip`.
- All SECURITY DEFINER, RLS-sealed, agent-key authed (match `0004`). Reconcile with PR #36's `0040/0041`
  before landing schema.

## 21. Migration-number constraints
- Highest migration on `main` is **`0039`**. **`0040`/`0041` are owned by the OPEN PR #36** (head
  `5899604`, fetched to `pr36`) — present on that branch, not on `main`/production. **Do not re-create or
  renumber them.** All new migrations here start at **`0042`**. Production is understood to be at ~`0038`;
  **verify the actual applied migration level and PR #36 merge state before deploying any schema.**

## 22. Security / privacy implications
- Preserve the frozen Phase-2 guarantees (DPAPI recorder cred, no service key in the binary, agent-key
  auth, RLS-sealed tables). Health probing uses the already-stored recorder credential — no new secret;
  recorder creds never enter cloud request rows or logs (matches PR #36's footage boundary).
- Recorded video stays on the recorder by default; only bounded incident clips leave, on an explicit
  request (D1) or an opt-in rule action (D2), with download/view **audit** + authorization.
- Health/probe/coverage data is non-video metadata — safe to store/transmit. No new PII; no face identity.

## 23. Storage / cost implications
- Stills: bytea, plan-retention pruned — fine at current volume (32 stills / 321 KB in ~40 min).
- Clips: **chunked DB rows with 24 h temporary retention + 32 MiB cap** (PR #36 pilot) — bounded and cheap
  per clip; object storage + signed URLs is a **future optimization**, not the pilot. Never continuous.
- Health transitions/intervals are small text — cheap; keep long (uptime + coverage history is the product);
  prune raw probe data aggressively (it isn't stored server-side anyway).

## 24. Performance / NVR-load implications
- Probing is **bounded** (§8): 120–300 s/camera, jittered, max-concurrent cap, fair scheduler, a single
  recorder-level authenticated check before per-channel probes, native-fault-first — designed to stay far
  below live-stream load and never storm the recorder on a wide outage.
- Clip extraction is on-incident only (rare), bounded, retried, with timeouts.
- Server aggregates are on-demand today; add rollups later only if health/report queries grow.

## 25. Phase A — schema (authoritative)
Phase A adds exactly these, migration 0042+ (see §20 for RPCs):
1. **Camera inventory** — current `inventory_state` + `camera_inventory_transitions`.
2. **Camera health** — current `health_state` (+ `recording_state`, reason_code, probe metadata) +
   `camera_health_transitions` (outage ledger).
3. **NVR connectivity/auth** — `nvr_health.nvr_reachable`/`nvr_auth_ok` + transitions.
4. **NVR recording/storage health** — `nvr_health.recording_state`/`storage_state` + transitions.
5. **Agent watchdog / unreachable intervals** — server-derived `agent_unreachable_intervals`.
6. **Monitoring coverage / unverified intervals** — `monitoring_coverage` + `unverified_intervals`.
7. **Operational faults** — `operational_faults` lifecycle (distinct from security incidents).

## 26. Phase A — acceptance criteria (evidence-based; prove each on the field NVR)
1. **Camera removed from the recorder** → `inventory_state MISSING` — distinct from a PRESENT-but-OFFLINE camera.
2. **Camera disabled on the recorder** → `inventory_state DISABLED` — distinct from `health OFFLINE` (broken).
3. **NVR HDD/storage failure while snapshots still work** → `recording_state STORAGE_FAULT` surfaced as a
   Reliability fault; camera not reported "fully operational".
4. **NVR recording disabled while camera video reachable** → `recording_state NOT_RECORDING`; camera
   `health OPERATIONAL` but recording flagged.
5. **Native video-loss** → immediate `OFFLINE` (before the next probe).
6. **Bounded probe** confirms availability and recovery (RECOVERED after J good probes).
7. **PC/cloud loss** → server watchdog opens `AGENT_UNREACHABLE` (**cause not guessed**); cameras `UNKNOWN`.
8. **CCTV LAN loss (agent still cloud-connected)** → `AGENT_ONLINE` + `NVR_UNREACHABLE`; cameras `UNKNOWN (recorder)`.
9. **Wrong recorder credentials** → `NVR_AUTH_FAILED`; cameras `UNKNOWN (credentials)`, not camera-broken.
10. **Monitoring coverage + uptime math is correct** — verified availability is over *monitored* time,
    coverage is over wall-clock, unverified windows are excluded from availability and counted in coverage.
11. **No flapping** — a 60 s connectivity blip does not toggle state (hysteresis holds).
12. **Transitions reconcile after internet recovery** — locally-retained transitions upload and the
    cloud-link gap is recorded as unverified coverage, not a fabricated camera outage.
- Windows Security Gate, full CI, installer contracts remain green; a new RC builds only after tests pass.

---

## Phased plan (authoritative — do not start until approved)

- **Phase A — Health foundation.** Layered health model (§6) with `inventory_state` + `health_state` +
  `recording_state` (§5), hybrid native-fault + bounded vendor-authenticated probing (§8), local health
  persistence — transitions/checkpoints, not event-spool (§9/§18), server-side agent watchdog + unreachable
  intervals (§9), dual availability + monitoring coverage (§9), operational-faults domain (§10), schema §25,
  acceptance §26, Reliability status line + Needs-Attention (§19 items 1–2).
- **Phase B — Event intelligence.** Confidence gate + cooldown/refractory + session grouping + severity
  routing (§10), **customer review feedback loop** (§10), demote raw counts (§12), after-hours → customer schedule.
- **Phase C — Rule engine.** Extend `monitoring_rules` (confidence/cooldown/`actions`), server-side health
  rules, predefined templates (§14), keep custom authoring (§15), define the `extract_footage` action.
- **Phase D1 — Manual incident footage.** Fetch/merge-reconcile PR #36 and field-prove its Owner/Admin
  on-demand retrieval (bounds, integrity, authz, Dahua `.dav`) — no re-authoring of 0040/0041.
- **Phase D2 — Automatic opt-in footage.** `extract_footage` rule action on top of PR #36's request RPC,
  enabled only per rule by an Owner/Admin, with configured window/retention/storage.
- **Phase E — Reporting.** Exception-first daily brief + weekly + site-health + incident reports, each
  carrying data completeness (§17).
- **Phase F — Product UX.** Exception-first portal hierarchy + drill-down (§19).

Sequencing: A answers the customer's #1 question ("can I trust my CCTV?") and everything the field site
needs now; B/C turn detections into intelligence; D1/D2 deliver evidence footage (D1 first, on the existing
PR #36 primitive); E/F deliver reporting + UX. Each phase ships behind its own tests + a fresh RC; RC8 stays
frozen; `/latest/` and `main` stay untouched until signing + acceptance.
