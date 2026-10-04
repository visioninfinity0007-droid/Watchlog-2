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
import incident_evidence  # noqa: E402
import recorder_runtime  # noqa: E402
import site_control  # noqa: E402
import watchlog_agent as core  # noqa: E402


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


def test_cloud_recorder_resolver_is_exact_and_legacy_fails_closed(monkeypatch):
    base = _cfg("base", None, "http://base")
    cfg_a = _cfg("a", "11111111-1111-1111-1111-111111111111", "http://a")
    cfg_b = _cfg("b", "22222222-2222-2222-2222-222222222222", "http://b")
    contexts = [
        SimpleNamespace(cloud_recorder_id=cfg_a.recorder_cloud_id, config=cfg_a),
        SimpleNamespace(cloud_recorder_id=cfg_b.recorder_cloud_id, config=cfg_b),
    ]
    monkeypatch.setattr(recorder_runtime, "load_contexts", lambda _cfg: contexts)

    assert recorder_runtime.config_for_cloud_recorder(base, cfg_a.recorder_cloud_id) is cfg_a
    assert recorder_runtime.config_for_cloud_recorder(base, cfg_b.recorder_cloud_id) is cfg_b

    with pytest.raises(ValueError, match="not mapped"):
        recorder_runtime.config_for_cloud_recorder(
            base, "33333333-3333-3333-3333-333333333333"
        )
    with pytest.raises(ValueError, match="recorder_id required"):
        recorder_runtime.config_for_cloud_recorder(base, None)

    # A cut-over singleton config can resolve its own recorder without touching
    # registry/decryption again.
    explicit = _cfg("explicit", cfg_a.recorder_cloud_id, "http://explicit")
    monkeypatch.setattr(
        recorder_runtime, "load_contexts",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("should not load registry")),
    )
    assert recorder_runtime.config_for_cloud_recorder(
        explicit, cfg_a.recorder_cloud_id
    ) is explicit


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
        return b"clip"

    def close(self):
        self.closed = True


def test_config_snapshot_uses_requested_secondary_without_opening_primary(monkeypatch):
    base = _cfg("base", "11111111-1111-1111-1111-111111111111", "http://a")
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
