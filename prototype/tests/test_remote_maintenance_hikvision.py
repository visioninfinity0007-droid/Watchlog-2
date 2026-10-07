#!/usr/bin/env python3
"""Regression tests for secure remote maintenance and Hikvision readback."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import remote_update
from drivers.base import Channel
from drivers.hikvision import HikvisionDriver
from drivers.native_recorder import NativeHikvisionDriver


class DummyCloud:
    def __init__(self):
        self.calls = []

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return {"ok": True}


def _cfg(root: Path):
    return SimpleNamespace(
        state_path=root / "agent_state.json",
        update_url="https://updates.example.test/manifest.json",
        update_public_key="public-key",
        update_require_signature=True,
        update_channel="production",
    )


def test_remote_update_up_to_date_completes_without_restart():
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(Path(td))
        cloud = DummyCloud()
        old = remote_update._fetch_manifest
        try:
            remote_update._fetch_manifest = lambda _cfg: {"action": "up-to-date"}
            out = remote_update.stage_latest(
                cloud, {"agent_id": "a", "agent_key": "k"}, cfg,
                {"request_id": "req-1"},
            )
        finally:
            remote_update._fetch_manifest = old
        assert out["restart"] is False
        assert cloud.calls[-1][0] == "wl_agent_complete_update_request"
        assert cloud.calls[-1][1]["p_ok"] is True


def test_remote_update_stages_only_manifest_selected_verified_package():
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(Path(td))
        cloud = DummyCloud()
        pkg = remote_update._package_path(cfg)
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_bytes(b"signed-package")
        old_fetch = remote_update._fetch_manifest
        old_download = remote_update._download_verified
        try:
            remote_update._fetch_manifest = lambda _cfg: {
                "action": "update",
                "target": "5.0.22",
                "url": "https://updates.example.test/watchlog-agent.exe",
                "sha256": "a" * 64,
                "size": len(b"signed-package"),
            }
            remote_update._download_verified = lambda _cfg, _plan: pkg
            out = remote_update.stage_latest(
                cloud, {"agent_id": "a", "agent_key": "k"}, cfg,
                {"request_id": "req-2"},
            )
        finally:
            remote_update._fetch_manifest = old_fetch
            remote_update._download_verified = old_download

        pending = json.loads(remote_update._pending_path(cfg).read_text(encoding="utf-8"))
        assert out["restart"] is True
        assert pending["request_id"] == "req-2"
        assert pending["target_version"] == "5.0.22"
        assert pending["sha256"] == "a" * 64
        assert cloud.calls[-1][0] == "wl_agent_stage_update_request"


def test_remote_update_reports_launcher_result_once():
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(Path(td))
        cloud = DummyCloud()
        path = remote_update._result_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "request_id": "req-3",
            "ok": False,
            "detail": "new agent failed startup health check; previous version restored",
            "applied_version": "",
        }), encoding="utf-8")

        assert remote_update.report_previous_result(
            cloud, {"agent_id": "a", "agent_key": "k"}, cfg
        ) is True
        assert not path.exists()
        assert cloud.calls[-1][0] == "wl_agent_complete_update_request"
        assert cloud.calls[-1][1]["p_ok"] is False


class FakeHikvision(HikvisionDriver):
    def __init__(self, docs):
        super().__init__("http://127.0.0.1", "u", "p")
        self.docs = docs

    def list_channels(self):
        return [Channel(channel="1", name="Front")]

    def _xml(self, path):
        raw = self.docs.get(path)
        if raw is None:
            from drivers.base import DriverError
            raise DriverError("unsupported")
        return ET.fromstring(raw)


def test_hikvision_readback_clock_storage_and_motion2():
    d = FakeHikvision({
        "/ISAPI/System/Video/inputs/channels/1/motionDetection":
            "<MotionDetection><enabled>true</enabled><targetType>human,vehicle</targetType></MotionDetection>",
        "/ISAPI/System/Video/inputs/channels/1/motionDetection/capabilities":
            "<MotionDetectionCap><targetType opt='human,vehicle'>human</targetType><vehicle>true</vehicle></MotionDetectionCap>",
        "/ISAPI/Smart/LineDetection/1":
            "<LineDetection><enabled>true</enabled></LineDetection>",
        "/ISAPI/Smart/FieldDetection/1":
            "<FieldDetection><enabled>false</enabled></FieldDetection>",
        "/ISAPI/Smart/RegionEntrance/1":
            "<RegionEntrance><enabled>true</enabled></RegionEntrance>",
        "/ISAPI/Smart/RegionExiting/1":
            "<RegionExiting><enabled>false</enabled></RegionExiting>",
        "/ISAPI/System/time":
            "<Time><timeMode>NTP</timeMode><localTime>2026-09-28T01:00:00+05:00</localTime><timeZone>CST-5:00:00</timeZone><dstEnabled>false</dstEnabled></Time>",
        "/ISAPI/System/time/ntpServers":
            "<NTPServerList><NTPServer><hostName>pool.ntp.org</hostName></NTPServer></NTPServerList>",
        "/ISAPI/Smart/storageDetection":
            "<storageDetection><healthState>good</healthState><badBlocks>0</badBlocks></storageDetection>",
        # 'ok' needs the disk list: a healthy disk with free space (5.1.2 storage truth).
        "/ISAPI/ContentMgmt/Storage":
            "<storage><hddList><hdd><id>1</id><hddName>hdd1</hddName><status>ok</status>"
            "<capacity>953869</capacity><freeSpace>400000</freeSpace><property>RW</property>"
            "</hdd></hddList></storage>",
    })
    try:
        caps = d.capabilities()
        analytics = {a["key"]: a for a in caps["channels"][0]["analytics"]}
        assert analytics["motion"]["active"] is True
        assert analytics["human_vehicle"]["supported"] is True
        assert analytics["human_vehicle"]["active"] is True
        assert analytics["line_crossing"]["active"] is True
        assert analytics["region_entry"]["active"] is True
        assert analytics["region_exit"]["active"] is False

        clock = d.get_clock()
        assert clock["supported"] is True
        assert clock["ntp_enabled"] is True
        assert clock["ntp_server"] == "pool.ntp.org"

        storage = d.storage_status()
        assert storage["supported"] is True
        assert storage["state"] == "ok"
        assert storage["disks"][0]["free_bytes"] == 400000 * 1024 * 1024

        # storageDetection 'good' alone says nothing about free space: never 'ok' from absence.
        del d.docs["/ISAPI/ContentMgmt/Storage"]
        alone = d.storage_status()
        assert alone["supported"] is True and alone["state"] is None
        assert alone["reason"] == "capacity_unknown"
    finally:
        d.close()


def test_hikvision_motion_target_is_preserved_as_native_ai():
    d = NativeHikvisionDriver("http://127.0.0.1", "u", "p")
    raw = b"""<EventNotificationAlert>
      <eventType>VMD</eventType>
      <eventState>active</eventState>
      <channelID>2</channelID>
      <dateTime>2026-09-28T01:00:00+05:00</dateTime>
      <activePostCount>1</activePostCount>
      <targetType>human</targetType>
    </EventNotificationAlert>"""
    try:
        ev = d._parse_alert(raw)
        assert ev is not None
        assert ev.event_type == "motion"
        assert ev.payload["native_ai"] is True
        assert ev.payload["source"] == "recorder_native_ai"
        assert ev.payload["native_code"].lower() == "vmd"
        assert ev.payload["targets"] == ["human"]
    finally:
        d.close()


def test_release_runtime_wraps_remote_update_worker():
    src = (ROOT / "agent" / "release_agent.py").read_text(encoding="utf-8")
    assert "import remote_update" in src
    assert "remote_update.wrap_cmd_run" in src


def test_success_result_waits_for_health_window():
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(Path(td))
        cloud = DummyCloud()
        path = remote_update._result_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "request_id": "req-health",
            "ok": True,
            "detail": "awaiting health",
            "applied_version": "5.0.22",
            "health_not_before": "2999-01-01T00:00:00+00:00",
        }), encoding="utf-8")
        assert remote_update.report_previous_result(
            cloud, {"agent_id": "a", "agent_key": "k"}, cfg
        ) is False
        assert path.exists()
        assert cloud.calls == []


def test_runtime_capabilities_require_successful_poll_proof():
    src = (ROOT / "agent" / "analytics_agent.py").read_text(encoding="utf-8")
    core = (ROOT / "agent" / "watchlog_agent.py").read_text(encoding="utf-8")
    updater_src = (ROOT / "agent" / "remote_update.py").read_text(encoding="utf-8")
    assert "site_control_last_poll_monotonic" in src
    assert "site_control_last_poll_monotonic" in core
    assert "remote_update_last_poll_monotonic" in src
    assert "remote_update_last_poll_monotonic" in updater_src
