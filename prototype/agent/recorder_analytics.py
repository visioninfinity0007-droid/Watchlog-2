"""Recorder-aware analytics multiplexing.

One site-level analytics authority/lease remains authoritative. This mux isolates
camera/rule/tracker state per recorder so overlapping channel numbers cannot
cross-attribute business intelligence.

The mux does not open recorder connections. It returns opaque sample targets;
the caller resolves the target recorder through recorder_runtime and owns driver
lifecycle.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import analytics
from runtime import AgentRuntime


LEGACY_GROUP = "__legacy__"


class RecorderAnalyticsMux:
    def __init__(self, *, cloud, state, lease, actions, dedup_dir,
                 log=lambda _m: None, engine_factory=None,
                 runtime_factory=AgentRuntime):
        self.cloud = cloud
        self.state = state
        self.lease = lease
        self.actions = actions
        self.dedup_dir = Path(dedup_dir)
        self.log = log
        self.engine_factory = engine_factory or analytics.AnalyticsEngine
        self.runtime_factory = runtime_factory
        self._engines = {}
        self._runtimes = {}
        self._targets = {}
        self._config = None

    @staticmethod
    def _group_key(recorder_id):
        return str(recorder_id).strip() if recorder_id else LEGACY_GROUP

    @staticmethod
    def _target_key(group_key: str, channel: str) -> str:
        # JSON is unambiguous for arbitrary channel text and remains stable.
        return json.dumps([group_key, str(channel)], separators=(",", ":"))

    def configure(self, config: dict | None) -> None:
        cfg = copy.deepcopy(config or {})
        cameras = list(cfg.get("cameras") or [])
        explicit = {
            str(row.get("recorder_id")).strip()
            for row in cameras if row.get("recorder_id")
        }
        if len(explicit) > 1 and any(not row.get("recorder_id") for row in cameras):
            raise ValueError(
                "multi-recorder analytics config contains camera without recorder_id"
            )

        groups = {}
        for camera in cameras:
            key = self._group_key(camera.get("recorder_id"))
            groups.setdefault(key, []).append(camera)

        new_engines = {}
        new_runtimes = {}
        targets = {}
        for key, rows in groups.items():
            filtered = copy.deepcopy(cfg)
            filtered["cameras"] = rows
            engine = self.engine_factory(log=self.log)
            engine.configure(filtered)
            safe = key.replace("/", "_").replace("\\", "_").replace(":", "_")
            runtime = self.runtime_factory(
                cloud=self.cloud,
                state=self.state,
                engine=engine,
                lease=self.lease,
                actions=self.actions,
                dedup_path=self.dedup_dir / f"analytics_action_dedup.{safe}.json",
                log=self.log,
            )
            new_engines[key] = engine
            new_runtimes[key] = runtime
            for channel, _interval in engine.sample_plan():
                target = self._target_key(key, str(channel))
                if target in targets:
                    raise ValueError("duplicate recorder analytics target")
                targets[target] = (None if key == LEGACY_GROUP else key, str(channel))

        self._config = cfg
        self._engines = new_engines
        self._runtimes = new_runtimes
        self._targets = targets

    def sample_plan(self):
        plan = []
        for key, engine in self._engines.items():
            for channel, interval in engine.sample_plan():
                target = self._target_key(key, str(channel))
                self._targets[target] = (None if key == LEGACY_GROUP else key, str(channel))
                plan.append((target, interval))
        return plan

    def resolve_target(self, target: str):
        if target not in self._targets:
            raise KeyError("unknown recorder analytics target")
        return self._targets[target]

    def on_frame(self, target: str, detections, frame_size, when=None) -> list[dict]:
        recorder_id, channel = self.resolve_target(target)
        key = self._group_key(recorder_id)
        runtime = self._runtimes.get(key)
        if runtime is None:
            raise KeyError("analytics runtime not configured for recorder")
        events = runtime.on_frame(channel, detections, frame_size, when)
        out = []
        for event in events:
            row = dict(event)
            if recorder_id:
                existing = row.get("recorder_id")
                if existing and str(existing) != str(recorder_id):
                    raise RuntimeError("analytic event recorder identity mismatch")
                row["recorder_id"] = str(recorder_id)
            out.append(row)
        return out

    @property
    def recorder_ids(self):
        return {
            rid for rid, _channel in self._targets.values()
            if rid is not None
        }

    @property
    def camera_count(self):
        return len(self._targets)
