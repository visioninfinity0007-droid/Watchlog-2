#!/usr/bin/env python3
"""Full acceptance suite (5.1.2, workstream D): every check's PASS/FAIL/UNKNOWN/UNSUPPORTED.

Owner rule under test: no capability is PASS because code exists. Each check is PASS only on
positive evidence; a failed probe is FAIL with its (redacted) error; an unjudgeable one is
UNKNOWN with a reason; a capability the hardware/site does not offer is UNSUPPORTED. The
suite runs per recorder with that recorder's own Config/driver, under one time budget, and
its result stays bounded (no image or clip bytes).

Fakes: a Hikvision-like and a Dahua-like recorder (vendor archive searches patched at the
dahua_archive / hikvision_archive seam the suite calls), a fake runtime holder and a fake
Agent environment. No recorder, cloud or network.
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import dahua_archive  # noqa: E402
import full_acceptance as fa  # noqa: E402
import hikvision_archive  # noqa: E402
from drivers.base import Channel, DeviceInfo, DriverError, NvrAuthFailed, NvrDriver  # noqa: E402

NOW = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
REC_H = "11111111-1111-1111-1111-111111111111"
REC_D = "22222222-2222-2222-2222-222222222222"


def jpeg(width=640, height=360) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (90, 90, 90)).save(buf, "JPEG")
    return buf.getvalue()


JPEG = jpeg()
DAV = b"DHAV" + b"\x00" * 2000
HIK_PS = b"IMKH" + b"\x00" * 2000


class FakeRecorder:
    """A recorder driver: identity, channels, faults, storage, stills, clips."""

    verified_against_hardware = True

    def __init__(self, name, *, vendor, model, serial, channels=("1", "2"), video_loss=(),
                 storage=None, snapshot=None, clip=None, snapshot_delay=0.0, faults=True):
        self.name = name
        self.info = DeviceInfo(vendor=vendor, model=model, firmware="fw-1", serial=serial,
                               driver=name)
        self.channels = list(channels)
        self.video_loss = list(video_loss)
        self.storage = storage if storage is not None else {"supported": True, "state": "ok"}
        self.snapshot = snapshot if snapshot is not None else {}
        self.clip = clip
        self.snapshot_delay = snapshot_delay
        self.faults = faults
        self.snapshot_calls = []
        self.clip_calls = []
        self.closed = False

    def list_channels(self):
        return [Channel(channel=c, name=f"Cam {c}") for c in self.channels]

    def current_faults(self):
        return {"supported": self.faults, "video_loss": list(self.video_loss),
                "video_blind": []}

    def storage_status(self):
        return dict(self.storage)

    def get_snapshot(self, channel):
        self.snapshot_calls.append(channel)
        if self.snapshot_delay:
            time.sleep(self.snapshot_delay)
        return self.snapshot.get(channel, JPEG)

    def get_clip(self, channel, start, end):
        self.clip_calls.append((channel, start, end))
        if isinstance(self.clip, BaseException):
            raise self.clip
        return self.clip

    def close(self):
        self.closed = True


def hik(**kw):
    kw.setdefault("clip", HIK_PS)
    return FakeRecorder("hikvision-isapi", vendor="Hikvision", model="DS-7608NI-Q1",
                        serial="HIKSERIAL1", **kw)


def dahua(**kw):
    kw.setdefault("clip", DAV)
    return FakeRecorder("dahua-cgi", vendor="Dahua", model="DH-XVR1B08-I",
                        serial="8E06857PAZ7EB3A", **kw)


def cfg_for(recorder_id, serial, **kw):
    base = dict(recorder_cloud_id=recorder_id, recorder_identity_fingerprint=f"serial:{serial}",
                credential_error=None, spool_path=Path("spool.sqlite"), spool_max_rows=1000,
                recovery_enabled=True, recovery_seconds=300, camera_profiles=[])
    base.update(kw)
    return SimpleNamespace(**base)


def live_holder(channels=("1", "2"), *, stream_connected=True, mono=1000.0):
    return {
        "camera_mapping": {c: f"cam-{c}" for c in channels},
        "live_driver": SimpleNamespace(last_activity_monotonic=mono - 5),
        "recorder_live_at": mono - 5,
        "event_stream": {"connected": stream_connected, "connected_at": "2026-10-07T09:00:00Z",
                         "last_frame_at": "2026-10-07T09:59:50Z",
                         "last_error": None if stream_connected else "HTTP 503"},
        "recovery_cycle_at": mono - 60,
        "recovery_last_ok_at": "2026-10-07T09:59:00Z",
    }


class FakeEnv(fa.Env):
    def __init__(self, drivers, *, holders=None, health=None, analytics=None, base=None,
                 state=None, auth=None, spool=None, manifest=None, command_id="cmd-1",
                 meta=None, mono=1000.0, open_delay=0.0):
        self.drivers = drivers                  # recorder_id -> FakeRecorder | exception
        self.holders = holders or {}
        self.health = health if health is not None else {
            "heartbeat_at": "2026-10-07T09:59:30Z", "site_control_poll_at": "2026-10-07T09:59:50Z"}
        self.analytics = analytics if analytics is not None else {
            "detector_available": True, "last_sample_at": "2026-10-07T09:58:00Z",
            "updated_at": "2026-10-07T09:59:40Z", "samples_ok": 10, "sample_errors": 0}
        self.base = base or SimpleNamespace(
            heartbeat_seconds=60, site_control_seconds=15, analytics_enabled=True,
            update_url="https://updates.example.test/manifest.json", update_public_key="",
            update_channel="production")
        self._state = state if state is not None else {
            "agent_id": "agent-1", "agent_key": "secret-agent-key", "site_id": "site-1"}
        self.auth = auth if auth is not None else {"agent_id": "agent-1", "site_id": "site-1"}
        self.spool = spool if spool is not None else {
            "exists": True, "depth": 3, "max_rows": 1000, "overflow": False}
        self.manifest = manifest
        self.command_id = command_id
        self.meta = meta if meta is not None else {
            "version": "5.1.2", "build_sha": "abc123def456", "build_channel": "production"}
        self.mono = mono
        self.open_delay = open_delay
        self.opened = []
        self._t0 = time.monotonic()

    def now(self):
        return NOW

    def monotonic(self):
        return self.mono + (time.monotonic() - self._t0)

    def agent_meta(self):
        return dict(self.meta)

    def state(self):
        return self._state

    def cloud_auth(self):
        if isinstance(self.auth, BaseException):
            raise self.auth
        return self.auth

    def runtime_health(self):
        return self.health

    def base_config(self):
        return self.base

    def open_driver(self, cfg):
        self.opened.append(cfg)
        if self.open_delay:
            time.sleep(self.open_delay)
        drv = self.drivers[cfg.recorder_cloud_id]
        if isinstance(drv, BaseException):
            raise drv
        return drv, drv.info

    def open_archive(self, cfg, live):
        return live

    def holder(self, recorder_id):
        return self.holders.get(recorder_id)

    def spool_probe(self, cfg):
        return dict(self.spool)

    def analytics_status(self):
        return self.analytics

    def fetch_manifest(self, url):
        if isinstance(self.manifest, BaseException):
            raise self.manifest
        return self.manifest



@pytest.fixture(autouse=True)
def archives(monkeypatch):
    """Vendor archive searches at the seam the suite calls. Per channel: 'found' | 'empty' |
    'unsupported' | 'error'."""
    plan = {"dahua": {}, "hik": {}, "calls": []}

    def dahua_find(driver, channel, start, end, *, max_items=100):
        plan["calls"].append(("dahua", channel, start, end))
        outcome = plan["dahua"].get(channel, "found")
        if outcome == "error":
            raise DriverError("search failed at http://192.168.1.108/cgi-bin/mediaFileFind.cgi")
        return [{"StartTime": "2026-10-07 14:50:00", "EndTime": "2026-10-07 15:00:00"}] \
            if outcome == "found" else []

    def hik_search(driver, channel, start, end, *, offset=0, limit=40):
        plan["calls"].append(("hik", channel, start, end))
        outcome = plan["hik"].get(channel, "found")
        if outcome == "unsupported":
            raise hikvision_archive.ArchiveRejected("not supported", affirmative=True)
        if outcome == "error":
            raise DriverError("search failed")
        rows = [{"track_id": "101", "start": "2026-10-07T09:40:00Z",
                 "end": "2026-10-07T09:59:00Z", "playback_uri": "rtsp://x"}] \
            if outcome == "found" else []
        return {"status": "supported", "matches": rows, "next_offset": None, "incomplete": 0}

    monkeypatch.setattr(dahua_archive, "find_recordings", dahua_find)
    monkeypatch.setattr(hikvision_archive, "search_recordings", hik_search)
    return plan


def target(rid, cfg, name="Main", primary=True):
    return fa.Target(rid, name, cfg, primary=primary)


def by_name(result, name, channel=None, recorder_id=None):
    out = [c for c in result["checks"] if c["name"] == name
           and (channel is None or c["channel"] == channel)
           and (recorder_id is None or c["recorder_id"] == recorder_id)]
    assert out, (name, channel, [c["name"] for c in result["checks"]])
    return out[0]


def statuses(result):
    return {(c["name"], c["channel"]): c["status"] for c in result["checks"]}


def signed_manifest():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import updater
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    manifest = {"schema": updater.MANIFEST_SCHEMA, "channels": {"production": {
        "version": "5.1.2", "url": "https://updates.example.test/a.exe",
        "sha256": "0" * 64, "size": 10}}}
    manifest["signature"] = base64.b64encode(
        key.sign(updater.canonical_manifest_bytes(manifest))).decode()
    return json.dumps(manifest), pub


def full_env(driver, rid=REC_H, **kw):
    text, pub = signed_manifest()
    kw.setdefault("manifest", text)
    env = FakeEnv({rid: driver}, holders={rid: live_holder(driver.channels)}, **kw)
    env.base.update_public_key = pub
    return env


# ---------------------------------------------------------------------------
# all-PASS on both vendor shapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("make,rid,method", [(hik, REC_H, "hik"), (dahua, REC_D, "dahua")])
def test_healthy_recorder_is_all_pass_with_positive_evidence(archives, make, rid, method):
    driver = make()
    env = full_env(driver, rid)
    result = fa.run_suite(env, [target(rid, cfg_for(rid, driver.info.serial))])

    bad = {k: v for k, v in statuses(result).items() if v != fa.PASS}
    assert not bad, bad
    s = result["summary"]
    assert s["passed"] == s["total"] == len(result["checks"]) and s["failed"] == 0
    # 6 agent + 11 recorder + 2 per camera.
    assert s["total"] == 6 + 11 + 2 * 2
    assert s["hardware"] == {"vendor": driver.info.vendor, "model": driver.info.model,
                             "firmware": "fw-1", "serial": driver.info.serial}
    assert s["agent"] == {"version": "5.1.2", "build_sha": "abc123def456"}
    assert s["tested_at"] == "2026-10-07T10:00:00Z"
    # The vendor's own archive search proved each camera's recording.
    assert {c[0] for c in archives["calls"]} == {method}
    rec = by_name(result, fa.RECORDING, "1")
    assert rec["evidence"]["segments_found"] == 1 and rec["healthy"] is True
    snap = by_name(result, fa.SNAPSHOT, "1")
    assert snap["evidence"]["width"] == 640 and snap["evidence"]["height"] == 360
    clip = by_name(result, fa.EVIDENCE_CLIP)
    assert clip["evidence"]["container"] in ("dav", "hikvision-ps")
    assert clip["evidence"]["window_seconds"] <= 6
    # The clip window is at most 6 s.
    (_ch, start, end), = driver.clip_calls
    assert (end - start).total_seconds() <= 6
    for check in result["checks"]:
        assert set(check) == {"name", "scope", "recorder_id", "channel", "status", "supported",
                              "enabled", "healthy", "last_success_at", "last_error", "evidence",
                              "duration_ms"}
        assert check["last_success_at"], check["name"]
    assert driver.closed


def test_operator_table_shape():
    driver = hik()
    result = fa.run_suite(full_env(driver), [target(REC_H, cfg_for(REC_H, "HIKSERIAL1"))])
    lines = fa.format_table(result)
    assert lines[0] == "Agent                    PASS"
    total = result["summary"]["total"]
    assert lines[-4] == f"{total}/{total} PASS"
    assert lines[-3] == "Hardware: Hikvision DS-7608NI-Q1 firmware fw-1 serial HIKSERIAL1"
    assert lines[-2] == "Agent: 5.1.2 (build abc123def456)"
    assert lines[-1] == "Tested: 2026-10-07T10:00:00Z"
    assert "Snapshot ch1             PASS" in lines


def test_no_media_bytes_or_secrets_in_the_result():
    driver = dahua()
    env = full_env(driver, REC_D)
    result = fa.run_suite(env, [target(REC_D, cfg_for(REC_D, driver.info.serial))])
    text = json.dumps(result)
    assert base64.b64encode(JPEG[:30]).decode()[:20] not in text
    assert "DHAV" not in text and "secret-agent-key" not in text
    assert by_name(result, fa.SNAPSHOT, "1")["evidence"]["bytes"] == len(JPEG)


# ---------------------------------------------------------------------------
# agent-scope checks
# ---------------------------------------------------------------------------

def _agent_result(env):
    return fa.run_suite(env, [], only=set(fa.AGENT_CHECKS))


def test_agent_without_build_sha_is_unknown_not_pass():
    env = full_env(hik(), meta={"version": "5.1.2", "build_sha": ""})
    c = by_name(_agent_result(env), fa.AGENT)
    assert c["status"] == fa.UNKNOWN and "build_sha" in c["last_error"]


@pytest.mark.parametrize("auth,state,expect", [
    ({"agent_id": "other", "site_id": "site-1"}, None, fa.FAIL),
    (RuntimeError("wl_agent_preflight_auth: HTTP 401 28000 agent not recognised"), None, fa.FAIL),
    ({"agent_id": "agent-1", "site_id": "site-1"}, {}, fa.FAIL),
])
def test_cloud_auth_failures(auth, state, expect):
    env = full_env(hik(), auth=auth, state=state)
    assert by_name(_agent_result(env), fa.CLOUD_AUTH)["status"] == expect


@pytest.mark.parametrize("health,expect", [
    ({"heartbeat_at": "2026-10-07T09:50:00Z"}, fa.FAIL),     # 10 min old
    ({}, fa.UNKNOWN),
    ({"heartbeat_at": "2026-10-07T09:59:00Z"}, fa.PASS),
])
def test_heartbeat_age(health, expect):
    env = full_env(hik(), health=health, command_id=None)
    assert by_name(_agent_result(env), fa.HEARTBEAT)["status"] == expect


def test_local_analytics_states():
    env = full_env(hik())
    env.base.analytics_enabled = False
    c = by_name(_agent_result(env), fa.LOCAL_ANALYTICS)
    assert c["status"] == fa.UNSUPPORTED and c["enabled"] is False

    env = full_env(hik(), analytics={"detector_available": False,
                                     "updated_at": "2026-10-07T09:59:40Z",
                                     "last_sample_at": "2026-10-07T09:59:00Z"})
    assert by_name(_agent_result(env), fa.LOCAL_ANALYTICS)["status"] == fa.FAIL

    env = full_env(hik(), analytics={})
    assert by_name(_agent_result(env), fa.LOCAL_ANALYTICS)["status"] == fa.UNKNOWN

    env = full_env(hik(), analytics={"detector_available": True,
                                     "updated_at": "2026-10-07T09:59:40Z",
                                     "last_sample_at": "2026-10-07T09:00:00Z"})
    assert by_name(_agent_result(env), fa.LOCAL_ANALYTICS)["status"] == fa.FAIL

    env = full_env(hik(), analytics={"detector_available": True,
                                     "updated_at": "2026-10-07T09:59:40Z",
                                     "last_sample_at": None})
    assert by_name(_agent_result(env), fa.LOCAL_ANALYTICS)["status"] == fa.UNKNOWN


def test_site_control_check():
    assert by_name(_agent_result(full_env(hik())), fa.SITE_CONTROL)["status"] == fa.PASS
    env = full_env(hik(), command_id=None, health={"heartbeat_at": "2026-10-07T09:59:30Z"})
    assert by_name(_agent_result(env), fa.SITE_CONTROL)["status"] == fa.UNKNOWN
    env = full_env(hik(), command_id=None,
                   health={"site_control_poll_at": "2026-10-07T09:30:00Z"})
    assert by_name(_agent_result(env), fa.SITE_CONTROL)["status"] == fa.FAIL
    env = full_env(hik(), command_id=None,
                   health={"site_control_poll_at": "2026-10-07T09:59:30Z"})
    assert by_name(_agent_result(env), fa.SITE_CONTROL)["status"] == fa.PASS


def test_remote_update_check(monkeypatch):
    import updater
    env = full_env(hik())
    assert by_name(_agent_result(env), fa.REMOTE_UPDATE)["status"] == fa.PASS

    env = full_env(hik())
    env.base.update_url = ""
    c = by_name(_agent_result(env), fa.REMOTE_UPDATE)
    assert c["status"] == fa.UNSUPPORTED and c["enabled"] is False

    env = full_env(hik())
    env.base.update_url = "http://updates.example.test/manifest.json"
    assert by_name(_agent_result(env), fa.REMOTE_UPDATE)["status"] == fa.FAIL

    env = full_env(hik())
    env.base.update_public_key = ""
    assert by_name(_agent_result(env), fa.REMOTE_UPDATE)["status"] == fa.FAIL

    env = full_env(hik())
    _text, other_key = signed_manifest()
    env.base.update_public_key = other_key                     # signature does not verify
    c = by_name(_agent_result(env), fa.REMOTE_UPDATE)
    assert c["status"] == fa.FAIL and c["evidence"]["signature"] == "invalid"

    env = full_env(hik(), manifest=OSError("unreachable"))
    assert by_name(_agent_result(env), fa.REMOTE_UPDATE)["status"] == fa.FAIL

    monkeypatch.setattr(updater, "verify_manifest_signature", lambda *_a: None)
    env = full_env(hik())
    assert by_name(_agent_result(env), fa.REMOTE_UPDATE)["status"] == fa.UNKNOWN


# ---------------------------------------------------------------------------
# recorder + camera checks
# ---------------------------------------------------------------------------

def run_one(driver, rid=REC_H, cfg=None, env=None, **kw):
    env = env or full_env(driver, rid)
    cfg = cfg or cfg_for(rid, driver.info.serial)
    return fa.run_suite(env, [target(rid, cfg)], **kw), env


def test_recorder_auth_rejected_blocks_device_checks_but_not_runtime_evidence():
    env = full_env(hik())
    env.drivers[REC_H] = NvrAuthFailed("HTTP 401 from http://192.168.1.64/ISAPI")
    result, _ = run_one(hik(), env=env)
    auth = by_name(result, fa.RECORDER_AUTH)
    assert auth["status"] == fa.FAIL and "192.168" not in auth["last_error"]
    for name in (fa.RECORDER_IDENTITY, fa.CAMERA_INVENTORY, fa.STORAGE, fa.ARCHIVE_SEARCH,
                 fa.EVIDENCE_STILL, fa.EVIDENCE_CLIP):
        c = by_name(result, name)
        assert c["status"] == fa.UNKNOWN and c["last_error"] == "recorder_not_open", name
    # Cameras WatchLog knows are still listed, UNKNOWN, never PASS.
    assert by_name(result, fa.SNAPSHOT, "1")["status"] == fa.UNKNOWN
    assert by_name(result, fa.RECORDING, "2")["status"] == fa.UNKNOWN
    # Runtime evidence does not need the device.
    assert by_name(result, fa.EVENT_STREAM)["status"] == fa.PASS
    assert by_name(result, fa.SPOOL)["status"] == fa.PASS


def test_unreadable_credential_is_fail():
    driver = hik()
    result, env = run_one(driver, cfg=cfg_for(REC_H, "HIKSERIAL1", credential_error="SecretError"))
    assert by_name(result, fa.RECORDER_AUTH)["status"] == fa.FAIL
    assert env.opened == []


def test_identity_mismatch_never_tests_the_wrong_device():
    driver = hik()
    result, _ = run_one(driver, cfg=cfg_for(REC_H, "SOMEONE-ELSE"))
    assert by_name(result, fa.RECORDER_IDENTITY)["status"] == fa.FAIL
    assert driver.snapshot_calls == [] and driver.clip_calls == []
    assert by_name(result, fa.SNAPSHOT, "1")["last_error"] == "recorder_identity_mismatch"
    assert by_name(result, fa.STORAGE)["status"] == fa.UNKNOWN


def test_identity_without_registry_pin_is_unknown():
    result, _ = run_one(hik(), cfg=cfg_for(REC_H, "x", recorder_identity_fingerprint=None))
    assert by_name(result, fa.RECORDER_IDENTITY)["status"] == fa.UNKNOWN


def test_event_stream_and_native_events_from_the_live_holder():
    driver = hik()
    env = full_env(driver)
    env.holders[REC_H] = live_holder(stream_connected=False, mono=1000.0)
    env.holders[REC_H]["recorder_live_at"] = 500.0           # 500 s ago
    env.holders[REC_H]["live_driver"] = None
    result, _ = run_one(driver, env=env)
    assert by_name(result, fa.EVENT_STREAM)["status"] == fa.FAIL
    native = by_name(result, fa.NATIVE_EVENTS)
    assert native["status"] == fa.FAIL and "503" in native["last_error"]

    env.holders[REC_H]["event_stream"]["connected"] = None   # driver cannot report it
    result, _ = run_one(driver, env=env)
    assert by_name(result, fa.NATIVE_EVENTS)["status"] == fa.UNKNOWN


def test_runtime_evidence_from_runtime_health_when_not_in_process():
    driver = hik()
    env = full_env(driver)
    env.holders = {}
    env.health = {"heartbeat_at": "2026-10-07T09:59:30Z", "recorders": [
        {"recorder_id": REC_H, "live": True, "last_live_at": "2026-10-07T09:59:30Z",
         "event_stream": {"connected": True, "connected_at": "2026-10-07T09:00:00Z",
                          "last_frame_at": "2026-10-07T09:59:58Z"}}]}
    result, _ = run_one(driver, env=env)
    assert by_name(result, fa.EVENT_STREAM)["status"] == fa.PASS
    assert by_name(result, fa.NATIVE_EVENTS)["status"] == fa.PASS
    # No live holder: recovery and the cloud camera list are not observable -> UNKNOWN.
    assert by_name(result, fa.RECOVERY)["status"] == fa.UNKNOWN
    assert by_name(result, fa.CAMERA_INVENTORY)["status"] == fa.UNKNOWN

    env.health["recorders"][0]["live"] = False
    result, _ = run_one(driver, env=env)
    assert by_name(result, fa.EVENT_STREAM)["status"] == fa.FAIL

    env.health = {}
    result, _ = run_one(driver, env=env)
    assert by_name(result, fa.EVENT_STREAM)["status"] == fa.UNKNOWN
    assert by_name(result, fa.NATIVE_EVENTS)["status"] == fa.UNKNOWN


def test_camera_inventory_missing_channel_fails():
    driver = hik(channels=("1",))
    env = full_env(driver)
    env.holders[REC_H] = live_holder(("1", "2"))
    result, _ = run_one(driver, env=env)
    inv = by_name(result, fa.CAMERA_INVENTORY)
    assert inv["status"] == fa.FAIL and inv["evidence"]["missing_channels"] == ["2"]
    assert by_name(result, fa.SNAPSHOT, "2")["status"] == fa.UNKNOWN
    assert by_name(result, fa.SNAPSHOT, "1")["status"] == fa.PASS


@pytest.mark.parametrize("storage,expect", [
    ({"supported": True, "state": "ok"}, fa.PASS),
    ({"supported": True, "state": "fault", "reason": "disk error"}, fa.FAIL),
    ({"supported": True, "state": "degraded"}, fa.FAIL),
    ({"supported": True, "state": None}, fa.UNKNOWN),
    ({"supported": False, "state": None}, fa.UNSUPPORTED),
])
def test_storage_states(storage, expect):
    result, _ = run_one(hik(storage=storage))
    assert by_name(result, fa.STORAGE)["status"] == expect


def test_snapshot_states():
    driver = hik(channels=("1", "2", "3"), video_loss=("2",),
                 snapshot={"3": b"<html>error</html>"})
    env = full_env(driver)
    env.holders[REC_H] = live_holder(("1", "2", "3"))
    result, _ = run_one(driver, env=env)
    assert by_name(result, fa.SNAPSHOT, "1")["status"] == fa.PASS
    loss = by_name(result, fa.SNAPSHOT, "2")
    assert loss["status"] == fa.FAIL and loss["evidence"]["video_loss"] is True
    assert by_name(result, fa.SNAPSHOT, "3")["status"] == fa.FAIL
    # Recording on a video-loss camera is UNKNOWN (video_loss), never searched.
    rec = by_name(result, fa.RECORDING, "2")
    assert rec["status"] == fa.UNKNOWN and rec["last_error"] == "video_loss"

    driver = hik(snapshot={"1": None, "2": b""})
    result, _ = run_one(driver)
    assert by_name(result, fa.SNAPSHOT, "1")["status"] == fa.FAIL
    assert by_name(result, fa.EVIDENCE_STILL)["status"] == fa.FAIL


def test_snapshot_unsupported_driver():
    class NoStills(NvrDriver):
        name = "onvif"

        def __init__(self):
            super().__init__("http://192.0.2.1")
            self.info = DeviceInfo(vendor="X", serial="S1", driver="onvif")
            self.channels = ["1"]

        def list_channels(self):
            return [Channel("1")]

    drv = NoStills()
    env = FakeEnv({REC_H: drv}, holders={REC_H: live_holder(("1",))})
    result = fa.run_suite(env, [target(REC_H, cfg_for(REC_H, "S1"))], include_clip=False)
    assert by_name(result, fa.SNAPSHOT, "1")["status"] == fa.UNSUPPORTED
    assert by_name(result, fa.EVIDENCE_STILL)["status"] == fa.UNSUPPORTED
    # The base driver exposes no searchable archive.
    assert by_name(result, fa.RECORDING, "1")["status"] == fa.UNSUPPORTED
    assert by_name(result, fa.ARCHIVE_SEARCH)["status"] == fa.UNSUPPORTED
    assert by_name(result, fa.STORAGE)["status"] == fa.UNSUPPORTED


def test_recording_states_dahua(archives):
    archives["dahua"].update({"1": "found", "2": "empty", "3": "error"})
    driver = dahua(channels=("1", "2", "3"))
    env = full_env(driver, REC_D)
    env.holders[REC_D] = live_holder(("1", "2", "3"))
    result, _ = run_one(driver, REC_D, env=env)
    assert by_name(result, fa.RECORDING, "1")["status"] == fa.PASS
    empty = by_name(result, fa.RECORDING, "2")
    assert empty["status"] == fa.FAIL and "no recording" in empty["last_error"]
    err = by_name(result, fa.RECORDING, "3")
    assert err["status"] == fa.FAIL and "192.168" not in err["last_error"]
    assert by_name(result, fa.ARCHIVE_SEARCH)["status"] == fa.PASS
    # The 15-minute window.
    _v, _ch, start, end = archives["calls"][0]
    assert end - start == timedelta(minutes=15)


def test_recording_states_hikvision(archives):
    archives["hik"].update({"1": "unsupported", "2": "unsupported"})
    result, _ = run_one(hik())
    assert by_name(result, fa.RECORDING, "1")["status"] == fa.UNSUPPORTED
    assert by_name(result, fa.ARCHIVE_SEARCH)["status"] == fa.UNSUPPORTED
    clip = by_name(result, fa.EVIDENCE_CLIP)
    assert clip["status"] == fa.UNKNOWN          # nothing recorded to export

    archives["hik"].update({"1": "error", "2": "error"})
    result, _ = run_one(hik())
    assert by_name(result, fa.ARCHIVE_SEARCH)["status"] == fa.FAIL


def test_evidence_still_over_the_limit_fails():
    big = b"\xff\xd8\xff" + b"\x00" * (fa.STILL_MAX_BYTES + 10)
    result, _ = run_one(hik(snapshot={"1": big, "2": big}))
    assert by_name(result, fa.EVIDENCE_STILL)["status"] == fa.FAIL


@pytest.mark.parametrize("make_clip,expect", [
    (lambda: b"plain text, not media" * 40, fa.FAIL),
    (lambda: None, fa.UNKNOWN),
    (lambda: b"DHAV" + b"\x00" * (fa.CLIP_MAX_BYTES + 1), fa.FAIL),
    (lambda: hikvision_archive.ClipNoRecording(), fa.UNKNOWN),
    (lambda: hikvision_archive.ClipUnsupported(), fa.UNSUPPORTED),
    (lambda: hikvision_archive.ClipTimedOut(), fa.FAIL),
], ids=["unrecognised", "no-footage", "oversize", "no-recording", "unsupported", "timed-out"])
def test_evidence_clip_states(make_clip, expect):
    result, _ = run_one(hik(clip=make_clip()))
    assert by_name(result, fa.EVIDENCE_CLIP)["status"] == expect


def test_clip_can_be_switched_off():
    driver = dahua()
    result, _ = run_one(driver, REC_D, include_clip=False)
    clip = by_name(result, fa.EVIDENCE_CLIP)
    assert clip["status"] == fa.UNKNOWN and clip["enabled"] is False
    assert driver.clip_calls == []


def test_spool_states():
    for spool, expect in (({"exists": True, "depth": 2, "max_rows": 100, "overflow": True}, fa.FAIL),
                          ({"exists": True, "depth": 95, "max_rows": 100, "overflow": False}, fa.FAIL),
                          ({"exists": False}, fa.UNKNOWN)):
        env = full_env(hik(), spool=spool)
        result, _ = run_one(hik(), env=env)
        assert by_name(result, fa.SPOOL)["status"] == expect, spool
    env = full_env(hik())
    env.holders[REC_H]["upload_degraded"] = "OperationalError"
    result, _ = run_one(hik(), env=env)
    assert by_name(result, fa.SPOOL)["status"] == fa.FAIL


def test_recovery_states():
    result, _ = run_one(hik(), cfg=cfg_for(REC_H, "HIKSERIAL1", recovery_enabled=False))
    c = by_name(result, fa.RECOVERY)
    assert c["status"] == fa.UNSUPPORTED and c["enabled"] is False

    env = full_env(hik())
    env.holders[REC_H]["recovery_waiting_for_inventory"] = True
    assert by_name(run_one(hik(), env=env)[0], fa.RECOVERY)["status"] == fa.UNKNOWN

    env = full_env(hik())
    env.holders[REC_H]["recovery_last_error"] = "NvrUnreachable"
    assert by_name(run_one(hik(), env=env)[0], fa.RECOVERY)["status"] == fa.FAIL

    env = full_env(hik())
    env.holders[REC_H]["recovery_cycle_at"] = env.mono - 5000
    assert by_name(run_one(hik(), env=env)[0], fa.RECOVERY)["status"] == fa.FAIL

    env = full_env(hik())
    env.holders[REC_H].pop("recovery_cycle_at")
    assert by_name(run_one(hik(), env=env)[0], fa.RECOVERY)["status"] == fa.UNKNOWN


# ---------------------------------------------------------------------------
# multi-recorder, budget, size bound, subsets
# ---------------------------------------------------------------------------

def test_multi_recorder_sections_use_each_recorders_own_config():
    h, d = hik(), dahua(channels=("1", "2", "3"))
    text, pub = signed_manifest()
    env = FakeEnv({REC_H: h, REC_D: d},
                  holders={REC_H: live_holder(("1", "2")), REC_D: live_holder(("1", "2", "3"))},
                  manifest=text)
    env.base.update_public_key = pub
    cfg_h, cfg_d = cfg_for(REC_H, "HIKSERIAL1"), cfg_for(REC_D, "8E06857PAZ7EB3A")
    result = fa.run_suite(env, [fa.Target(REC_H, "Front", cfg_h, primary=True),
                                fa.Target(REC_D, "Back", cfg_d, primary=False)])
    assert {id(c) for c in env.opened} == {id(cfg_h), id(cfg_d)}
    assert [r["recorder_id"] for r in result["recorders"]] == [REC_H, REC_D]
    assert result["recorders"][1]["hardware"]["vendor"] == "Dahua"
    assert result["summary"]["hardware"]["vendor"] == "Hikvision"   # the primary
    assert result["summary"]["recorders"] == 2
    assert result["summary"]["total"] == 6 + (11 + 4) + (11 + 6)
    assert result["summary"]["passed"] == result["summary"]["total"]
    # Each camera check names its own recorder; Dahua channel 3 exists only on Back.
    assert by_name(result, fa.SNAPSHOT, "3")["recorder_id"] == REC_D
    assert d.snapshot_calls and h.snapshot_calls
    lines = fa.format_table(result)
    assert any(line.startswith("Back: Snapshot ch3") for line in lines)
    assert any(line.startswith("Front: Recorder auth") for line in lines)


def test_one_broken_recorder_never_sinks_the_other():
    h = hik()
    env = full_env(h)
    env.drivers[REC_D] = DriverError("connect timeout to http://10.0.0.9")
    env.holders[REC_D] = live_holder()
    result = fa.run_suite(env, [fa.Target(REC_H, "Front", cfg_for(REC_H, "HIKSERIAL1"), True),
                                fa.Target(REC_D, "Back", cfg_for(REC_D, "D"), False)])
    assert by_name(result, fa.RECORDER_AUTH, recorder_id=REC_D)["status"] == fa.FAIL
    assert by_name(result, fa.RECORDER_AUTH, recorder_id=REC_H)["status"] == fa.PASS
    assert by_name(result, fa.SNAPSHOT, "1", recorder_id=REC_H)["status"] == fa.PASS
    assert "10.0.0.9" not in json.dumps(result)


def test_time_budget_is_enforced_and_unfinished_checks_are_unknown():
    driver = hik(channels=tuple(str(i) for i in range(1, 9)), snapshot_delay=1.5)
    env = full_env(driver)
    env.holders[REC_H] = live_holder(tuple(str(i) for i in range(1, 9)))
    started = time.monotonic()
    result = fa.run_suite(env, [target(REC_H, cfg_for(REC_H, "HIKSERIAL1"))],
                          budget_seconds=4, include_clip=False)
    elapsed = time.monotonic() - started
    assert elapsed < 4 + fa.SECTION_JOIN_GRACE_SECONDS + 1.0, elapsed
    snaps = [c for c in result["checks"] if c["name"] == fa.SNAPSHOT]
    assert len(snaps) == 8
    late = [c for c in snaps if c["status"] == fa.UNKNOWN]
    assert late, "an over-budget run must leave later cameras UNKNOWN"
    assert all(c["last_error"] in ("time_budget_exhausted",) or "timed out" in c["last_error"]
               for c in late)
    assert result["budget_seconds"] == 4


def test_budget_is_capped_at_180_seconds():
    result = fa.run_suite(full_env(hik()), [], budget_seconds=10_000, only={fa.AGENT})
    assert result["budget_seconds"] == fa.MAX_BUDGET_SECONDS


def test_a_wedged_recorder_is_abandoned_within_the_check_timeout(monkeypatch):
    monkeypatch.setitem(fa.TIMEOUTS, "recorder_open", 1.0)
    env = full_env(hik(), open_delay=5.0)
    started = time.monotonic()
    result, _ = run_one(hik(), env=env, include_clip=False)
    assert time.monotonic() - started < 4.0
    auth = by_name(result, fa.RECORDER_AUTH)
    assert auth["status"] == fa.UNKNOWN and "timed out" in auth["last_error"]
    assert by_name(result, fa.STORAGE)["last_error"] == "recorder_not_open"


def test_result_size_is_bounded():
    channels = tuple(str(i) for i in range(1, 81))
    driver = hik(channels=channels)
    env = full_env(driver)
    env.holders[REC_H] = live_holder(channels)
    result, _ = run_one(driver, env=env, include_clip=False)
    snaps = [c for c in result["checks"] if c["name"] == fa.SNAPSHOT]
    assert len(snaps) == fa.MAX_CAMERAS_PER_RECORDER
    assert by_name(result, fa.CAMERA_INVENTORY)["evidence"]["cameras_not_tested"] == 16
    assert len(json.dumps(result).encode()) <= fa.MAX_RESULT_BYTES

    huge = {"summary": {"total": 1}, "checks": [
        fa.make_check("Snapshot", "camera", fa.PASS, channel=str(i),
                      evidence={"blob": "x" * 5000}) for i in range(200)]}
    bounded = fa.bound_result(huge)
    assert bounded["truncated"] is True
    assert len(json.dumps(bounded).encode()) <= fa.MAX_RESULT_BYTES


def test_recording_check_subset():
    driver = dahua()
    result, _ = run_one(driver, REC_D, only=fa.RECORDING_CHECK_ONLY, include_clip=False)
    assert {c["name"] for c in result["checks"]} == {fa.RECORDER_AUTH, fa.RECORDER_IDENTITY,
                                                      fa.RECORDING}
    assert driver.snapshot_calls == []


def test_archive_check_subset_on_one_channel(archives):
    driver = hik(channels=("1", "2", "3"))
    env = full_env(driver)
    env.holders[REC_H] = live_holder(("1", "2", "3"))
    result, _ = run_one(driver, env=env, only=fa.ARCHIVE_CHECK_ONLY, channels=["2"],
                        include_clip=False)
    names = [c["name"] for c in result["checks"]]
    assert set(names) == {fa.RECORDER_AUTH, fa.RECORDER_IDENTITY, fa.RECORDING,
                          fa.ARCHIVE_SEARCH, fa.EVIDENCE_CLIP}
    assert [c["channel"] for c in result["checks"] if c["name"] == fa.RECORDING] == ["2"]
    assert [c[1] for c in archives["calls"]] == ["2"]


def test_unknown_is_never_counted_as_pass():
    env = full_env(hik(), health={}, analytics={}, command_id=None)
    env.holders = {}
    result, _ = run_one(hik(), env=env)
    s = result["summary"]
    assert s["passed"] + s["failed"] + s["unknown"] + s["unsupported"] == s["total"]
    assert s["unknown"] >= 4
    assert fa.format_table(result)[-4].startswith(f"{s['passed']}/{s['total']} PASS (")


def test_jpeg_dimensions_and_containers():
    assert fa.jpeg_dimensions(jpeg(320, 240)) == (320, 240)
    assert fa.jpeg_dimensions(b"not a jpeg") is None
    assert fa.clip_container(b"\x00\x00\x00\x18ftypmp42") == "mp4"
    assert fa.clip_container(b"DHAV....") == "dav"
    assert fa.clip_container(b"IMKH....") == "hikvision-ps"
    assert fa.clip_container(b"\x00\x00\x01\xba....") == "mpeg-ps"
    assert fa.clip_container(b"hello") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
