"""Multi-recorder camera/job runtime routing contracts."""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import credential_store as cs  # noqa: E402
import incident_evidence  # noqa: E402
import recorder_registry as rr  # noqa: E402
import recorder_runtime  # noqa: E402
import site_control  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402


def _cfg(name, cloud_id, url):
    return SimpleNamespace(
        name=name,
        recorder_cloud_id=cloud_id,
        recorder_local_id=f"local-{name}",
        nvr_driver="onvif",
        nvr_url=url,
        nvr_username=f"user-{name}",
        nvr_password=f"pw-{name}",
        site_control_enabled=True,
        site_control_seconds=0.01,
        supabase_url="https://example.invalid",
        publishable_key="test",
        analytics_enabled=True,
        analytics_config_path=Path("/tmp/unused-analytics.json"),
        recovery_ai_max_frames=4,
    )


def _isolated_registry(monkeypatch, tmp_path, rows):
    """A real recorders.json + per-recorder credentials under a temp ProgramData."""
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))

    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(text[5:])

    monkeypatch.setattr(cs, "write_json_secret", wjs)
    monkeypatch.setattr(cs, "read_json_secret", rjs)
    for row in rows:
        cs.save_recorder_credential(row["local_id"], f"user-{row['display_name']}",
                                    f"pw-{row['display_name']}")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": rows})


def test_cloud_recorder_resolver_is_exact_and_legacy_fails_closed(monkeypatch, tmp_path):
    cloud_a = "11111111-1111-1111-1111-111111111111"
    cloud_b = "22222222-2222-2222-2222-222222222222"
    local_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    local_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    _isolated_registry(monkeypatch, tmp_path, [
        {"local_id": local_a, "cloud_recorder_id": cloud_a, "display_name": "a",
         "url": "http://a", "driver": "onvif", "is_primary": True, "is_configured": True},
        {"local_id": local_b, "cloud_recorder_id": cloud_b, "display_name": "b",
         "url": "http://b", "driver": "onvif", "is_primary": False, "is_configured": True},
    ])
    # The base config is the unbound process config: no hand-set recorder id.
    base = _cfg("base", None, "http://base")
    base.state_path = tmp_path / "WatchLog" / "agent_state.json"
    base.spool_path = tmp_path / "WatchLog" / "spool.sqlite"
    base.health_store_path = tmp_path / "WatchLog" / "health.sqlite"
    base.last_live_path = tmp_path / "WatchLog" / "last_live.json"

    cfg_a = recorder_runtime.config_for_cloud_recorder(base, cloud_a)
    cfg_b = recorder_runtime.config_for_cloud_recorder(base, cloud_b)
    assert (cfg_a.nvr_url, cfg_a.nvr_username, cfg_a.recorder_cloud_id) == (
        "http://a", "user-a", cloud_a)
    assert (cfg_b.nvr_url, cfg_b.nvr_username, cfg_b.recorder_cloud_id) == (
        "http://b", "user-b", cloud_b)

    with pytest.raises(ValueError, match="not mapped"):
        recorder_runtime.config_for_cloud_recorder(
            base, "33333333-3333-3333-3333-333333333333"
        )
    with pytest.raises(ValueError, match="recorder_id required"):
        recorder_runtime.config_for_cloud_recorder(base, None)

    # A config already bound to its recorder resolves its own jobs without
    # touching the registry or decrypting again.
    monkeypatch.setattr(
        rr, "recorders",
        lambda: (_ for _ in ()).throw(AssertionError("should not load registry")),
    )
    assert recorder_runtime.config_for_cloud_recorder(cfg_a, cloud_a) is cfg_a


class _Driver:
    def __init__(self, name, snapshot=b"jpeg", clip_error=None):
        self.name = name
        self.snapshot = snapshot
        self.clip_error = clip_error
        self.closed = False

    def get_snapshot(self, channel):
        return self.snapshot + str(channel).encode()

    def get_clip(self, channel, start, end):
        if self.clip_error:
            raise self.clip_error
        # An MP4 container header: clips are labelled by their container, not the driver.
        return b"\x00\x00\x00\x18ftypmp42clip"

    def close(self):
        self.closed = True


def test_config_snapshot_uses_requested_secondary_without_opening_primary(monkeypatch):
    base = _cfg("base", None, "http://a")
    cfg_b = _cfg("b", "22222222-2222-2222-2222-222222222222", "http://b")
    opened = []

    monkeypatch.setattr(
        recorder_runtime,
        "config_for_cloud_recorder",
        lambda _cfg, rid: cfg_b if rid == cfg_b.recorder_cloud_id else base,
    )
    monkeypatch.setattr(
        analytics_agent,
        "_open_analytics_driver",
        lambda cfg: opened.append(cfg) or _Driver(cfg.name),
    )

    calls = []

    class Cloud:
        def call(self, name, **kwargs):
            calls.append((name, kwargs))
            return {"ok": True}

    completed = analytics_agent._service_snapshot_requests(
        Cloud(),
        {"agent_id": "a", "agent_key": "k"},
        base,
        None,  # crucial: no primary driver is pre-opened
        [{
            "request_id": "req-b",
            "camera_id": "cam-b",
            "recorder_id": cfg_b.recorder_cloud_id,
            "channel": "1",
        }],
    )

    assert completed == 1
    assert opened == [cfg_b]
    upload = [x for x in calls if x[0] == "wl_upload_config_snapshot"]
    assert len(upload) == 1 and upload[0][1]["p_camera_id"] == "cam-b"


def test_site_control_builds_driver_from_claimed_recorder(monkeypatch):
    base = _cfg("base", None, "http://base")
    cfg_b = _cfg("b", "22222222-2222-2222-2222-222222222222", "http://b")
    stop = threading.Event()
    built = []
    completed = []

    monkeypatch.setattr(
        recorder_runtime,
        "config_for_cloud_recorder",
        lambda _cfg, rid: cfg_b if rid == cfg_b.recorder_cloud_id else (_ for _ in ()).throw(
            AssertionError("wrong recorder target")
        ),
    )

    class Driver:
        def close(self):
            pass

    monkeypatch.setattr(
        core,
        "build",
        lambda driver, url, username, password: (
            built.append((driver, url, username, password)) or Driver()
        ),
    )
    monkeypatch.setattr(
        site_control,
        "execute_read",
        lambda _driver, action, params: {
            "action": action, "ok": True, "data": {"target": params.get("channel")}
        },
    )

    class Cloud:
        def call(self, name, **kwargs):
            if name == "wl_agent_claim_command":
                return {
                    "enabled": True,
                    "authority": True,
                    "command": {
                        "id": "cmd-1",
                        "recorder_id": cfg_b.recorder_cloud_id,
                        "action": "get_channels",
                        "params": {},
                        "tier": "read",
                    },
                }
            if name == "wl_agent_complete_command":
                completed.append(kwargs)
                stop.set()
                return {"ok": True}
            raise AssertionError(name)

    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    core.command_worker(
        base,
        {"agent_id": "agent", "agent_key": "key"},
        Cloud(),
        stop,
    )

    assert built == [("onvif", "http://b", "user-b", "pw-b")]
    assert completed and completed[0]["p_status"] == "succeeded"


def test_incident_clip_worker_opens_claimed_recorder(monkeypatch):
    base = _cfg("base", None, "http://base")
    cfg_b = _cfg("b", "22222222-2222-2222-2222-222222222222", "http://b")
    stop = threading.Event()
    opened = []

    monkeypatch.setattr(
        recorder_runtime,
        "config_for_cloud_recorder",
        lambda _cfg, rid: cfg_b if rid == cfg_b.recorder_cloud_id else (_ for _ in ()).throw(
            AssertionError("wrong recorder target")
        ),
    )

    def open_archive(cfg):
        opened.append(cfg)
        return _Driver("archive-b", clip_error=RuntimeError("probe failure")), SimpleNamespace(
            vendor="Test", model="B"
        )

    monkeypatch.setattr(core, "open_archive_driver", open_archive)

    class Cloud:
        def __init__(self, *_a, **_k):
            self.claimed = False

        def call(self, name, **kwargs):
            if name == "wl_agent_claim_clip_requests":
                if not self.claimed:
                    self.claimed = True
                    return [{
                        "request_id": "req-b",
                        "camera_id": "cam-b",
                        "recorder_id": cfg_b.recorder_cloud_id,
                        "channel": "1",
                        "start_at": "2026-10-02T12:00:00Z",
                        "end_at": "2026-10-02T12:01:00Z",
                    }]
                stop.set()
                return []
            if name == "wl_agent_fail_clip":
                stop.set()
                return {"ok": True}
            raise AssertionError(name)

    monkeypatch.setattr(core, "Cloud", Cloud)
    incident_evidence.footage_worker(
        base, {"agent_id": "agent", "agent_key": "key"}, stop
    )
    assert opened == [cfg_b]


def test_incident_still_worker_opens_claimed_recorder(monkeypatch):
    base = _cfg("base", None, "http://base")
    cfg_b = _cfg("b", "22222222-2222-2222-2222-222222222222", "http://b")
    stop = threading.Event()
    opened = []

    monkeypatch.setattr(
        recorder_runtime,
        "config_for_cloud_recorder",
        lambda _cfg, rid: cfg_b if rid == cfg_b.recorder_cloud_id else (_ for _ in ()).throw(
            AssertionError("wrong recorder target")
        ),
    )

    class BrokenDriver(_Driver):
        def get_snapshot(self, channel):
            raise RuntimeError("camera unavailable")

    def open_driver(cfg):
        opened.append(cfg)
        return BrokenDriver("still-b"), SimpleNamespace(vendor="Test", model="B")

    monkeypatch.setattr(core, "open_driver", open_driver)

    class Cloud:
        def __init__(self, *_a, **_k):
            self.claimed = False

        def call(self, name, **kwargs):
            if name == "wl_agent_claim_incident_stills":
                if not self.claimed:
                    self.claimed = True
                    return [{
                        "request_id": "still-b",
                        "camera_id": "cam-b",
                        "recorder_id": cfg_b.recorder_cloud_id,
                        "channel": "1",
                        "occurred_at": "2026-10-02T12:00:00Z",
                    }]
                stop.set()
                return []
            if name == "wl_agent_fail_incident_still":
                stop.set()
                return {"ok": True}
            raise AssertionError(name)

    monkeypatch.setattr(core, "Cloud", Cloud)
    incident_evidence.stills_worker(
        base, {"agent_id": "agent", "agent_key": "key"}, stop
    )
    assert opened == [cfg_b]


def test_archive_retrieval_resolves_camera_recorder_and_filters_engine(monkeypatch):
    base = _cfg("base", None, "http://base")
    base.recovery_ai_max_frames = 2
    cfg_a = _cfg("a", "11111111-1111-1111-1111-111111111111", "http://a")
    cfg_b = _cfg("b", "22222222-2222-2222-2222-222222222222", "http://b")
    opened = []

    config = {
        "cameras": [
            {"id": "cam-a", "recorder_id": cfg_a.recorder_cloud_id, "channel": "1", "rules": []},
            {"id": "cam-b", "recorder_id": cfg_b.recorder_cloud_id, "channel": "1", "rules": []},
        ]
    }
    monkeypatch.setattr(analytics_agent.analytics, "load_config", lambda _p: {"config": config})
    monkeypatch.setattr(
        recorder_runtime,
        "config_for_cloud_recorder",
        lambda _cfg, rid: cfg_a if rid == cfg_a.recorder_cloud_id else cfg_b,
    )

    class ArchiveDriver:
        name = "fake-archive"
        def enumerate_historical_events(self, channel, start, end, cursor=None, limit=100):
            return {
                "status": "supported",
                "events": [{"segment": {"start": "2026-10-02T12:00:00Z"}}],
                "next_cursor": None,
            }
        def close(self):
            pass

    def open_archive(cfg):
        opened.append(cfg)
        return ArchiveDriver(), SimpleNamespace(vendor="Test", model=cfg.name)

    monkeypatch.setattr(core, "open_archive_driver", open_archive)

    import recovery_ai
    monkeypatch.setattr(recovery_ai, "recovered_frame", lambda *_a, **_k: b"jpeg")

    retrieve, _analyze = analytics_agent._archive_scan_handlers(
        base, detector=None, stop=threading.Event()
    )
    rows = retrieve(
        "cam-b",
        datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc),
    )

    assert opened == [cfg_b]
    assert rows and rows[0][0] == b"jpeg"


def test_corrupt_sibling_credential_does_not_fail_a_healthy_recorder_clip(monkeypatch, tmp_path):
    cloud_a = "11111111-1111-1111-1111-111111111111"
    cloud_b = "22222222-2222-2222-2222-222222222222"
    local_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    local_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    _isolated_registry(monkeypatch, tmp_path, [
        {"local_id": local_a, "cloud_recorder_id": cloud_a, "display_name": "a",
         "url": "http://a", "driver": "onvif", "is_primary": True, "is_configured": True},
        {"local_id": local_b, "cloud_recorder_id": cloud_b, "display_name": "b",
         "url": "http://b", "driver": "onvif", "is_primary": False, "is_configured": True},
    ])
    cs.recorder_credential_path(local_b).write_text("CORRUPT", encoding="utf-8")
    base = _cfg("base", None, "http://base")
    base.state_path = tmp_path / "WatchLog" / "agent_state.json"
    base.spool_path = tmp_path / "WatchLog" / "spool.sqlite"
    base.health_store_path = tmp_path / "WatchLog" / "health.sqlite"
    base.last_live_path = tmp_path / "WatchLog" / "last_live.json"
    stop = threading.Event()
    opened, outcomes = [], []

    def open_archive(cfg):
        opened.append(cfg.nvr_url)
        return _Driver("archive-a"), SimpleNamespace(vendor="Test", model="A")

    monkeypatch.setattr(core, "open_archive_driver", open_archive)

    class Cloud:
        def __init__(self, *_a, **_k):
            self.queue = [
                {"request_id": "req-a", "recorder_id": cloud_a, "channel": "1",
                 "start_at": "2026-10-02T12:00:00Z", "end_at": "2026-10-02T12:00:30Z"},
                {"request_id": "req-b", "recorder_id": cloud_b, "channel": "1",
                 "start_at": "2026-10-02T12:00:00Z", "end_at": "2026-10-02T12:00:30Z"},
            ]

        def call(self, name, **kwargs):
            if name == "wl_agent_claim_clip_requests":
                if self.queue:
                    return [self.queue.pop(0)]
                stop.set()
                return []
            if name in ("wl_agent_complete_clip", "wl_agent_fail_clip"):
                outcomes.append((kwargs["p_request_id"], name))
            return {"ok": True}

    monkeypatch.setattr(core, "Cloud", Cloud)
    incident_evidence.footage_worker(base, {"agent_id": "agent", "agent_key": "key"}, stop)

    assert opened == ["http://a"]
    # Each recorder's clips are fetched by its own worker (MNVR-034): no order between them.
    assert sorted(outcomes) == [("req-a", "wl_agent_complete_clip"),
                                ("req-b", "wl_agent_fail_clip")]


# --- the registry disappears while a multi-recorder Agent runs -----------------------

def _two_bound_rows_then_registry_gone(monkeypatch, tmp_path):
    """Two bound rows; the Agent starts with them, then Setup quarantines recorders.json."""
    cloud_a = "11111111-1111-1111-1111-111111111111"
    cloud_b = "22222222-2222-2222-2222-222222222222"
    _isolated_registry(monkeypatch, tmp_path, [
        {"local_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "cloud_recorder_id": cloud_a,
         "display_name": "a", "url": "http://a", "driver": "onvif",
         "is_primary": True, "is_configured": True},
        {"local_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cloud_recorder_id": cloud_b,
         "display_name": "b", "url": "http://b", "driver": "onvif",
         "is_primary": False, "is_configured": True},
    ])
    # main()'s base Config: the legacy ini recorder and the legacy (continuity) login.
    base = _cfg("legacy", None, "http://legacy")
    base.state_path = tmp_path / "WatchLog" / "agent_state.json"
    base.spool_path = tmp_path / "WatchLog" / "spool.sqlite"
    base.health_store_path = tmp_path / "WatchLog" / "health.sqlite"
    base.last_live_path = tmp_path / "WatchLog" / "last_live.json"
    assert recorder_runtime.mark_registry_required(base) is True
    rr.quarantine_registry()
    assert not rr.registry_path().exists()
    return base, cloud_b


def test_registry_gone_job_for_a_named_recorder_fails_closed(monkeypatch, tmp_path):
    base, cloud_b = _two_bound_rows_then_registry_gone(monkeypatch, tmp_path)
    with pytest.raises(recorder_runtime.RecorderRegistryUnavailable):
        recorder_runtime.config_for_cloud_recorder(base, cloud_b)
    with pytest.raises(recorder_runtime.RecorderRegistryUnavailable):
        recorder_runtime.config_for_cloud_recorder(base, None)


def test_registry_gone_still_clip_and_site_control_open_no_driver(monkeypatch, tmp_path):
    base, cloud_b = _two_bound_rows_then_registry_gone(monkeypatch, tmp_path)
    opened, calls = [], []

    def never(*_a, **_k):
        opened.append(_a)
        raise AssertionError("no recorder may be opened without the registry")

    for name in ("open_driver", "open_archive_driver", "autodetect", "build"):
        monkeypatch.setattr(core, name, never)

    class Cloud:
        def call(self, name, **kwargs):
            calls.append((name, kwargs))
            return {"ok": True}

    state = {"agent_id": "agent", "agent_key": "key"}
    incident_evidence._serve_still_request(
        Cloud(), state, base, incident_evidence._StillRecorderGate(),
        {"request_id": "still-b", "recorder_id": cloud_b, "channel": "1"})
    incident_evidence._serve_clip_request(
        Cloud(), state, base, (),
        {"request_id": "req-b", "recorder_id": cloud_b, "channel": "1",
         "start_at": "2026-10-02T12:00:00Z", "end_at": "2026-10-02T12:01:00Z"})
    core._run_claimed_command(
        base, state, Cloud(),
        {"id": "cmd-b", "recorder_id": cloud_b, "action": "get_channels", "params": {}},
        site_control)

    assert opened == []
    outcomes = [(name, kw.get("p_status")) for name, kw in calls]
    assert outcomes == [("wl_agent_fail_incident_still", None),
                        ("wl_agent_fail_clip", None),
                        ("wl_agent_complete_command", "failed")]
    assert calls[1][1]["p_reason"] == incident_evidence.RECORDER_UNAVAILABLE
    # Nothing about the legacy recorder or its login leaks into any outcome.
    assert "legacy" not in json.dumps([kw for _n, kw in calls])


@pytest.mark.parametrize("worker,claim", [
    (incident_evidence.footage_worker, "wl_agent_claim_clip_requests"),
    (incident_evidence.stills_worker, "wl_agent_claim_incident_stills"),
])
def test_evidence_workers_do_not_claim_while_the_registry_is_gone(
        monkeypatch, tmp_path, worker, claim):
    base, _cloud_b = _two_bound_rows_then_registry_gone(monkeypatch, tmp_path)
    stop = threading.Event()
    claims, waits = [], []

    class Cloud:
        def __init__(self, *_a, **_k):
            pass

        def call(self, name, **kwargs):
            claims.append(name)
            return []

    real_wait = stop.wait

    def wait(seconds=None):
        waits.append(seconds)
        if len(waits) >= 3:
            stop.set()
        return real_wait(0)

    monkeypatch.setattr(stop, "wait", wait)
    monkeypatch.setattr(core, "Cloud", Cloud)
    worker(base, {"agent_id": "agent", "agent_key": "key"}, stop)
    assert claims == []
    assert len(waits) >= 3


def test_registry_required_only_when_configured_rows_or_an_unreadable_registry(
        monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    base = _cfg("legacy", None, "http://legacy")
    # No registry: the 5.0.x singleton runtime, unchanged.
    assert recorder_runtime.mark_registry_required(base) is False
    assert recorder_runtime.config_for_cloud_recorder(base, "x") is base
    # An unreadable registry is required: nothing runs on the legacy recorder meanwhile.
    rr.registry_path().parent.mkdir(parents=True, exist_ok=True)
    rr.registry_path().write_text("{not json", encoding="utf-8")
    assert recorder_runtime.mark_registry_required(base) is True
    assert recorder_runtime.registry_unavailable(base) is True


# --- Site Control verifies the recorder's identity before it runs a command --------------

@pytest.mark.parametrize("driver_name", ["auto", "dahua-cgi"])
def test_site_control_refuses_a_different_recorder_at_the_saved_address(
        monkeypatch, driver_name):
    cfg_a = _cfg("a", "11111111-1111-1111-1111-111111111111", "http://192.0.2.10")
    cfg_a.nvr_driver = driver_name
    cfg_a.recorder_identity_fingerprint = "serial:AAA111"
    monkeypatch.setattr(recorder_runtime, "config_for_cloud_recorder", lambda _c, _r: cfg_a)
    drivers, executed, completed = [], [], []

    class Driver:
        closed = False

        def probe(self):
            return SimpleNamespace(serial="BBB222", vendor="Dahua", model="X")

        def close(self):
            self.closed = True

    def make(*_a, **_k):
        drivers.append(Driver())
        return drivers[-1]

    monkeypatch.setattr(core, "build", make)
    monkeypatch.setattr(core, "autodetect", lambda *_a, **_k: (make(), make().probe()))
    for name in ("execute_read", "execute_write"):
        monkeypatch.setattr(site_control, name,
                            lambda *_a, **_k: executed.append(_a) or {"ok": True})

    class Cloud:
        def call(self, name, **kwargs):
            completed.append(kwargs)
            return {"ok": True}

    core._run_claimed_command(
        cfg_a, {"agent_id": "agent", "agent_key": "key"}, Cloud(),
        {"id": "cmd-a", "recorder_id": cfg_a.recorder_cloud_id, "action": "get_channels",
         "params": {}}, site_control)

    assert executed == [], "a command for recorder A never runs on another device"
    assert completed[0]["p_status"] == "failed"
    assert "serial number" in completed[0]["p_error"]
    assert "192.0.2.10" not in completed[0]["p_error"] and "BBB222" not in completed[0]["p_error"]
    assert drivers and all(d.closed for d in drivers[:1])


def test_site_control_runs_on_the_saved_recorder_and_skips_the_probe_without_a_serial(
        monkeypatch):
    cfg_a = _cfg("a", "11111111-1111-1111-1111-111111111111", "http://a")
    cfg_a.nvr_driver = "dahua-cgi"
    monkeypatch.setattr(recorder_runtime, "config_for_cloud_recorder", lambda _c, _r: cfg_a)
    probes, completed = [], []

    class Driver:
        def probe(self):
            probes.append(1)
            return SimpleNamespace(serial="AAA111")

        def close(self):
            pass

    monkeypatch.setattr(core, "build", lambda *_a, **_k: Driver())
    monkeypatch.setattr(site_control, "execute_read",
                        lambda *_a, **_k: {"ok": True, "data": {}})

    class Cloud:
        def call(self, name, **kwargs):
            completed.append(kwargs)
            return {"ok": True}

    state = {"agent_id": "agent", "agent_key": "key"}
    cmd = {"id": "cmd-a", "recorder_id": cfg_a.recorder_cloud_id, "action": "get_channels",
           "params": {}}
    # Unknown stays unknown: no saved serial, no extra probe, nothing refused.
    core._run_claimed_command(cfg_a, state, Cloud(), cmd, site_control)
    assert probes == [] and completed[-1]["p_status"] == "succeeded"
    # A saved serial that matches: verified, then run.
    cfg_a.recorder_identity_fingerprint = "serial:aaa111"
    core._run_claimed_command(cfg_a, state, Cloud(), cmd, site_control)
    assert probes == [1] and completed[-1]["p_status"] == "succeeded"
