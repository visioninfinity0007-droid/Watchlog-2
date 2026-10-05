"""Recorder-aware analytics mux contracts."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import recorder_analytics  # noqa: E402


class FakeEngine:
    def __init__(self, log=print):
        self.config = {}
        self.cameras = {}

    def configure(self, config):
        self.config = config or {}
        self.cameras = {
            str(c["channel"]): c for c in (self.config.get("cameras") or [])
        }

    def sample_plan(self):
        return [
            (str(c["channel"]), 2.0)
            for c in self.config.get("cameras") or []
            if c.get("rules")
        ]

    def process(self, channel, detections, frame_size, when=None):
        camera = self.cameras[str(channel)]
        rule = camera["rules"][0]
        return [{
            "rule_id": str(rule["id"]),
            "channel": str(channel),
            "event_type": "measurement",
            "occurred_at": "2026-10-03T09:00:00Z",
            "dedupe_key": f"{rule['id']}:{channel}",
            "metadata": {},
        }]


class FakeRuntime:
    made = []

    def __init__(self, **kwargs):
        self.engine = kwargs["engine"]
        self.dedup_path = kwargs["dedup_path"]
        FakeRuntime.made.append(self)

    def on_frame(self, channel, detections, frame_size, when=None):
        return self.engine.process(channel, detections, frame_size, when)


def _mux(tmp_path):
    FakeRuntime.made.clear()
    return recorder_analytics.RecorderAnalyticsMux(
        cloud=object(),
        state={"agent_id": "agent", "agent_key": "key"},
        lease=object(),
        actions=object(),
        dedup_dir=tmp_path,
        engine_factory=FakeEngine,
        runtime_factory=FakeRuntime,
    )


def _config():
    return {
        "timezone": "Asia/Karachi",
        "cameras": [
            {
                "id": "cam-a",
                "recorder_id": "11111111-1111-1111-1111-111111111111",
                "channel": "1",
                "rules": [{"id": "rule-a"}],
            },
            {
                "id": "cam-b",
                "recorder_id": "22222222-2222-2222-2222-222222222222",
                "channel": "1",
                "rules": [{"id": "rule-b"}],
            },
        ],
    }


def test_overlapping_channel_one_produces_two_distinct_targets(tmp_path):
    mux = _mux(tmp_path)
    mux.configure(_config())
    plan = mux.sample_plan()
    assert len(plan) == 2
    targets = [row[0] for row in plan]
    resolved = {mux.resolve_target(t) for t in targets}
    assert resolved == {
        ("11111111-1111-1111-1111-111111111111", "1"),
        ("22222222-2222-2222-2222-222222222222", "1"),
    }
    assert mux.camera_count == 2


def test_events_are_stamped_with_exact_recorder(tmp_path):
    mux = _mux(tmp_path)
    mux.configure(_config())
    by_recorder = {}
    for target, _interval in mux.sample_plan():
        recorder_id, channel = mux.resolve_target(target)
        rows = mux.on_frame(target, [], (640, 480))
        assert len(rows) == 1
        assert rows[0]["recorder_id"] == recorder_id
        assert rows[0]["channel"] == channel
        by_recorder[recorder_id] = rows[0]["rule_id"]
    assert by_recorder == {
        "11111111-1111-1111-1111-111111111111": "rule-a",
        "22222222-2222-2222-2222-222222222222": "rule-b",
    }


def test_each_recorder_gets_isolated_engine_and_action_dedup_file(tmp_path):
    mux = _mux(tmp_path)
    mux.configure(_config())
    assert len(FakeRuntime.made) == 2
    camera_sets = [
        {row["id"] for row in rt.engine.config["cameras"]}
        for rt in FakeRuntime.made
    ]
    assert {frozenset(x) for x in camera_sets} == {
        frozenset({"cam-a"}), frozenset({"cam-b"})
    }
    assert len({str(rt.dedup_path) for rt in FakeRuntime.made}) == 2


def test_multi_recorder_config_with_missing_recorder_id_fails_closed(tmp_path):
    cfg = _config()
    cfg["cameras"].append({
        "id": "ambiguous",
        "channel": "2",
        "rules": [{"id": "rule-x"}],
    })
    mux = _mux(tmp_path)
    with pytest.raises(ValueError, match="without recorder_id"):
        mux.configure(cfg)


def test_singleton_legacy_camera_remains_supported(tmp_path):
    mux = _mux(tmp_path)
    mux.configure({
        "cameras": [{
            "id": "legacy-cam",
            "channel": "1",
            "rules": [{"id": "legacy-rule"}],
        }]
    })
    target = mux.sample_plan()[0][0]
    assert mux.resolve_target(target) == (None, "1")
    rows = mux.on_frame(target, [], (640, 480))
    assert "recorder_id" not in rows[0]


def test_runtime_recorder_identity_mismatch_fails_closed(tmp_path):
    class BadRuntime(FakeRuntime):
        def on_frame(self, channel, detections, frame_size, when=None):
            return [{
                "rule_id": "x",
                "channel": channel,
                "recorder_id": "99999999-9999-9999-9999-999999999999",
            }]

    mux = recorder_analytics.RecorderAnalyticsMux(
        cloud=object(), state={}, lease=None, actions=None,
        dedup_dir=tmp_path, engine_factory=FakeEngine,
        runtime_factory=BadRuntime,
    )
    mux.configure(_config())
    target = mux.sample_plan()[0][0]
    with pytest.raises(RuntimeError, match="identity mismatch"):
        mux.on_frame(target, [], (1, 1))
