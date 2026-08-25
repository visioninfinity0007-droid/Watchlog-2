# WatchLog

Turns an existing DVR/NVR into a monitored, reportable system — without
opening a single inbound port on the client's network.

A small agent runs on a Windows PC on the site LAN. It talks to the
recorder locally, and pushes event metadata outward to a hosted database
over HTTPS. Nothing listens. No port forwarding, no VPN, no static IP,
and the recorder's credentials never leave the site.

```
   Recorder on the site LAN                Hosted database
   (Hikvision / Dahua / ONVIF)             (Supabase, RLS sealed)
            |                                     ^
            | outbound                            | outbound HTTPS
            v                                     | publishable key only
   +--------------------+   +---------+   +-------------------+
   |  driver thread     |-->|  spool  |-->|  RPC functions    |
   |  streams events    |   | SQLite  |   |  enroll/heartbeat |
   +--------------------+   +---------+   |  cameras/ingest   |
                                          +-------------------+
```

---

## Status

**This is a working prototype, not a finished product.** Read the table
before drawing conclusions from a demo.

| Piece | State |
|---|---|
| Database schema, dedupe, multi-tenant keys | live and exercised |
| Agent API — enroll, heartbeat, cameras, ingest | live, verified over HTTPS |
| Enrollment: single-use codes, forged-key rejection | verified |
| Crash / restart / outage recovery | verified — 0 duplicates, 0 gaps |
| Local spool survives a dead uplink | verified |
| Analytics + daily report + site health | live, verified |
| One-file Windows `.exe` | verified end to end |
| Hikvision, Dahua, ONVIF drivers | **written; not yet run against real hardware** |
| Multi-tenant web portal | **not built** |
| AI false-alarm filtering | **not built** |

The two gaps that matter:

1. **No real recorder has been connected yet.** The drivers are written
   to the published ISAPI / CGI / ONVIF specifications and are exercised
   against protocol-accurate simulators in `sim/`, which has already
   caught real bugs. But a simulator written alongside a driver cannot
   prove that driver works on a physical unit. Every driver reports
   `verified_against_hardware = False` and says so at runtime. Run
   `--probe` against the first real device and expect to fix things.
2. **The portal is a single static page**, not the multi-tenant product.
   `viewer/index.html` is an operator view for development and demos.

---

## Supported recorders

| Driver | Covers | Protocol |
|---|---|---|
| `hikvision-isapi` | Hikvision, HiLook | ISAPI — HTTP/XML, digest auth, `alertStream` |
| `dahua-cgi` | Dahua, Imou, CP Plus and other Dahua-OEM units | CGI — HTTP, digest auth, `eventManager attach` |
| `onvif` | Uniview, Tiandy, most other ONVIF-conformant units | SOAP PullPoint |
| `mock` | bundled test device | HTTP polling |

Two native APIs plus ONVIF is what covers most of the installed base.
Native APIs are preferred where available because they expose richer
event types than ONVIF does on the same hardware. `nvr_driver = auto`
probes Hikvision, then Dahua, then ONVIF, and keeps the first that
answers.

**Not supported:** budget no-name recorders built on Xiongmai/Hisilicon
boards, which speak a proprietary binary protocol on port 34567 and
implement ONVIF poorly or not at all. The ONVIF fallback will not rescue
these; they need a separate driver.

Every vendor's event vocabulary is normalised to one set — `motion`,
`line_crossing`, `intrusion`, `tamper`, `video_loss`, `person`,
`vehicle`, `face`, `disk_error`, `disk_full` — so nothing downstream has
to know which brand a site runs.

---

## Quick start

Requires Python 3.11+ and `requests`.

**1. Configure credentials.** Create `.env` one level above this
directory (see `.env.example`), with the Supabase URL, publishable key,
and database connection for migrations.

**2. Apply the schema.**

```bash
python supabase/apply_migrations.py
```

**3. Mint an enrollment code — one per machine.**

```bash
python supabase/mint_code.py -n 1 --days 7
```

**4. Point the agent at a recorder.** Copy
`agent/watchlog.ini.example` to `agent/watchlog.ini` and fill it in.

**5. Identify the recorder before committing to it.**

```bash
python agent/watchlog_agent.py --probe
```

Prints the detected driver, model, firmware and channel list, then
watches 20 seconds for live events. Touches no cloud service and burns no
enrollment code. **This is the first thing to run against real hardware.**

**6. Run it.**

```bash
python agent/watchlog_agent.py
```

**7. Watch it.**

```bash
python -m http.server 8600 --directory viewer
```

Open `http://127.0.0.1:8600/` and enter the project URL and publishable
key.

---

## Testing without hardware

Three simulators, all stdlib-only, all outbound-compatible with the real
drivers:

```bash
python sim/hikvision_sim.py --port 8451     # ISAPI + digest + alertStream
python sim/dahua_sim.py     --port 8452     # CGI + digest + attach stream
python mock_nvr/mock_nvr.py --port 8420     # simple polling device
```

They reproduce the awkward parts of real devices deliberately: digest
auth challenges, alarms that repeat once a second for as long as they
last, and idle keep-alive traffic that must not be mistaken for events.

`tests/fake_postgrest.py` stands in for the database so the whole
pipeline can run with no cloud account at all.

---

## Building the Windows executable

```bash
powershell -ExecutionPolicy Bypass -File agent/build_exe.ps1
```

Produces `dist/watchlog-agent.exe`, a single ~15 MB file with no Python
dependency. Ship it alongside a `watchlog.ini` carrying that machine's
own enrollment code and recorder credentials.

The executable is **not code signed**. Windows SmartScreen will warn on a
downloaded copy, and some antivirus will quarantine it. Budget for a code
signing certificate before any wide rollout.

---

## Security model

Every table has row-level security enabled with **no policies**, so no
client key can read or write them directly. Agents reach the database
only through `SECURITY DEFINER` functions, authenticating with the
**publishable** key — which is public by design and grants nothing on its
own — plus a 256-bit per-agent secret minted at enrollment and stored
server-side only as a SHA-256 hash.

Consequences worth knowing:

- A stolen executable yields no database access.
- `tenant_id` and `site_id` are read from the agent's own row, never from
  client input, so a compromised agent cannot write into another tenant.
- Enrollment runs in one transaction, so a failure part-way cannot
  consume an enrollment code.
- Recorder credentials stay in `watchlog.ini` on the site PC. They are
  never transmitted; only event metadata goes upward.

`0005_viewer_api.sql` grants read access to the anonymous role for the
development viewer. **It is prototype-only and must not be applied to a
database holding production data** — the multi-tenant portal will read
through authenticated, tenant-scoped sessions instead.

Rotate the database password and API keys before going to production.

---

## Layout

```
supabase/
  migrations/          0001 schema · 0002 seed · 0003 fleet view
                       0004 agent API · 0005 viewer API · 0006 analytics
  apply_migrations.py  idempotent runner, tracks what has run
  mint_code.py         one-time enrollment codes
agent/
  watchlog_agent.py    orchestrator
  spool.py             local SQLite buffer
  drivers/             base · hikvision · dahua · onvif · mock
  build_exe.ps1        PyInstaller one-file build
sim/                   protocol-accurate device simulators
mock_nvr/              simple polling test device
viewer/                static operator page
tests/                 offline database stub
```

---

## Design notes

Things that look like details and are not:

- **Three clocks are stored separately** — the recorder's, the agent
  machine's, and the server's. They disagree, and collapsing them
  corrupts both ordering and deduplication.
- **The deduplication key is computed server-side**, from the device's
  own identifiers, so an outdated agent build cannot weaken it. It falls
  back to site + camera + timestamp + type when a device offers no stable
  event ID — which many do not.
- **Devices are asked for events ascending from a high-water mark.** A
  device that returns the *newest* N instead makes a naive client skip
  the middle of any backlog longer than one page.
- **Alarm bursts are collapsed** to one event per camera per type per 30
  seconds. Hikvision repeats an active alarm roughly once a second;
  without this, one person walking past a camera writes hundreds of rows.
- **Events buffer to local disk before upload.** A dropped uplink or a
  site power cut queues instead of losing data.
- **Reports bucket time in the site's own timezone**, not UTC. A report
  that files a 2am event on the wrong day is worse than no report.
