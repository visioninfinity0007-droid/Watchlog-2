# WatchLog Footage/Archive and Gap/Recovery — Status

Status date: 2026-10-06. Line under test: release candidate **5.1.1** (not promoted, no
artifact, installed nowhere; `docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md` section 0).

This document holds the footage/archive and gap/recovery status and the physical proofs still
owed. It does not repeat the install procedure
(`docs/runbooks/WINDOWS_RECORDER_FIELD_ACCEPTANCE.md`, Stage M runs the proofs below) or the
release status (source of truth).

Status words: NOT STARTED, FIXED LOCALLY, CI VERIFIED, INSTALLER VERIFIED, FIELD VERIFIED,
PRODUCTION VERIFIED, BLOCKED. CI VERIFIED means the repository CI suite reproduced locally; no
5.1.1 commit has run on GitHub CI.

---

## 1. Four separate states

Recording, storage, archive availability and gap/recovery are four different facts. WatchLog
reports each on its own; none is inferred from another, from a heartbeat, or from a still.
**Unknown stays Unknown**: it is never shown or counted as OK.

| State | Values | What decides it | Never inferred from |
|---|---|---|---|
| Recording (per camera) | `recording`, `not_recording`, `storage_fault`, `unknown` | the recorder's own record/storage API (`recording_model.py`) | a still or live image; a recording schedule or mode (configuration only); camera video health; a heartbeat |
| Storage / HDD (per recorder) | `ok`, `degraded`, `fault`, `unknown` | the recorder's storage API and storage events | a recording state; silence. Low space is `degraded`, not `fault`; unsupported or unreadable is `unknown`, never `ok` |
| Archive availability (per recorder, camera and window) | archive proof: `verified`, `available_frame_unverified`, `empty`, `unsupported`, `failed`, `unknown` (`site_status.py`); per request: footage delivered, unsupported, or failed (retryable unless the recorder refused) | an actual archive read through the transport the runtime uses (incident clip, archive proof, recovery claim) | recording state (recording now does not prove the archive can be read, and the reverse) |
| Gap / recovery (per window) | coverage `LIVE`, `RECOVERED`, `UNVERIFIED`; interval outcome `recovered`, `partial`, `unrecoverable` | the recorder's last-live marker, recovery intervals and what the archive actually returned | a cloud heartbeat; an archive that was not read; hours with no recording (those stay unknown, never recovered) |

Further rules from the code:

- When the recorder is unreachable or refuses login, recording and storage are `unknown` with
  that cause. A missing or disabled channel has no recording state (`unknown`, never
  `not_recording`).
- A storage `fault` (no usable storage) makes the channel `storage_fault`; `degraded` does not.
- While the Hikvision live collector stands in for a full recorder read, recording and storage
  are not re-observed from the stream, and a present-tense fault is reported as not observed.
- Unmonitored time is never presented as "nothing happened".

## 2. Status at a glance

| Area | 5.1.1 code | Installer | Field | Evidence |
|---|---|---|---|---|
| Recording and storage classification, Unknown kept | CI VERIFIED | NOT STARTED | NOT STARTED | `test_recording_model.py`, `test_recording_health.py`, `test_recording_storage_contract.py`, `test_recording_current_multi_recorder.py` |
| Incident footage (bounded clip) | CI VERIFIED | NOT STARTED | NOT STARTED | `test_hikvision_archive_*`, `test_dahua_archive_*`, `test_incident_clip_stale_recovery.py` |
| Incident still on request | CI VERIFIED | NOT STARTED | NOT STARTED | `test_incident_still_stale_recovery.py` |
| Archive per recorder (breaker, auth back-off, isolation) | CI VERIFIED | NOT STARTED | NOT STARTED | `test_archive_breaker_*`, `test_archive_driver_auth_backoff.py`, `test_hikvision_archive_recorder_isolation.py` |
| Gap detection (last-live from the event stream) | CI VERIFIED | NOT STARTED | NOT STARTED | `test_shipped_last_live.py`, `test_recovery_rpc_contract.py` |
| Gap recovery from the archive | CI VERIFIED | NOT STARTED | NOT STARTED | `test_recovery*.py`, `test_spool_recovery_gap.py`, `e2e_recovery_*_pg.py` |
| Coverage truth (LIVE / RECOVERED / UNVERIFIED) | CI VERIFIED | n/a | NOT STARTED | `test_coverage_model.py`, `e2e_multi_recorder_coverage_pg.py`, `e2e_multi_recorder_reporting_coverage_pg.py` |
| Database side (recorder-scoped recovery, evidence and coverage; migrations 0146-0157) | n/a | n/a | PRODUCTION VERIFIED (owner, 2026-10-06; schema applied only) | source of truth section 0.7 |

What production has shown on the fielded Agents (read-only audit, 2026-10-06 08:20-09:30 UTC,
before the migrations were applied):

- No recovery interval and no RECOVERED or UNVERIFIED window had ever been written for any site.
- Al-Khalid's recorder read as reachable and authenticated while it sent no events or stills.
- Incident clips: Chai Wala (5.0.17) returned "unsupported" with no bytes; HASCO (5.0.26)
  returned a failure.
- Chai Wala after the 2026-10-06 migration: 4 `visual_sample` / `periodic_snapshot` events. That
  proves legacy periodic ingestion only, not native alarms, clip/archive, recovery or recording
  truth.

Nothing in this document is FIELD VERIFIED for any 5.x build. The only recorder field evidence on
record is Dahua DH-XVR1B08-I on Agent 0.4.1 (dahua-cgi, 2026-09-10), where clip retrieval was
unsupported.

## 3. Vendor paths (implemented, not field-proven)

Dahua (native CGI): archive search pages through all results, `loadfile.cgi` bounded clip
retrieval with a total time budget, segment times on the Agent clock, representative JPEG through
the bundled FFmpeg. On an ONVIF-live Dahua site (Al-Khalid) the native archive is used only for
cameras with a label-consistent ONVIF-to-native channel map (that equivalence is unverified on
hardware).

Hikvision (ISAPI): `/ISAPI/ContentMgmt/search`, recorder-returned `playbackURI`, bounded
`/ISAPI/ContentMgmt/download` with GET/POST compatibility and by-time fallback, downloads bounded
to the requested window, 32 MiB clip limit, 90 s total retrieval budget, typed refusal outcomes,
and archive calls serialised with the live stream by the per-recorder lock.

Clip clock (MNVR-035): an incident clip on an event uses the recorder clock unless that clock is
more than 5 minutes from every civil offset; the claim does not yet carry the event's
`clock_source` (server side not built), so a recovered event, or one uploaded by an Agent before
5.0.28, can miss by the recorder's drift (up to 5 minutes).

## 4. Footage/archive physical proofs (12 points, gate G8)

All NOT STARTED. Run per vendor (Hikvision, Dahua) on the exact artifact, under
`docs/runbooks/WINDOWS_RECORDER_FIELD_ACCEPTANCE.md` Stage M. Record recording state, storage
state and archive availability separately for every proof.

1. Archive search returns the requested channel and time window through the transport the
   runtime uses.
2. The per-recorder archive proof (Site Status, or `watchlog-agent.exe --recheck-archive-json`)
   reports archive availability truthfully (`verified` only with a decoded frame; otherwise
   `available_frame_unverified`, `empty`, `unsupported`, `failed` or `unknown`).
3. A bounded incident clip for a known camera and time is returned, and its window contains the
   event.
4. The clip comes from the right recorder and camera (on a two-recorder site, from the recorder
   that owns the camera).
5. The clip's placement in time is checked against the recorder clock; record the recorder clock
   (and, on an ONVIF-live site, `event_stream.last_clock_skew_s`) before judging a clip that
   missed.
6. The bundled decoder produces a JPEG from that recorder's footage (including DHAV/H.265 where
   the recorder uses it).
7. An incident still requested for a camera completes (the fielded Agents completed 0 of 7 at
   Chai Wala and 0 of 8 at HASCO).
8. A window with no recording returns an honest empty or unrecoverable result; nothing is
   fabricated.
9. A recorder or firmware that does not offer footage returns `unsupported` with customer-safe
   text (no recorder address, no internal detail).
10. A refused archive login backs off for that recorder only and does not lock the recorder out
    or interrupt live events.
11. Live events keep arriving while a clip downloads (no stream timeouts during retrieval).
12. On the ONVIF-live Dahua site, an incident clip through the mapped native archive covers the
    event, and an unmapped camera is refused rather than read from the wrong channel.

## 5. Gap/recovery physical proofs (15 points, gate G9)

All NOT STARTED. Same rules as section 4.

1. The recorder's last-live time advances from real event-stream activity (keep-alives included),
   not from the cloud heartbeat.
2. A forced recorder-LAN gap longer than 3 minutes (outage threshold 180 s) opens exactly one
   recovery interval for that recorder, keyed by camera, never by a guessed channel.
3. A controlled Agent or PC restart gap opens an interval in the same way.
4. The interval is claimed, and the archive is opened only for a claimed interval.
5. Segments are read in bounded chunks (one hour per chunk) and progress is checkpointed after
   each chunk.
6. Quiet recovered frames are kept as `recovered_snapshot`, one visual checkpoint about every
   300 s.
7. Frames with activity are kept as `recovered_activity`.
8. Recovered items carry their original footage time (`device_ts`, `snapshots.captured_at`) and
   recorder-archive provenance; nothing recovered is presented as live.
9. Recovered stills enter the normal review queue at their original time, and an older recovered
   frame never replaces a newer live camera preview.
10. Recovery resumes after an Agent restart without duplicates.
11. Live monitoring keeps priority: live events still arrive promptly while recovery runs.
12. An Internet drop in the middle of recovery is retried from the checkpoint without
    duplicates.
13. A local queue overflow keeps its time range as a recovery gap and that range is reconciled
    from the archive once connectivity returns.
14. The interval ends truthfully: `recovered`, `partial` or `unrecoverable`; hours with no
    recording stay unknown; corrupt or undecodable footage produces no fabricated image; archive
    read failures back off and close after a bounded number of claims.
15. The portal and the report show the window as LIVE, RECOVERED or UNVERIFIED to match the
    above, and on a two-recorder site one recorder's outage opens only that recorder's interval.

## 6. Earlier lines (record)

- 5.0.23 completed the archive/gap-recovery contract in source; Watchlog-2 Build 100 (5.0.23)
  proved the bundled FFmpeg decoder inside the packaged Windows EXE (frozen self-test). That is
  packaging proof, not hardware proof.
- Migration `0120_recovered_snapshot_timestamps.sql` (recovered stills keep their footage time)
  is applied in production.
- The 5.0.26-era packaged run loop did not start the automatic recovery worker (fixed from
  5.0.27, source of truth section 1A), which is consistent with production having no recovery
  interval.
- Build 69 / 5.0.17 returned "unsupported" for Hikvision archive download.
