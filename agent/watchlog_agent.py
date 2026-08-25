#!/usr/bin/env python3
"""
WatchLog site agent.

Proves the thesis of the engagement: software installed on a machine we
will never touch, inside a network we have no access to, behind NAT and a
firewall we do not control, can enroll itself and report events to us
with no port forwarding, no VPN, and no inbound connection ever.

Every connection this process makes is OUTBOUND — to the NVR on the local
LAN, and to Supabase over HTTPS. It never binds a socket, never listens,
and needs no firewall rule.

Shape:

    driver thread  --> local SQLite spool --> uploader --> Supabase RPC
    (Hikvision /                             (drains,
     Dahua / ONVIF /                          at-least-once)
     mock)

The spool is why a dead internet link buffers instead of losing events.

Authentication carries only the PUBLISHABLE key, which is public by
design. Identity is the per-agent secret minted at enrollment and stored
server-side as a SHA-256 hash. A stolen build grants no database access.

Usage
    python watchlog_agent.py                 # enroll if needed, then run
    python watchlog_agent.py --probe         # identify the NVR, no cloud
    python watchlog_agent.py --once          # one drain + heartbeat, exit
    python watchlog_agent.py --enroll-only
    python watchlog_agent.py --status
    python watchlog_agent.py --reset

Config, in precedence order:
    1. environment variables  WATCHLOG_*
    2. watchlog.ini beside this script (or beside the .exe when frozen)

Requires: requests
"""

from __future__ import annotations

import argparse
import base64
import configparser
import json
import os
import platform
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import discover
from drivers import DRIVERS, DriverError, autodetect, build

AGENT_VERSION = "0.2.0-prototype"

HEARTBEAT_SECONDS = 60
UPLOAD_SECONDS = 15
UPLOAD_BATCH = 200
HTTP_TIMEOUT = 30
DRIVER_RETRY_SECONDS = 20
ONCE_COLLECT_SECONDS = 25

# Incident stills. One per camera at most every SNAPSHOT_MIN_INTERVAL
# seconds: a busy gate can fire every few seconds, and an image per event
# would flood both the site uplink and the storage budget for no extra
# information.
SNAPSHOT_MIN_INTERVAL = 60
SNAPSHOT_MAX_BYTES = 2_000_000
# Faults where the camera is, by definition, not producing a usable
# picture. Asking anyway just blocks the event loop on a timeout.
NO_SNAPSHOT_EVENTS = {"video_loss", "disk_error", "disk_full"}
# Uploads are capped by BYTES as well as count - 200 events carrying
# stills would be a ~40 MB request.
UPLOAD_MAX_BYTES = 4_000_000


# --- helpers -----------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"{iso(now_utc())} [agent] {msg}", flush=True)


def mask(secret: str | None) -> str:
    """Secrets Gate: never print a credential in full, not even to a log."""
    if not secret:
        return "<unset>"
    return f"{secret[:6]}...{secret[-4:]} ({len(secret)} chars)"


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_state_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog"
    return Path.home() / ".watchlog"


# --- config ------------------------------------------------------------

class Config:
    def __init__(self) -> None:
        ini = configparser.ConfigParser()
        ini_path = base_dir() / "watchlog.ini"
        section: dict[str, str] = {}
        if ini_path.exists():
            # utf-8-sig, not utf-8: Notepad and PowerShell's Set-Content
            # both write a BOM, and configparser treats a leading ﻿ as
            # content before the section header and refuses the whole file.
            # An installer editing this on site will hit that.
            try:
                ini.read(ini_path, encoding="utf-8-sig")
            except configparser.Error as e:
                raise SystemExit(
                    f"FATAL: {ini_path} could not be read.\n  {e}\n"
                    f"It must start with the line [watchlog] and contain "
                    f"key = value pairs. Compare it against "
                    f"watchlog.ini.example.") from e
            if not ini.has_section("watchlog"):
                raise SystemExit(
                    f"FATAL: {ini_path} has no [watchlog] section. "
                    f"Compare it against watchlog.ini.example.")
            section = dict(ini.items("watchlog"))
            log(f"config file: {ini_path}")
        else:
            log(f"config file: none at {ini_path}, using environment only")

        def get(key: str, default: str | None = None) -> str | None:
            return (os.environ.get("WATCHLOG_" + key.upper())
                    or section.get(key) or default)

        self.supabase_url = (get("supabase_url") or "").rstrip("/")
        self.publishable_key = get("supabase_publishable_key") or ""
        self.enrollment_code = get("enrollment_code") or ""

        self.nvr_url = (get("nvr_url") or "").rstrip("/")
        self.nvr_username = get("nvr_username") or ""
        self.nvr_password = get("nvr_password") or ""
        self.nvr_driver = (get("nvr_driver") or "auto").strip().lower()
        self.snapshots = (str(get("snapshots") or "true").strip().lower()
                          not in ("0", "false", "no", "off"))
        self.snapshot_min_interval = int(
            get("snapshot_min_interval") or SNAPSHOT_MIN_INTERVAL)

        state_dir = Path(get("state_dir") or default_state_dir())
        self.state_path = Path(get("state_file") or (state_dir / "agent_state.json"))
        self.spool_path = Path(get("spool_file")
                               or (self.state_path.parent / "spool.sqlite"))

        self.heartbeat_seconds = int(get("heartbeat_seconds") or HEARTBEAT_SECONDS)
        self.upload_seconds = int(get("upload_seconds") or UPLOAD_SECONDS)

    def require_cloud(self) -> None:
        missing = [n for n, v in (("supabase_url", self.supabase_url),
                                  ("supabase_publishable_key", self.publishable_key))
                   if not v]
        if missing:
            raise SystemExit(
                "FATAL: missing config: " + ", ".join(missing) + ".\n"
                "Set WATCHLOG_SUPABASE_URL / WATCHLOG_SUPABASE_PUBLISHABLE_KEY, "
                "or fill in " + str(base_dir() / "watchlog.ini") +
                " (copy watchlog.ini.example).")

    def require_nvr(self) -> None:
        # 37777/37778 are Dahua's binary SDK ports and 34567 is Xiongmai's.
        # None of them speak HTTP, so pointing the agent at one produces a
        # confusing timeout rather than an obvious "wrong port".
        for bad, why in ((":37777", "Dahua's binary SDK port"),
                         (":37778", "Dahua's binary SDK port"),
                         (":34567", "Xiongmai's binary port"),
                         (":554",   "the RTSP video port")):
            if self.nvr_url.endswith(bad):
                raise SystemExit(
                    f"FATAL: nvr_url points at port {bad[1:]}, which is "
                    f"{why} - not a web interface.\n"
                    f"WatchLog needs the recorder's HTTP port, usually 80. "
                    f"Find it on the recorder itself under "
                    f"Main Menu > Network > Port.")
        if not self.nvr_url:
            raise SystemExit(
                "FATAL: no nvr_url. Point it at the recorder on the local "
                "LAN, e.g. http://192.168.1.108")


# --- Supabase RPC ------------------------------------------------------

class Cloud:
    """
    The entire cloud surface: four SECURITY DEFINER functions.

    No table is ever addressed directly. The publishable key on its own
    grants nothing — RLS is on with no policies — so identity is the
    agent's own secret, passed to each call.
    """

    def __init__(self, url: str, key: str) -> None:
        self.rpc = url + "/rest/v1/rpc"
        self.s = requests.Session()
        self.s.headers.update({
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        })

    def call(self, fn: str, **params):
        r = self.s.post(f"{self.rpc}/{fn}", json=params, timeout=HTTP_TIMEOUT)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            raise RuntimeError(f"{fn}: HTTP {r.status_code} {str(msg)[:300]}")
        return r.json() if r.text.strip() else None


# --- local state -------------------------------------------------------

def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log(f"WARNING: state file at {path} unreadable ({e}); treating as unenrolled")
        return None


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)
    if os.name != "nt":
        os.chmod(path, 0o600)
    # Memory law: read it back from disk, do not trust the write.
    if json.loads(path.read_text(encoding="utf-8")).get("agent_id") != state.get("agent_id"):
        raise RuntimeError("state write verification failed at " + str(path))


# --- driver ------------------------------------------------------------

def open_driver(cfg: Config):
    cfg.require_nvr()
    if cfg.nvr_driver in ("auto", ""):
        return autodetect(cfg.nvr_url, cfg.nvr_username, cfg.nvr_password,
                          log=log)
    driver = build(cfg.nvr_driver, cfg.nvr_url, cfg.nvr_username,
                   cfg.nvr_password)
    return driver, driver.probe()


# --- enrollment --------------------------------------------------------

def enroll(cfg: Config, cloud: Cloud, device) -> dict:
    if not cfg.enrollment_code:
        raise SystemExit("FATAL: no enrollment code. Set WATCHLOG_ENROLLMENT_CODE "
                         "or put enrollment_code in watchlog.ini.")
    code = cfg.enrollment_code.strip()
    log(f"enrolling with code {code}")

    try:
        res = cloud.call(
            "wl_enroll",
            p_code=code,
            p_hostname=platform.node(),
            p_platform=f"{platform.system()} {platform.release()}",
            p_agent_version=AGENT_VERSION,
            p_device_vendor=device.vendor if device else None,
            p_device_model=device.model if device else None,
            p_device_driver=device.driver if device else None,
        )
    except RuntimeError as e:
        raise SystemExit(f"FATAL: {e}\nMint a fresh code and try again.") from e

    state = {
        "agent_id": res["agent_id"],
        "agent_key": res["agent_key"],
        "tenant_id": res["tenant_id"],
        "site_id": res["site_id"],
        "enrolled_at": iso(now_utc()),
        "agent_version": AGENT_VERSION,
    }
    save_state(cfg.state_path, state)
    log(f"enrolled: agent_id={state['agent_id']} site={state['site_id']}")
    log(f"agent key {mask(state['agent_key'])} written to {cfg.state_path}")
    return state


# --- workers -----------------------------------------------------------

def collector(cfg: Config, spool, stop: threading.Event) -> None:
    """Driver thread. Never dies: on error it backs off and re-opens."""
    while not stop.is_set():
        driver = None
        try:
            driver, info = open_driver(cfg)
            log(f"driver {driver.name}: {info.vendor} {info.model or ''} "
                f"fw={info.firmware or '?'}".rstrip())
            if not driver.verified_against_hardware:
                log(f"NOTE: driver '{driver.name}' has not been verified against "
                    f"real hardware. Treat its output as unproven.")

            last_shot: dict[str, float] = {}

            for ev in driver.stream_events(stop):
                if stop.is_set():
                    break

                # The image is best-effort and strictly secondary. A
                # camera that hangs, refuses auth or returns junk must
                # cost us the picture, never the incident record.
                if cfg.snapshots and ev.event_type not in NO_SNAPSHOT_EVENTS:
                    clock = time.monotonic()
                    if clock - last_shot.get(ev.channel, 0.0) >= cfg.snapshot_min_interval:
                        last_shot[ev.channel] = clock
                        try:
                            raw = driver.get_snapshot(ev.channel)
                        except Exception as e:              # noqa: BLE001
                            raw = None
                            log(f"snapshot ch{ev.channel} failed: "
                                f"{type(e).__name__}: {str(e)[:120]}")
                        if raw and len(raw) <= SNAPSHOT_MAX_BYTES:
                            ev = ev.with_snapshot(
                                base64.b64encode(raw).decode("ascii"))
                            log(f"snapshot ch{ev.channel} {len(raw) // 1024} KB")
                        elif raw:
                            log(f"snapshot ch{ev.channel} discarded: "
                                f"{len(raw) // 1024} KB exceeds cap")

                spool.add(ev.to_json(now_utc()))
                dropped = spool.trim()
                if dropped:
                    log(f"WARNING: spool over capacity, dropped {dropped} "
                        f"oldest events")
        except (DriverError, requests.RequestException, RuntimeError) as e:
            # Log every line. The first line alone is "no driver recognised
            # the device", which tells whoever is reading the log nothing
            # they can act on; the per-driver reasons are the diagnosis.
            for line in str(e).splitlines():
                if line.strip():
                    log(f"ERROR: driver: {line.strip()[:200]}")
            log("run  watchlog-agent.exe --probe  to find out what is at "
                "that address")
        except SystemExit as e:
            # open_driver() exits on missing config. In a thread that would
            # end the thread silently, leaving an agent that heartbeats
            # forever and collects nothing with no explanation in the log.
            log(f"ERROR: driver not configured: {str(e).splitlines()[0][:200]}")
        except Exception as e:                       # noqa: BLE001
            log(f"ERROR: driver crashed: {type(e).__name__}: {e}")
        finally:
            if driver:
                driver.close()
        if not stop.is_set():
            log(f"driver reconnecting in {DRIVER_RETRY_SECONDS}s")
            stop.wait(DRIVER_RETRY_SECONDS)


def upload_once(cloud: Cloud, state: dict, spool) -> int:
    ids, events = spool.take(UPLOAD_BATCH)
    if not ids:
        return 0

    # Trim the batch by payload size. Events carrying stills are ~200 KB
    # each, so a full count-based batch would be a multi-megabyte POST on
    # a site uplink. Always keep at least one, or a single oversized row
    # would wedge the queue forever.
    total, cut = 0, len(events)
    for i, ev in enumerate(events):
        total += len(ev.get("snapshot_b64") or "") + 512
        if total > UPLOAD_MAX_BYTES and i > 0:
            cut = i
            break
    ids, events = ids[:cut], events[:cut]

    res = cloud.call("wl_ingest_events", p_agent_id=state["agent_id"],
                     p_agent_key=state["agent_key"], p_events=events)
    # Only acknowledge after the server has committed.
    spool.ack(ids)
    shots = res.get("snapshots") or 0
    log(f"uploaded {res['received']}: {res['inserted']} new, "
        f"{res['skipped']} already stored"
        + (f", {shots} image(s)" if shots else "")
        + f"; {spool.count()} left in spool")
    return res["inserted"]


def heartbeat(cloud: Cloud, state: dict, device) -> None:
    cloud.call("wl_heartbeat", p_agent_id=state["agent_id"],
               p_agent_key=state["agent_key"], p_agent_version=AGENT_VERSION,
               p_device_vendor=device.vendor if device else None,
               p_device_model=device.model if device else None,
               p_device_driver=device.driver if device else None)
    log("heartbeat ok")


# --- commands ----------------------------------------------------------

def cmd_probe(cfg: Config) -> None:
    """Identify the recorder. Touches no cloud service — pure diagnosis."""
    log(f"probing {cfg.nvr_url}")
    try:
        driver, info = open_driver(cfg)
    except DriverError as e:
        # Whoever runs --probe is standing in front of the recorder with a
        # laptop. A Python traceback tells them nothing they can act on.
        print(f"\n  Could not identify a recorder at {cfg.nvr_url}\n")
        # autodetect() puts a header on line 1 and one line per driver
        # after it; a named driver raises a single line. Show the detail
        # either way — "HTTP 401" is the whole answer for a bad password.
        lines = str(e).splitlines()
        for line in (lines[1:] if lines[0].startswith("no driver recognised")
                     else lines):
            if line.strip():
                print("   ", line.strip()[:200])

        # Do not stop at "it did not work". Find out what IS there: a
        # closed port, a web interface moved to 8080, an unsupported
        # protocol and a wrong IP all look identical above, and they have
        # four different fixes.
        host = discover.host_of(cfg.nvr_url)
        discover.report(host, discover.scan(cfg.nvr_url, log=print), log=print)
        raise SystemExit(1) from None
    print()
    print(f"  driver     {driver.name}"
          + ("" if driver.verified_against_hardware
             else "   (NOT verified against hardware)"))
    print(f"  vendor     {info.vendor}")
    print(f"  model      {info.model or '-'}")
    print(f"  firmware   {info.firmware or '-'}")
    print(f"  serial     {info.serial or '-'}")
    print(f"  channels   {info.channel_count if info.channel_count is not None else '-'}")
    try:
        chans = driver.list_channels()
        print(f"\n  {len(chans)} channel(s):")
        for c in chans:
            print(f"    {c.channel:>4}  {c.name or '-'}")
    except DriverError as e:
        print(f"  channel list failed: {e}")

    print("\n  listening 20s for live events...")
    stop = threading.Event()
    threading.Timer(20, stop.set).start()
    seen = 0
    try:
        for ev in driver.stream_events(stop):
            seen += 1
            print(f"    {iso(ev.device_ts)}  ch{ev.channel:<4} {ev.event_type}")
            if seen >= 20:
                break
    except DriverError as e:
        print(f"  event stream failed: {e}")
    finally:
        stop.set()
        driver.close()
    if seen == 0:
        print("    (nothing fired — normal on a quiet site; walk past a camera)")
    print()


def cmd_run(cfg: Config, state: dict, cloud: Cloud, once: bool,
            device=None) -> None:
    from spool import Spool

    spool = Spool(cfg.spool_path)
    log(f"spool: {cfg.spool_path} ({spool.count()} queued)")

    if once:
        # Collect for a short window first, otherwise --once on a fresh
        # install drains an empty spool and looks like nothing works.
        stop = threading.Event()
        worker = threading.Thread(target=collector, args=(cfg, spool, stop),
                                  daemon=True, name="collector")
        worker.start()
        log(f"collecting for {ONCE_COLLECT_SECONDS}s...")
        stop.wait(ONCE_COLLECT_SECONDS)
        stop.set()
        worker.join(timeout=5)
        try:
            upload_once(cloud, state, spool)
        except RuntimeError as e:
            log(f"ERROR: upload failed: {e}")
        heartbeat(cloud, state, device)
        spool.close()
        return

    stop = threading.Event()
    worker = threading.Thread(target=collector, args=(cfg, spool, stop),
                              daemon=True, name="collector")
    worker.start()

    log(f"running: upload every {cfg.upload_seconds}s, heartbeat every "
        f"{cfg.heartbeat_seconds}s, outbound only. Ctrl-C to stop.")

    next_up = next_beat = 0.0
    try:
        while True:
            clock = time.monotonic()
            if clock >= next_up:
                next_up = clock + cfg.upload_seconds
                try:
                    upload_once(cloud, state, spool)
                except (RuntimeError, requests.RequestException) as e:
                    log(f"ERROR: upload failed, will retry: "
                        f"{str(e).splitlines()[0][:200]}")
            if clock >= next_beat:
                next_beat = clock + cfg.heartbeat_seconds
                try:
                    heartbeat(cloud, state, device)
                except (RuntimeError, requests.RequestException) as e:
                    log(f"ERROR: heartbeat failed, will retry: "
                        f"{str(e).splitlines()[0][:200]}")
            time.sleep(1)
    except KeyboardInterrupt:
        log("stopping...")
        stop.set()
        worker.join(timeout=5)
        spool.close()
        log("stopped")


# --- main --------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="WatchLog site agent")
    ap.add_argument("--probe", action="store_true",
                    help="identify the NVR and watch for events; no cloud calls")
    ap.add_argument("--once", action="store_true",
                    help="one spool drain + heartbeat, then exit")
    ap.add_argument("--enroll-only", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="delete local identity and spool, then exit")
    ap.add_argument("--list-drivers", action="store_true")
    ap.add_argument("--scan", metavar="IP",
                    help="scan an address for a recorder and report what "
                         "answers; needs no config at all")
    args = ap.parse_args()

    if args.scan:
        host = discover.host_of(args.scan)
        discover.report(host, discover.scan(args.scan, log=print), log=print)
        return

    if args.list_drivers:
        for name, cls in DRIVERS.items():
            mark = "verified" if cls.verified_against_hardware else "UNVERIFIED"
            print(f"  {name:18} {mark}")
        return

    cfg = Config()
    log(f"watchlog-agent {AGENT_VERSION} on {platform.node()} "
        f"({platform.system()} {platform.release()})")
    log(f"state file: {cfg.state_path}")

    if args.reset:
        for p in (cfg.state_path, cfg.spool_path):
            if p.exists():
                p.unlink()
                log(f"deleted {p}")
        log("next run will re-enroll")
        return

    state = load_state(cfg.state_path)

    if args.status:
        if not state:
            log("not enrolled")
        else:
            log(f"enrolled  agent_id={state['agent_id']}")
            log(f"          site_id={state['site_id']} tenant_id={state['tenant_id']}")
            log(f"          key={mask(state.get('agent_key'))}")
            log(f"          enrolled_at={state.get('enrolled_at')}")
        if cfg.spool_path.exists():
            from spool import Spool
            sp = Spool(cfg.spool_path)
            log(f"spool     {sp.count()} events queued at {cfg.spool_path}")
            sp.close()
        return

    if args.probe:
        cmd_probe(cfg)
        return

    cfg.require_cloud()
    log(f"supabase: {cfg.supabase_url}  publishable key "
        f"{mask(cfg.publishable_key)}")
    cloud = Cloud(cfg.supabase_url, cfg.publishable_key)

    # Identify the recorder ONCE and reuse the answer: enrollment, the
    # camera sync and the heartbeat all want it, and probing four times
    # on every start is noise on the wire and in the log.
    device, channels = None, []
    try:
        driver, device = open_driver(cfg)
        try:
            channels = [{"channel": c.channel, "name": c.name}
                        for c in driver.list_channels()]
        finally:
            driver.close()
    except (DriverError, SystemExit) as e:
        for line in str(e).splitlines():
            if line.strip():
                log(f"WARNING: NVR not identified: {line.strip()[:200]}")
        # Run the scan automatically, once, at startup. Telling someone to
        # "go and run --probe" assumes they will read the log, be at that
        # machine, and try again. They usually just run it the same way
        # again, and we learn nothing. Fifteen seconds spent here answers
        # the question the first time.
        if cfg.nvr_url:
            try:
                host = discover.host_of(cfg.nvr_url)
                discover.report(host,
                                discover.scan(cfg.nvr_url, log=log),
                                log=log)
            except Exception as se:                    # noqa: BLE001
                log(f"scan failed: {type(se).__name__}: {se}")

    if state:
        log(f"already enrolled as {state['agent_id']} - skipping enrollment")
    else:
        state = enroll(cfg, cloud, device)

    if args.enroll_only:
        return

    if channels:
        try:
            mapping = cloud.call("wl_sync_cameras", p_agent_id=state["agent_id"],
                                 p_agent_key=state["agent_key"],
                                 p_cameras=channels)
            log(f"cameras synced: {len(mapping)} channels")
        except RuntimeError as e:
            log(f"WARNING: camera sync failed: {str(e).splitlines()[0][:160]}")

    cmd_run(cfg, state, cloud, once=args.once, device=device)


if __name__ == "__main__":
    main()
