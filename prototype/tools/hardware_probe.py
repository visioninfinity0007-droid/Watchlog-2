#!/usr/bin/env python3
"""WatchLog standalone recorder hardware-validation harness.

Runs a bounded, READ-ONLY probe of a single recorder and emits a capability evidence report.
It installs NOTHING, changes NO recorder configuration, and does NOT scan the network — it only
talks to the ONE recorder you point it at (plus, if you pass --onvif, a single bounded WS-Discovery
multicast). It reuses the SAME drivers the agent uses, so what it proves here is what the agent can
do here.

Every capability is classified into exactly one honest state:

  field-proven            exercised successfully against THIS recorder in THIS run
  implemented-unverified  code exists but was not (or could not be) exercised here
  unsupported             the driver does not implement it (the honest "we do not do this")
  unknown                 attempted but the recorder's answer was inconclusive / errored

`field-proven` is only ever written when the operation actually returned real data here.

SAFETY: never run this against a client recorder without explicit approval. The tool refuses to
run until you pass --i-have-authorization (or set WATCHLOG_PROBE_AUTHORIZED=1).

Usage:
  python hardware_probe.py --url http://10.0.0.10 --username admin --password '***' \
      --channel 1 --onvif --json report.json --i-have-authorization

Credentials are read from --username/--password or WATCHLOG_NVR_USERNAME/PASSWORD; they are used
only to talk to the recorder and are never written to the report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "agent"))

import recorder_probe                                  # noqa: E402
from drivers import DriverError, autodetect, build     # noqa: E402

FIELD, IMPL, UNSUP, UNKNOWN = "field-proven", "implemented-unverified", "unsupported", "unknown"


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _implements(driver, method):
    """The driver overrides the base no-op for `method` (else it is honestly unsupported)."""
    from drivers.base import NvrDriver
    return getattr(type(driver), method, None) is not getattr(NvrDriver, method, None)


def _connect(url, username, password, driver_name, log):
    """Open the recorder read-only, trying a BOUNDED set of known ports on the SAME host if the
    configured URL fails to connect (never on an auth failure — that risks a lockout)."""
    host, port, scheme = _split(url)

    def attempt(base):
        if driver_name in ("auto", "", None):
            return autodetect(base, username, password, log=log)
        drv = build(driver_name, base, username, password)
        return drv, drv.probe()

    try:
        return attempt(url), url
    except DriverError as primary:
        if _looks_auth(primary):
            raise
        sdk = recorder_probe.classify_port(port) if port else None
        if sdk:
            log(f"note: configured port {port} is the {sdk} binary control port, not HTTP")
        vendor = "auto" if driver_name in ("auto", "", None) else driver_name
        for cand in recorder_probe.candidates(vendor, host, port)[:4]:
            if cand.get("non_http") or cand["base_url"].rstrip("/") == str(url).rstrip("/"):
                continue
            try:
                log(f"trying {cand['base_url']}")
                return attempt(cand["base_url"]), cand["base_url"]
            except DriverError as alt:
                if _looks_auth(alt):
                    raise
        raise primary


def _split(url):
    from urllib.parse import urlparse
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw
    p = urlparse(raw)
    return p.hostname or "", p.port, ("https" if p.scheme == "https" else "http")


def _looks_auth(err):
    s = str(err).lower()
    return ("http 401" in s or "http 403" in s or "unauthor" in s
            or "rejected the username or password" in s or "invalid username or password" in s)


def _cap(driver, method, run):
    """Run one read-only capability probe. `run(driver)` returns (ok, detail, evidence)."""
    if not _implements(driver, method):
        return {"status": UNSUP, "detail": "driver does not implement this", "evidence": None}
    try:
        ok, detail, evidence = run(driver)
    except NotImplementedError as e:
        return {"status": UNSUP, "detail": str(e)[:160] or "not implemented", "evidence": None}
    except Exception as e:                              # noqa: BLE001
        return {"status": UNKNOWN, "detail": f"{type(e).__name__}: {str(e).splitlines()[0][:160]}", "evidence": None}
    return {"status": FIELD if ok else IMPL, "detail": detail, "evidence": evidence}


def probe_recorder(url, username="", password="", driver_name="auto", channel="1",
                   do_onvif=False, log=lambda _m: None) -> dict:
    """Bounded read-only probe of ONE recorder. Returns the capability evidence report dict."""
    report = {"probed_at": _iso(_now()), "target_host": _split(url)[0], "driver_requested": driver_name,
              "connection": {"status": UNKNOWN}, "capabilities": {}, "transport": {}, "onvif_discovery": None}

    try:
        (driver, info), base = _connect(url, username, password, driver_name, log)
    except DriverError as e:
        report["connection"] = {"status": UNKNOWN if not _looks_auth(e) else "auth-required",
                                "detail": str(e).splitlines()[0][:200]}
        return report

    report["connection"] = {"status": FIELD, "driver": driver.name, "base_url_scheme": _split(base)[1],
                            "vendor": info.vendor, "model": info.model, "firmware": info.firmware,
                            "verified_against_hardware_flag": bool(getattr(driver, "verified_against_hardware", False))}
    report["transport"] = {"scheme": _split(base)[1],
                           "port": _split(base)[1] == "https" and "https-port" or "http-port",
                           "https": FIELD if _split(base)[1] == "https" else IMPL,
                           "port_classification": IMPL}

    try:
        # ---- read-only capability probes -------------------------------------------------
        def _inventory(d):
            chans = list(d.list_channels() or [])
            return (len(chans) > 0, f"enumerated {len(chans)} channel(s)", {"channels": len(chans)})

        def _snapshot(d):
            raw = d.get_snapshot(str(channel))
            return (bool(raw), f"{len(raw)} bytes" if raw else "no image returned",
                    {"bytes": len(raw) if raw else 0})

        def _recording(d):
            st = d.recording_status()
            return (st is not None, "read recording state" if st is not None else "no state", None)

        def _storage(d):
            st = d.storage_status()
            return (st is not None, "read storage state" if st is not None else "no state", None)

        def _clip(d):
            end = _now() - timedelta(seconds=30)
            data = d.get_clip(str(channel), end - timedelta(seconds=5), end)
            return (bool(data), f"{len(data)} bytes" if data else "recorder returned no clip",
                    {"bytes": len(data) if data else 0})

        report["capabilities"]["inventory"] = _cap(driver, "list_channels", _inventory)
        report["capabilities"]["snapshot"] = _cap(driver, "get_snapshot", _snapshot)
        report["capabilities"]["recording_verification"] = _cap(driver, "recording_status", _recording)
        report["capabilities"]["storage_health"] = _cap(driver, "storage_status", _storage)
        report["capabilities"]["footage_retrieval"] = _cap(driver, "get_clip", _clip)

        # native events / AI: implemented if the driver streams, but confirming LIVE flow needs
        # real activity during the probe, so we never over-claim it here.
        if _implements(driver, "stream_events"):
            report["capabilities"]["native_events"] = {
                "status": IMPL, "detail": "driver streams recorder events; live flow needs activity to confirm",
                "evidence": None}
        else:
            report["capabilities"]["native_events"] = {"status": UNSUP, "detail": "driver does not stream events", "evidence": None}

        if _implements(driver, "capabilities"):
            try:
                caps = driver.capabilities()
                report["capabilities"]["smart_analytics"] = {
                    "status": FIELD if caps else IMPL,
                    "detail": "read recorder-side analytics capabilities" if caps else "no analytics reported",
                    "evidence": caps if isinstance(caps, (dict, list)) else None}
            except Exception as e:                      # noqa: BLE001
                report["capabilities"]["smart_analytics"] = {"status": UNKNOWN, "detail": str(e)[:160], "evidence": None}
        else:
            report["capabilities"]["smart_analytics"] = {"status": UNSUP, "detail": "not implemented", "evidence": None}
    finally:
        try:
            driver.close()
        except Exception:                               # noqa: BLE001
            pass

    if do_onvif:
        report["onvif_discovery"] = _onvif_probe(log)
    return report


def _onvif_probe(log):
    """A SINGLE bounded WS-Discovery multicast (not a host scan)."""
    try:
        import wsdiscovery
    except Exception as e:                              # noqa: BLE001
        return {"status": UNSUP, "detail": f"wsdiscovery unavailable: {type(e).__name__}"}
    try:
        found = wsdiscovery.discover(timeout=4)         # bounded single multicast
        n = len(found or [])
        return {"status": FIELD if n else UNKNOWN, "detail": f"{n} ONVIF device(s) answered", "count": n}
    except Exception as e:                              # noqa: BLE001
        return {"status": UNKNOWN, "detail": f"{type(e).__name__}: {str(e)[:160]}"}


def render(report: dict) -> str:
    lines = [f"WatchLog recorder capability report — {report['probed_at']}",
             f"target host: {report.get('target_host') or '?'}"]
    conn = report.get("connection", {})
    lines.append(f"connection: {conn.get('status')} "
                 + (f"({conn.get('driver')} · {conn.get('vendor')} {conn.get('model') or ''})" if conn.get("driver") else f"({conn.get('detail','')})"))
    lines.append("")
    lines.append("capability                | status")
    lines.append("--------------------------+---------------------")
    for key, cap in (report.get("capabilities") or {}).items():
        lines.append(f"{key:25} | {cap['status']:20} {('- ' + cap['detail']) if cap.get('detail') else ''}")
    if report.get("onvif_discovery"):
        o = report["onvif_discovery"]
        lines.append("")
        lines.append(f"onvif discovery: {o.get('status')} - {o.get('detail','')}")
    lines.append("")
    lines.append("legend: field-proven = worked here | implemented-unverified = code exists, not proven here")
    lines.append("        unsupported = not implemented | unknown = attempted, inconclusive")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WatchLog standalone recorder hardware probe (read-only)")
    ap.add_argument("--url", required=True, help="recorder base URL, e.g. http://10.0.0.10")
    ap.add_argument("--username", default=os.environ.get("WATCHLOG_NVR_USERNAME", ""))
    ap.add_argument("--password", default=os.environ.get("WATCHLOG_NVR_PASSWORD", ""))
    ap.add_argument("--driver", default="auto", help="auto | dahua-cgi | hikvision-isapi | onvif | mock")
    ap.add_argument("--channel", default="1")
    ap.add_argument("--onvif", action="store_true", help="also run ONE bounded ONVIF WS-Discovery multicast")
    ap.add_argument("--json", default=None, help="write the full JSON report to this path")
    ap.add_argument("--i-have-authorization", action="store_true",
                    help="confirm you are authorized to probe this recorder")
    args = ap.parse_args(argv)

    if not (args.i_have_authorization or os.environ.get("WATCHLOG_PROBE_AUTHORIZED") == "1"):
        print("REFUSING: pass --i-have-authorization (or WATCHLOG_PROBE_AUTHORIZED=1). "
              "Never probe a client recorder without explicit approval.", file=sys.stderr)
        return 2

    report = probe_recorder(args.url, args.username, args.password, args.driver, args.channel,
                            do_onvif=args.onvif, log=lambda m: print(m, file=sys.stderr))
    print(render(report))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nfull JSON written to {args.json}", file=sys.stderr)
    return 0 if report["connection"]["status"] == FIELD else 1


if __name__ == "__main__":
    raise SystemExit(main())
