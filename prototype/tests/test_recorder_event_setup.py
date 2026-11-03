#!/usr/bin/env python3
"""Recorder event setup (5.1.11): the Agent makes each camera's detections reach WatchLog.

Field (HASCO DS-7608NI-Q1, 2026-10-30): motion on for all 8 channels, no motion event ever
arrived — the linkage lacked "Notify Surveillance Center". Pins:
- Hikvision: motion turned on and the center linkage added by editing the recorder's own XML in
  place (every other element sent back as read); removing it restores the trigger exactly;
- Dahua: motion turned on, smart motion (people) turned on only where present and off;
- every change is a verified site_control write that rolls back when the recorder does not keep it;
- channels switched off are left alone; a fully set-up recorder is not touched again until its
  channels change; a failure is retried next start; the ini switch turns it off;
- it runs in the background capability worker, never in Setup.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import recorder_event_setup as res  # noqa: E402
import site_control  # noqa: E402
from drivers.base import Channel, DriverError  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

NS = "http://www.hikvision.com/ver20/XMLSchema"


def motion_xml(ch, on):
    return (f'<?xml version="1.0" encoding="UTF-8"?><MotionDetection version="2.0" xmlns="{NS}">'
            f"<id>{ch}</id><enabled>{'true' if on else 'false'}</enabled><enableHighlight>false"
            "</enableHighlight><samplingInterval>2</samplingInterval><MotionDetectionLayout>"
            "<sensitivityLevel>60</sensitivityLevel><layout><gridMap>fffffc</gridMap></layout>"
            "</MotionDetectionLayout></MotionDetection>")


def trigger_xml(ch, *methods, empty_list=False):
    notes = "".join(f"<EventTriggerNotification><id>{m}-{ch}</id><notificationMethod>{m}"
                    "</notificationMethod><notificationRecurrence>beginning</notificationRecurrence>"
                    "</EventTriggerNotification>" for m in methods)
    lst = "<EventTriggerNotificationList/>" if empty_list else \
        f"<EventTriggerNotificationList>{notes}</EventTriggerNotificationList>"
    return (f'<?xml version="1.0" encoding="UTF-8"?><EventTrigger version="2.0" xmlns="{NS}">'
            f"<id>VMD-{ch}</id><eventType>VMD</eventType><eventDescription>VMD Event trigger"
            f"</eventDescription><videoInputChannelID>{ch}</videoInputChannelID>{lst}</EventTrigger>")


class FakeHik(HikvisionDriver):
    def __init__(self, docs, channels=("1",), keep=True):
        super().__init__("http://127.0.0.1", "u", "p")
        self.docs, self.puts, self.keep = dict(docs), [], keep
        self.chans = [Channel(channel=c) if not c.startswith("-") else Channel(channel=c[1:], enabled=False)
                      for c in channels]

    def list_channels(self):
        return self.chans

    def _get(self, path, **kw):
        if path not in self.docs:
            raise DriverError("HTTP 404")
        return SimpleNamespace(text=self.docs[path], content=self.docs[path].encode())

    def _put(self, path, body):
        self.puts.append((path, body))
        if self.keep:
            self.docs[path] = body

    def close(self):
        pass


def hik_docs(ch="1", motion=True, methods=("record", "beep")):
    return {f"/ISAPI/System/Video/inputs/channels/{ch}/motionDetection": motion_xml(ch, motion),
            f"/ISAPI/Event/triggers/VMD-{ch}": trigger_xml(ch, *methods)}


class FakeDahua(DahuaDriver):
    def __init__(self, cfg_text, channels=("1",)):
        self.cfg, self.sets = dict(cfg_text), []
        self.last_activity_monotonic = 0.0
        self.chans = [Channel(channel=c) for c in channels]

    def list_channels(self):
        return self.chans

    def _get(self, path, **kw):
        if "action=setConfig&" in path:
            k, v = path.split("action=setConfig&", 1)[1].split("=", 1)
            self.sets.append((k, v))
            self.cfg[f"table.{k}"] = v
            return "OK"
        name = path.rsplit("name=", 1)[1]
        rows = [f"{k}={v}" for k, v in self.cfg.items() if k.startswith(f"table.{name}[")]
        if not rows:
            raise DriverError("Error")
        return "\n".join(rows) + "\n"

    def close(self):
        pass


# --- Hikvision -------------------------------------------------------------------------------

def test_hikvision_reads_motion_and_center_linkage():
    d = FakeHik(hik_docs(methods=("center", "beep")))
    assert d.get_motion_events("1") == {"motion": True, "notify_center": True}
    d = FakeHik(hik_docs(motion=False))
    assert d.get_motion_events("1") == {"motion": False, "notify_center": False}
    assert FakeHik({}).get_motion_events("1") == {"motion": None, "notify_center": None}


def test_hikvision_adds_center_in_place_and_keeps_the_rest():
    before = trigger_xml("1", "record", "beep")
    d = FakeHik(hik_docs())
    out = site_control.execute_write(d, "configure_motion_events",
                                     {"channel": "1", "motion": True, "notify_center": True})
    assert out["ok"] and out["verified"] and out["changed"]
    assert out["before"] == {"motion": True, "notify_center": False}
    assert [p for p, _ in d.puts] == ["/ISAPI/Event/triggers/VMD-1"]   # motion already on: untouched
    body = d.puts[0][1]
    assert "<notificationMethod>center</notificationMethod>" in body
    assert body.replace(HikvisionDriver._CENTER_NOTIFICATION, "") == before
    # removing it gives the recorder back its trigger exactly
    d.set_motion_events("1", notify_center=False)
    assert d.docs["/ISAPI/Event/triggers/VMD-1"] == before


def test_hikvision_turns_motion_on_touching_only_enabled():
    d = FakeHik(hik_docs(motion=False))
    out = site_control.execute_write(d, "configure_motion_events",
                                     {"channel": "1", "motion": True, "notify_center": True})
    assert out["ok"] and out["after"] == {"motion": True, "notify_center": True}
    path = "/ISAPI/System/Video/inputs/channels/1/motionDetection"
    assert d.docs[path] == motion_xml("1", True)
    assert "<enableHighlight>false</enableHighlight>" in d.docs[path]


def test_hikvision_empty_or_missing_notification_list():
    d = FakeHik({**hik_docs(), "/ISAPI/Event/triggers/VMD-1": trigger_xml("1", empty_list=True)})
    d.set_motion_events("1", notify_center=True)
    assert d.get_motion_events("1")["notify_center"] is True
    bare = trigger_xml("1", "record").replace(
        "<EventTriggerNotificationList><EventTriggerNotification><id>record-1</id><notificationMethod>record"
        "</notificationMethod><notificationRecurrence>beginning</notificationRecurrence>"
        "</EventTriggerNotification></EventTriggerNotificationList>", "")
    d = FakeHik({**hik_docs(), "/ISAPI/Event/triggers/VMD-1": bare})
    d.set_motion_events("1", notify_center=True)
    assert d.get_motion_events("1")["notify_center"] is True


def test_hikvision_recorder_that_ignores_the_write_is_rolled_back_and_reported():
    d = FakeHik(hik_docs(), keep=False)
    out = site_control.execute_write(d, "configure_motion_events",
                                     {"channel": "1", "motion": True, "notify_center": True})
    assert out["ok"] is False and out["verified"] is False and out["rolled_back"] is True


# --- Dahua -----------------------------------------------------------------------------------

def test_dahua_motion_on_and_smart_motion_people_only_where_present_and_off():
    d = FakeDahua({"table.MotionDetect[0].Enable": "false", "table.MotionDetect[1].Enable": "true",
                   "table.SmartMotionDetect[0].Enable": "false",
                   "table.SmartMotionDetect[0].ObjectTypes.Human": "false",
                   "table.SmartMotionDetect[0].ObjectTypes.Vehicle": "false",
                   "table.SmartMotionDetect[1].Enable": "true",
                   "table.SmartMotionDetect[1].ObjectTypes.Human": "true",
                   "table.SmartMotionDetect[1].ObjectTypes.Vehicle": "false"}, channels=("1", "2"))
    out = res.configure(d, ["1", "2"])
    assert out == {"supported": True, "channels": 2, "changed": 1, "already": 1, "failed": []}
    assert ("MotionDetect[0].Enable", "true") in d.sets
    assert ("SmartMotionDetect[0].Enable", "true") in d.sets
    assert ("SmartMotionDetect[0].ObjectTypes.Human", "true") in d.sets
    assert not any(k.endswith("Vehicle") for k, _ in d.sets)            # vehicles left as they were
    assert not any(k.startswith(("MotionDetect[1]", "SmartMotionDetect[1]")) for k, _ in d.sets)


def test_dahua_without_smart_motion_only_sets_motion():
    d = FakeDahua({"table.MotionDetect[0].Enable": "false"})
    out = res.configure(d, ["1"])
    assert out["changed"] == 1 and not out["failed"]
    assert d.sets == [("MotionDetect[0].Enable", "true")]


# --- the background pass -----------------------------------------------------------------------

class Cloud:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append(name)
        return {"ok": True}


def _run(driver, td, **kw):
    cfg = SimpleNamespace(state_path=Path(td) / "agent_state.json", _ini_path=kw.pop("ini", None))
    cloud = Cloud()
    out = res.setup_once(cfg, {"agent_id": "a", "agent_key": "k"}, cloud,
                         lambda _c: (driver, None), **kw)
    return out, cloud


def test_background_pass_sets_up_once_skips_disabled_channels_and_resends_census():
    docs = {**hik_docs("1"), **hik_docs("2", methods=("center",)), **hik_docs("3")}
    with tempfile.TemporaryDirectory() as td:
        d = FakeHik(docs, channels=("1", "2", "-3"))
        out, cloud = _run(d, td)
        assert out == {"supported": True, "channels": 2, "changed": 1, "already": 1, "failed": []}
        assert all("VMD-3" not in p and "channels/3/" not in p for p, _ in d.puts)
        assert "wl_sync_capabilities" in cloud.calls                  # portal sees the new state
        d.puts.clear()
        assert _run(d, td)[0] is None and d.puts == []                # set up: not touched again
        d.chans.append(Channel(channel="4"))
        d.docs.update(hik_docs("4"))
        out, _ = _run(d, td)                                           # a new camera: run again
        assert out["changed"] == 1 and any("VMD-4" in p for p, _ in d.puts)


def test_background_pass_retries_after_a_failure_and_honours_the_ini_switch():
    with tempfile.TemporaryDirectory() as td:
        d = FakeHik(hik_docs(), keep=False)
        out, _ = _run(d, td)
        assert len(out["failed"]) == 1
        d.keep = True
        assert _run(d, td)[0]["changed"] == 1                         # not remembered: retried
        ini = Path(td) / "watchlog.ini"
        ini.write_text("[watchlog]\nrecorder_event_setup = false\n", encoding="utf-8")
        assert _run(FakeHik(hik_docs()), td, ini=ini, force=True)[0] is None


def test_unsupported_driver_and_crashing_recorder_never_raise():
    from drivers.base import NvrDriver
    with tempfile.TemporaryDirectory() as td:
        class Plain(FakeHik):
            MOTION_EVENT_FIELDS = NvrDriver.MOTION_EVENT_FIELDS
        assert _run(Plain(hik_docs()), td)[0]["supported"] is False
        cfg = SimpleNamespace(state_path=Path(td) / "s.json", _ini_path=None)

        def boom(_c):
            raise OSError("recorder unreachable")
        assert res.setup_once(cfg, {}, Cloud(), boom) is None


def test_runs_in_the_background_worker_and_never_in_setup():
    cap = (ROOT / "agent" / "capability_sync.py").read_text(encoding="utf-8")
    assert "recorder_event_setup.setup_once(cfg, state, cloud, open_fn, recorder_id=recorder_id" in cap
    assert "recorder_event_setup" not in (ROOT / "agent" / "setup_backend.py").read_text(encoding="utf-8")
    # local only: the cloud catalog cannot request it (wl_site_command_propose_write parity)
    assert "configure_motion_events" not in site_control.WRITE_ACTIONS
