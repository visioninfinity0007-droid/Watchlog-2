# WatchLog Archive / Gap Recovery — Acceptance Status

## Authority

Authoritative repository: `Alkalid-security/Watchlog`

Authoritative branch: `main`

Current recovery source version: **5.0.23**

Merged implementation commit:

`eecdc197468b9bf15ddaf2b3e4b34f8a5ed4d92b`

Production database migration:

`0120_recovered_snapshot_timestamps.sql`

Production migration status: **APPLIED**

---

## Product contract

A WatchLog/Internet/recorder observation gap is DELAYED intelligence, not silently lost
intelligence, whenever the NVR retained the relevant recording and its archive API can be read.

For a recoverable gap, WatchLog must:

1. retain the last timestamp at which the **recorder transport** was actually observed;
2. never advance that marker merely because cloud heartbeat still works;
3. detect the missed interval after recorder contact returns;
4. attach the actual camera/channel list;
5. query the recorder archive read-only;
6. enumerate recorded segments in bounded chunks;
7. retrieve a bounded historical clip/frame;
8. decode a representative JPEG using the bundled FFmpeg;
9. recover visual checkpoints every **300 seconds by default** across long segments;
10. preserve quiet/no-detector frames as `recovered_snapshot`;
11. preserve detected activity as `recovered_activity`;
12. keep the original footage time as `device_ts`;
13. upload the JPEG through the normal event spool;
14. store `snapshots.captured_at = device_ts`;
15. enqueue the recovered image into the normal visual-review queue;
16. checkpoint progress after every recovery chunk;
17. deduplicate by segment + sample timestamp across restart/retry;
18. yield to live monitoring whenever live backlog is high;
19. preserve any spool-overflow time range before deleting old local rows;
20. reconcile that overflow range from NVR archive after connectivity returns;
21. return `partial` / `unknown` when footage exists but frames cannot be decoded;
22. never fabricate historical images, activity or successful recovery.

---

## Vendor paths

### Dahua

Implemented:

- native archive finder/search;
- bounded `loadfile.cgi` historical clip retrieval;
- timezone/device-clock normalization;
- segment enumeration;
- representative JPEG extraction through bundled FFmpeg;
- gap snapshot/activity recovery.

Evidence boundary:

Dahua archive retrieval has prior pilot/field evidence, but the exact 5.0.23 installer must
still pass the current field acceptance before fleet promotion.

### Hikvision

Implemented:

- ISAPI `/ISAPI/ContentMgmt/search`;
- recorder-returned `playbackURI`;
- bounded `/ISAPI/ContentMgmt/download`;
- GET/POST firmware compatibility;
- by-time fallback;
- 32 MiB clip bound;
- segment enumeration;
- representative JPEG extraction through bundled FFmpeg;
- gap snapshot/activity recovery.

Evidence boundary:

The 5.0.23 code and packaged decoder are validated, but the exact Chai Wala
`DS-7608NI-Q1` has **not yet physically proven** the new archive-download path.
Build 69's older path returned unsupported. Do not convert packaged proof into a false
hardware claim.

---

## Windows packaged validation — Build 100

Validation repository:

`visioninfinity0007-droid/Watchlog-2`

Windows Release:

**Build 100 / version 5.0.23**

- source SHA:
  `377462fbd36d834d52864838803299a2a97eb7af`
- run id:
  `36373435504`
- artifact:
  `WatchLog-Windows-100`
- artifact id:
  `10950610443`
- artifact ZIP digest:
  `sha256:35e46bccd8549aa844932970d266c26badcc14ebe358cee50b1c375b73e86a9e`
- `watchlog-agent.exe` SHA-256:
  `1EC1C2C685E24907822CA4550FEB057D2D49996159B33C6740683A43AE313B84`
- `watchlog-setup-ui.exe` SHA-256:
  `4C88206420F97AC1BDF28A824F4B0F1CAE675F374E1791C1D163911E3CC5F297`
- `WatchLog-Setup.exe` SHA-256:
  `D40C5622E6DB30BE064FD273624281A08F404112ADD274B7BACE851A558CD42B`

Release #100 passed.

Its frozen connector self-test explicitly calls `recovery_ai.decoder_selftest()`.
That test creates a tiny video with the bundled FFmpeg and decodes it back to JPEG.
Therefore packaged historical-frame decode is **proven inside the Windows EXE**, not inferred
from source dependencies.

---

## Cloud storage / timeline proof

Production `wl_ingest_events` now:

- preserves event `device_ts`;
- inserts the decoded image into `snapshots`;
- explicitly sets `snapshots.captured_at` to the same historical `device_ts`;
- retains the existing 3 MiB snapshot bound.

The existing `snapshots` trigger then enqueues `snapshot_visual_reviews` with
`captured_at = new.captured_at`.

Therefore recovered archive images use their original footage time in the review/timeline
pipeline instead of the later recovery/upload time.

The realtime camera-preview signal uses `greatest(existing,new)`, so an old recovered
historical frame cannot replace a newer live camera preview.

---

## Resilience proof

Recovery state is durable:

- local event spool is SQLite WAL;
- recovery interval checkpoints after each bounded chunk;
- seen keys survive retry/restart;
- spool overflow persists a separate recovery-gap interval before rows are trimmed;
- stale acknowledgement cannot clear a newly extended overflow gap.

Live monitoring has priority over archive backfill.

---

## Mandatory physical acceptance before declaring hardware 100%

### Hikvision pilot

Use the exact 5.0.23 candidate on a Hikvision NVR and prove:

- archive search returns the requested channel/time;
- bounded clip bytes are returned;
- bundled decoder produces JPEG;
- a forced >3 minute observation gap opens a recovery interval;
- recovered snapshots appear at original historical timestamps;
- a quiet recovered frame is retained;
- an activity recovered frame is retained;
- recovery resumes correctly after Agent restart;
- current live monitoring remains responsive during recovery.

For Chai Wala specifically, the target recorder is `DS-7608NI-Q1`.

### Dahua pilot

Repeat the same acceptance using native Dahua CGI archive retrieval.

### Failure acceptance

Also prove:

- no recording -> honest empty/unrecoverable;
- unsupported firmware -> honest unsupported/partial;
- corrupt/unreadable media -> no fabricated snapshot and partial/unknown recovery;
- Internet drops again mid-recovery -> checkpoint/retry without duplicates.

---

## Promotion rule

Software implementation + packaged decoder: **COMPLETE**

Production timestamp/cloud queue path: **LIVE**

Exact Hikvision and Dahua 5.0.23 hardware acceptance: **REQUIRED BEFORE FLEET PROMOTION**

Build 69 remains the live discovery/connectivity reference until the successor passes the
full field matrix. Do not replace a working Build-69 site merely to satisfy a version number.
