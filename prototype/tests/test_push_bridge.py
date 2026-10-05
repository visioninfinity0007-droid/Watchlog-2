#!/usr/bin/env python3
"""
Push-bridge parser tests.

The bridge turns a recorder's native alarm POST into a WatchLog event. If
it mis-parses, we either drop real events or invent fake ones — both bad —
so the parser is pinned here against the exact shapes Hikvision sends: a
bare XML alert, and the multipart form with a JPEG attached. Namespaced
XML (Hikvision uses one) and keep-alive/inactive alerts are covered too.

    python prototype/tests/test_push_bridge.py
    pytest -q prototype/tests/test_push_bridge.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import push_bridge as pb          # noqa: E402


ALERT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <ipAddress>192.168.1.108</ipAddress>
  <channelID>3</channelID>
  <dateTime>2026-08-29T21:14:07+05:00</dateTime>
  <activePostCount>1</activePostCount>
  <eventType>linedetection</eventType>
  <eventState>active</eventState>
  <eventDescription>Line Crossing detected</eventDescription>
</EventNotificationAlert>"""

KEEPALIVE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <channelID>1</channelID>
  <dateTime>2026-08-29T21:15:00+05:00</dateTime>
  <eventType>videoloss</eventType>
  <eventState>inactive</eventState>
</EventNotificationAlert>"""

HEARTBEAT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">
  <channelID>1</channelID>
  <dateTime>2026-09-25T12:00:00Z</dateTime>
  <activePostCount>0</activePostCount>
  <eventType>heartBeat</eventType>
  <eventState>active</eventState>
  <eventDescription>heartBeat</eventDescription>
</EventNotificationAlert>"""

NOT_AN_ALERT = b"<?xml version='1.0'?><Something><x>1</x></Something>"

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("bare XML line-crossing alert parses to a mapped event")
def t_line():
    ev = pb.parse_hikvision(ALERT_XML, "application/xml")
    assert ev is not None, "returned None on a valid alert"
    assert ev["event_type"] == "line_crossing", ev["event_type"]
    assert ev["channel"] == "3", ev["channel"]
    assert ev["device_ts"].startswith("2026-08-29T16:14:07"), ev["device_ts"]
    assert "snapshot_b64" not in ev
    return f"{ev['event_type']} ch{ev['channel']} @ {ev['device_ts'][:19]}Z"


@case("multipart with a JPEG attaches the snapshot")
def t_multipart():
    jpeg = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"fake-jpeg-body" * 8
    boundary = "MIME_boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/xml\r\n\r\n".encode()
        + ALERT_XML + b"\r\n"
        + f"--{boundary}\r\nContent-Type: image/jpeg\r\n\r\n".encode()
        + jpeg + b"\r\n"
        + f"--{boundary}--\r\n".encode()
    )
    ev = pb.parse_hikvision(body, f"multipart/form-data; boundary={boundary}")
    assert ev is not None, "returned None on a valid multipart alert"
    assert ev["event_type"] == "line_crossing"
    assert "snapshot_b64" in ev, "JPEG part was not attached"
    assert base64.b64decode(ev["snapshot_b64"])[:2] == bytes([0xFF, 0xD8]), \
        "snapshot is not a JPEG"
    return "event + snapshot recovered from multipart"


@case("an inactive keep-alive is dropped, not recorded")
def t_keepalive():
    ev = pb.parse_hikvision(KEEPALIVE_XML, "application/xml")
    assert ev is None, "a keep-alive was turned into an event"
    return "dropped"


@case("an active Hikvision heartBeat is liveness only, not an incident")
def t_hik_heartbeat():
    assert pb.parse_hikvision(HEARTBEAT_XML, "application/xml") is None
    return "heartBeat reserved for recorder liveness"


@case("handler authenticates liveness before vendor parsing")
def t_handler_liveness_first():
    src = Path(pb.__file__).read_text(encoding="utf-8")
    block = src.split("def do_POST(self):", 1)[1]
    assert block.index("liveness(token)") < block.index("parse_any(body, ctype)")
    return "liveness precedes parser"


@case("a non-alert POST is refused")
def t_not_alert():
    assert pb.parse_hikvision(NOT_AN_ALERT, "application/xml") is None
    assert pb.parse_hikvision(b"garbage", "application/xml") is None
    return "None on non-alert and on garbage"


@case("an unknown eventType is kept under its own name, not dropped")
def t_unknown_type():
    xml = ALERT_XML.replace(b"linedetection", b"someNewThing")
    ev = pb.parse_hikvision(xml, "application/xml")
    assert ev is not None and ev["event_type"] == "somenewthing", ev
    return "kept as 'somenewthing'"


# --- MNVR-026: one alarm, one meaning, whichever path it took ------------------
#
# The Agent (alertStream / attach) and the bridge (recorder push) must give the same
# alarm the same channel, event type, device_event_id and device time, because
# wl_ingest_push and wl_ingest_events key it with the same
# wl_recorder_event_dedupe_key. Before the shared parser they disagreed: the bridge
# invented device_event_id '<ch>-<type>-<ts>', mapped regionEntrance to 'intrusion'
# (Agent: region_entry), defaulted a channel-less alert to camera 1 or its name, kept
# heartbeat videoloss (activePostCount 0) and never collapsed a burst.

ROOT = Path(__file__).resolve().parents[1]


def _hik(etype, fields="<channelID>2</channelID>",
         when="2026-10-04T21:00:00+05:00", post="1"):
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">'
            f'{fields}<dateTime>{when}</dateTime>'
            f'<activePostCount>{post}</activePostCount>'
            f'<eventType>{etype}</eventType><eventState>active</eventState>'
            f'</EventNotificationAlert>').encode()


def _agent_hik_event(raw):
    sys.path.insert(0, str(ROOT / "agent"))
    from drivers.native_recorder import NativeHikvisionDriver
    d = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    try:
        return d._parse_alert(raw)
    finally:
        d.close()


def _agent_dahua_event(line):
    sys.path.insert(0, str(ROOT / "agent"))
    from drivers.native_recorder import NativeDahuaDriver
    d = NativeDahuaDriver("http://127.0.0.1", "u", "p", timeout=1)
    try:
        return d._parse_line(line)
    finally:
        d.close()


def _identity_of_agent(ev):
    from datetime import timezone
    return (ev.channel, ev.event_type, ev.device_event_id,
            ev.device_ts.astimezone(timezone.utc).replace(microsecond=0).isoformat())


def _identity_of_bridge(ev):
    from datetime import datetime, timezone
    ts = datetime.fromisoformat(ev["device_ts"]).astimezone(timezone.utc)
    return (ev["channel"], ev["event_type"], ev.get("device_event_id"),
            ts.replace(microsecond=0).isoformat())


HIK_PARITY = [
    _hik("linedetection"), _hik("VMD"), _hik("regionEntrance"), _hik("regionExiting"),
    _hik("facedetection"), _hik("fielddetection"), _hik("videoloss", post="3"),
    _hik("diskfull", fields=""), _hik("diskerror", fields="<channelID>1</channelID>"),
    _hik("VMD", fields="<channelName>Main Gate</channelName>"),
    _hik("VMD", fields="<dynChannelID>5</dynChannelID>"),
    _hik("someNewThing"),
]


@case("MNVR-026: the bridge and the Agent give every Hikvision alarm the same identity")
def t_hik_parity():
    for raw in HIK_PARITY:
        agent = _agent_hik_event(raw)
        bridge = pb.parse_hikvision(raw, "application/xml")
        assert agent is not None and bridge is not None, raw
        assert _identity_of_agent(agent) == _identity_of_bridge(bridge), \
            (raw[-140:], _identity_of_agent(agent), _identity_of_bridge(bridge))
    return f"{len(HIK_PARITY)} alarm shapes agree on channel, type, id and time"


@case("MNVR-026: the bridge never invents a device_event_id the Agent does not send")
def t_no_invented_id():
    ev = pb.parse_hikvision(ALERT_XML, "application/xml")
    assert ev.get("device_event_id") is None, ev.get("device_event_id")
    dh = pb.parse_dahua(b"Code=VideoMotion;action=Start;index=0", "text/plain")
    assert dh.get("device_event_id") is None, dh.get("device_event_id")
    return "device_event_id is NULL on both vendors, as the Agent sends it"


@case("MNVR-026: a Dahua alarm has the same channel, type and id on both paths")
def t_dahua_parity():
    lines = ["Code=VideoMotion;action=Start;index=0", "Code=CrossLineDetection;action=Start;index=3",
             "Code=StorageLowSpace;action=Start;index=0", "Code=AlarmLocal;action=Start;index=2",
             "Code=SmartMotionHuman;action=Pulse;index=1", "Code=VideoMotion;action=Start",
             "Code=SomeNewThing;action=Start;index=4"]
    for line in lines:
        agent = _agent_dahua_event(line)
        bridge = pb.parse_dahua(line.encode(), "text/plain")
        assert agent is not None and bridge is not None, line
        assert (agent.channel, agent.event_type, agent.device_event_id) == \
            (bridge["channel"], bridge["event_type"], bridge.get("device_event_id")), line
    return f"{len(lines)} Dahua codes agree"


@case("MNVR-026: heartbeat videoloss (activePostCount 0) is not an event")
def t_heartbeat_videoloss():
    for post in ("0", ""):
        raw = _hik("videoloss", post=post)
        assert pb.parse_hikvision(raw, "application/xml") is None, post
    assert pb.parse_hikvision(_hik("videoloss", post="2"), "application/xml") is not None
    return "keep-alive videoloss dropped, a real loss kept"


@case("MNVR-026: no channel guessing; a channel-less alarm is not camera 1 or its name")
def t_no_channel_guess():
    named = pb.parse_hikvision(_hik("VMD", fields="<channelName>Main Gate</channelName>"),
                               "application/xml")
    assert named["channel"] is None, named["channel"]
    assert named["payload"]["channelName"] == "Main Gate"
    disk = pb.parse_hikvision(_hik("diskfull", fields=""), "application/xml")
    assert disk["channel"] is None and disk["payload"]["recorder_scoped"] is True
    dh = pb.parse_dahua(b"Code=VideoMotion;action=Start", "text/plain")
    assert dh["channel"] is None, dh["channel"]
    return "unknown stays unknown"


@case("MNVR-026: a naive recorder time is never read as UTC")
def t_naive_time():
    from datetime import datetime, timedelta, timezone
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    ev = pb.parse_hikvision(_hik("VMD", when="2026-10-04T21:00:00"), "application/xml")
    ts = datetime.fromisoformat(ev["device_ts"])
    assert ts.tzinfo is not None and ts >= before, ev["device_ts"]
    assert ev["payload"]["device_time_raw"] == "2026-10-04T21:00:00"
    assert ev["payload"]["clock_source"] != "recorder"
    return "receive time used, recorder text kept"


@case("MNVR-026: the bridge collapses a burst per recorder token, like the Agent")
def t_burst():
    sent = []

    def fake_rpc(fn, payload):
        sent.append((fn, payload))
        return True, "{}"

    original = pb._rpc
    pb._rpc = fake_rpc
    try:
        pb.BURST = pb.BurstFilter()
        for second in (0, 1, 29):
            vendor, ev = pb.parse_any(_hik("linedetection"), "application/xml")
            assert pb.deliver("tokA", vendor, ev, mono=1000.0 + second) == 200
        vendor, ev = pb.parse_any(_hik("linedetection"), "application/xml")
        assert pb.deliver("tokB", vendor, ev, mono=1001.0) == 200   # another recorder
        vendor, ev = pb.parse_any(_hik("linedetection"), "application/xml")
        assert pb.deliver("tokA", vendor, ev, mono=1031.0) == 200   # window passed
    finally:
        pb._rpc = original
    tokens = [p["p_token"] for fn, p in sent if fn == "wl_ingest_push"]
    assert tokens == ["tokA", "tokB", "tokA"], tokens
    for _fn, p in sent:
        assert "_burst" not in json_dumps(p)
    return "3 of 5 posts ingested"


@case("MNVR-026: a failed ingest does not swallow the recorder's retry")
def t_burst_retry():
    calls = []

    def flaky(fn, payload):
        calls.append(fn)
        return (len(calls) > 1), "{}"

    original = pb._rpc
    pb._rpc = flaky
    try:
        pb.BURST = pb.BurstFilter()
        vendor, ev = pb.parse_any(_hik("VMD"), "application/xml")
        assert pb.deliver("tokA", vendor, ev, mono=500.0) == 502
        vendor, ev = pb.parse_any(_hik("VMD"), "application/xml")
        assert pb.deliver("tokA", vendor, ev, mono=502.0) == 200
    finally:
        pb._rpc = original
    assert calls == ["wl_ingest_push", "wl_ingest_push"], calls
    return "retry after 502 ingested"


@case("MNVR-011: the token alone names the recorder; events carry no site or recorder")
def t_token_names_recorder():
    sent = []
    original = pb._rpc
    pb._rpc = lambda fn, payload: (sent.append((fn, payload)) or (True, "{}"))
    try:
        pb.BURST = pb.BurstFilter()
        vendor, ev = pb.parse_any(_hik("VMD"), "application/xml")
        pb.deliver("recTok1", vendor, ev, mono=10.0)
    finally:
        pb._rpc = original
    (fn, payload), = sent
    assert fn == "wl_ingest_push" and payload["p_token"] == "recTok1"
    for e in payload["p_events"]:
        assert not ({"site_id", "recorder_id", "camera_id", "p_site_id"} & set(e)), e
    return "recorder identity comes from the per-recorder token"


@case("MNVR-026: the bridge ships the Agent's shared parser byte for byte")
def t_shared_parser_copy():
    canonical = (ROOT / "agent" / "drivers" / "alarm_parsing.py").read_bytes()
    shipped = (ROOT / "bridge" / "alarm_parsing.py").read_bytes()
    assert canonical == shipped, ("prototype/bridge/alarm_parsing.py differs from "
                                  "prototype/agent/drivers/alarm_parsing.py; copy it")
    docker = (ROOT / "bridge" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY alarm_parsing.py" in docker, "the bridge image must ship the parser"
    return "identical, and copied into the image"


def json_dumps(obj):
    import json
    return json.dumps(obj)


def run():
    print("Push-bridge parser")
    print("=" * 60)
    p = f = 0
    for name, fn in CASES:
        try:
            print(f"  PASS  {name}\n          {fn()}"); p += 1
        except AssertionError as e:
            print(f"  FAIL  {name}\n          {e}"); f += 1
        except Exception as e:                          # noqa: BLE001
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}"); f += 1
    print("=" * 60)
    print(f"  {p} passed, {f} failed")
    return 1 if f else 0


def test_push_bridge():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
