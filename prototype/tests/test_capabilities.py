#!/usr/bin/env python3
"""Capability-probe and recorder-native evidence tests."""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "sim"))

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class DahuaSim:
    """Run the real Dahua simulator on a background thread."""

    def __enter__(self):
        import dahua_sim
        self.port = _free_port()
        srv = dahua_sim.ThreadingHTTPServer(
            ("127.0.0.1", self.port), dahua_sim.make_handler()
            if hasattr(dahua_sim, "make_handler") else dahua_sim.Handler)
        self.srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        return self

    def __exit__(self, *a):
        self.srv.shutdown()


@case("Dahua probe reports the sim's configured analytics correctly")
def t_dahua():
    from drivers.dahua import DahuaDriver
    with DahuaSim() as sim:
        d = DahuaDriver(f"http://127.0.0.1:{sim.port}", "admin", "admin")
        caps = d.capabilities()
        d.close()

    chans = {c["channel"]: c for c in caps["channels"]}
    assert len(chans) == 4, f"expected 4 channels, got {len(chans)}"

    def a(ch, key):
        return next(x for x in chans[ch]["analytics"] if x["key"] == key)

    assert all(a(ch, "motion")["active"] for ch in chans), "motion not active on all"
    assert a("1", "human_vehicle")["supported"]
    assert not a("1", "human_vehicle")["active"]
    assert a("1", "line_crossing")["active"], "gate line not detected active"
    assert not a("2", "line_crossing")["active"], "ch2 line should be off"
    assert a("3", "intrusion")["active"], "yard zone not detected active"
    assert a("4", "tamper")["active"] and not a("1", "tamper")["active"]
    assert a("1", "line_crossing")["geometry"] is True
    assert a("1", "motion")["geometry"] is False
    return "line on ch1, zone on ch3, tamper on ch4, motion everywhere"


@case("every analytic has the required, correctly-typed fields")
def t_shape():
    from drivers.mock import MockDriver
    caps = MockDriver("http://mock").capabilities()
    assert caps["channels"], "mock returned no channels"
    for ch in caps["channels"]:
        assert isinstance(ch["channel"], str)
        for x in ch["analytics"]:
            for f in ("key", "label", "supported", "active", "geometry"):
                assert f in x, f"missing '{f}' in {x}"
            assert isinstance(x["supported"], bool)
            assert isinstance(x["active"], bool)
            assert isinstance(x["geometry"], bool)
            assert not (x["active"] and not x["supported"]), \
                f"{x['key']} active but unsupported"
    return "shape valid; no active-without-supported"


@case("the probe fails safe when the device errors")
def t_fail_safe():
    from drivers.dahua import DahuaDriver
    d = DahuaDriver("http://127.0.0.1:1", "x", "x", timeout=1)
    try:
        caps = d.capabilities()
    except Exception as e:                         # noqa: BLE001
        raise AssertionError(f"probe crashed instead of failing safe: {e}")
    finally:
        d.close()
    assert caps == {"channels": []} or caps["channels"] == [], caps
    return "returned empty, did not crash"


@case("Dahua SMD/IVS events carry recorder-native AI provenance")
def t_native_dahua_events():
    from drivers.native_recorder import NativeDahuaDriver
    d = NativeDahuaDriver("http://127.0.0.1", "admin", "x", timeout=1)
    smart = d._parse_line("Code=SmartMotionHuman;action=Start;index=0;data={}")
    assert smart and smart.event_type == "person"
    assert smart.payload.get("native_ai") is True
    assert smart.payload.get("source") == "recorder_native_ai"
    assert smart.payload.get("native_code") == "SmartMotionHuman"
    generic = d._parse_line("Code=VideoMotion;action=Start;index=1;data={}")
    assert generic and generic.event_type == "motion"
    assert not generic.payload.get("native_ai")
    assert generic.payload.get("source") == "recorder_event"
    d.close()
    return "SMD trusted as native AI; generic motion remains locally filterable"


@case("Hikvision smart events carry recorder-native AI provenance")
def t_native_hik_events():
    from drivers.native_recorder import NativeHikvisionDriver
    d = NativeHikvisionDriver("http://127.0.0.1", "admin", "x", timeout=1)
    raw = b"""<?xml version='1.0'?>
    <EventNotificationAlert>
      <eventType>lineDetection</eventType><eventState>active</eventState>
      <channelID>1</channelID><dateTime>2026-09-05T10:00:00Z</dateTime>
      <activePostCount>1</activePostCount>
    </EventNotificationAlert>"""
    event = d._parse_alert(raw)
    assert event and event.event_type == "line_crossing"
    assert event.payload.get("native_ai") is True
    assert event.payload.get("source") == "recorder_native_ai"
    d.close()
    return "smart line event is preserved as recorder-native AI"


@case("packaged collector does not re-gate recorder-native AI through local vision")
def t_native_collector_policy():
    src = (ROOT / "agent" / "native_event_collector.py").read_text(encoding="utf-8")
    assert 'native_ai = bool((ev.payload or {}).get("native_ai"))' in src
    native_block = src.split("if native_ai:", 1)[1].split("elif detector", 1)[0]
    # A native smart event is NEVER re-gated: it is never dropped in this branch (no
    # false-alarm `continue`). Item-8 secondary verification may attach a state to a
    # person/vehicle classification, but it is is_verifiable-gated (geometry/temporal
    # events are never second-guessed from one still) and never gates the event.
    assert "continue" not in native_block
    assert "native_verification" in native_block and "is_verifiable" in native_block
    assert "classify_event(raw)" in src
    return "native smart events are never re-gated; verification only annotates person/vehicle"


@case("incident footage transport is on-demand, bounded and fail-closed")
def t_incident_footage_contract():
    driver_src = (ROOT / "agent" / "drivers" / "native_recorder.py").read_text(encoding="utf-8")
    sql = (ROOT / "supabase" / "migrations" /
           "0040_incident_footage_requests.sql").read_text(encoding="utf-8")
    lower = sql.lower()
    assert "/cgi-bin/loadfile.cgi" in driver_src
    assert "CLIP_MAX_BYTES = 32 * 1024 * 1024" in driver_src
    assert "getCurrentTime" in driver_src
    for table in ("incident_clip_requests", "incident_clip_chunks"):
        assert f"alter table public.{table} enable row level security" in lower
        assert f"revoke all on table public.{table}" in lower
    assert "wl_require_role(array['owner','admin'])" in sql
    assert "60 seconds" in lower and "24 hours" in lower and "32 mb pilot limit" in lower
    assert "recorder_password" not in lower and "rtsp_url" not in lower
    return "Owner/Admin request, 60s/32MB/24h bounds, no recorder secrets in cloud schema"


@case("release entrypoint activates native AI and footage workers only for runtime")
def t_release_policy():
    src = (ROOT / "agent" / "release_agent.py").read_text(encoding="utf-8")
    assert "app.core.collector = native_event_collector.collector" in src
    assert "incident_evidence.wrap_cmd_run" in src
    assert 'if explicit_setup:' in src
    return "packaged runtime enabled; explicit installer setup still exits deterministically"


def run():
    print("Capability probe")
    print("=" * 62)
    p = f = 0
    for name, fn in CASES:
        try:
            print(f"  PASS  {name}\n          {fn()}"); p += 1
        except AssertionError as e:
            print(f"  FAIL  {name}\n          {e}"); f += 1
        except Exception as e:                     # noqa: BLE001
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}"); f += 1
    print("=" * 62)
    print(f"  {p} passed, {f} failed")
    return 1 if f else 0


def test_capabilities():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
