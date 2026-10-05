#!/usr/bin/env python3
"""Archive-scan handlers follow the current analytics camera config (MNVR-038).

The handlers read analytics_config.json once, when the archive thread started. On a fresh
install that file does not exist yet, and cameras added later were never seen, so every
archive scan failed with "not present in local config" until the Agent restarted. The
handlers now reload the camera config whenever its version changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import analytics_agent as aa  # noqa: E402
import recovery_ai  # noqa: E402

A = "aaaaaaaa-0000-4000-8000-000000000001"
FROM = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
TO = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


class Archive:
    def __init__(self, log):
        self.log = log

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=100):
        self.log.append(channel)
        return {"status": "supported", "next_cursor": None,
                "events": [{"segment": {"start": "2026-10-05T08:10:00Z"}}]}

    def close(self):
        pass


def _write(path: Path, version: int, cameras):
    path.write_text(json.dumps({"version": version, "config": {"cameras": cameras}}),
                    encoding="utf-8")


@pytest.fixture
def handlers(tmp_path, monkeypatch):
    channels, logs = [], []
    cfg = SimpleNamespace(analytics_config_path=tmp_path / "analytics_config.json",
                          recovery_ai_max_frames=2)
    monkeypatch.setattr(aa.core, "log", logs.append)
    monkeypatch.setattr(aa.recorder_runtime, "config_for_cloud_recorder", lambda c, _r: c)
    monkeypatch.setattr(aa.core, "open_archive_driver", lambda _c: (Archive(channels), None))
    monkeypatch.setattr(recovery_ai, "recovered_frame", lambda *_a, **_k: b"frame")
    retrieve, analyze = aa._archive_scan_handlers(cfg, None, threading.Event())
    return cfg, retrieve, analyze, channels, logs


def test_a_camera_configured_after_the_thread_started_is_scanned(handlers):
    cfg, retrieve, _analyze, channels, logs = handlers
    assert not cfg.analytics_config_path.exists()          # fresh install
    _write(cfg.analytics_config_path, 1,
           [{"id": "cam-1", "recorder_id": A, "channel": "2", "rules": []}])

    frames = retrieve("cam-1", FROM, TO)

    assert frames and frames[0][0] == b"frame"
    assert channels == ["2"]
    assert not any("not present" in line for line in logs)


def test_a_changed_camera_config_version_is_picked_up(handlers):
    cfg, retrieve, _analyze, channels, _logs = handlers
    _write(cfg.analytics_config_path, 1,
           [{"id": "cam-1", "recorder_id": A, "channel": "2", "rules": []}])
    assert retrieve("cam-1", FROM, TO)
    _write(cfg.analytics_config_path, 2,
           [{"id": "cam-1", "recorder_id": A, "channel": "3", "rules": []},
            {"id": "cam-2", "recorder_id": A, "channel": "4", "rules": []}])

    assert retrieve("cam-1", FROM, TO)
    assert retrieve("cam-2", FROM, TO)
    assert channels == ["2", "3", "4"]


def test_a_camera_that_is_really_absent_is_still_refused(handlers):
    cfg, retrieve, _analyze, channels, logs = handlers
    _write(cfg.analytics_config_path, 1,
           [{"id": "cam-1", "recorder_id": A, "channel": "2", "rules": []}])
    assert retrieve("cam-9", FROM, TO) is None
    assert channels == []
    assert any("not present" in line for line in logs)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
