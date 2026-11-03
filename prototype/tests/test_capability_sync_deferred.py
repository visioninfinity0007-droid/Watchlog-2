#!/usr/bin/env python3
"""Recorder capability enrichment runs after monitoring starts, never on the startup path.

Field Build 41: on Hikvision driver.capabilities() fans out into several ISAPI calls per
channel and delayed the live collector and heartbeat by minutes; the field builds removed it
from startup. capability_sync runs it once, later, best-effort.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import capability_sync  # noqa: E402

CAPS = {"channels": [{"channel": "1", "motion": True}]}


class Cloud:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        return {}


class Driver:
    def __init__(self, caps=CAPS, boom=None):
        self.caps, self.boom, self.closed = caps, boom, False

    def capabilities(self):
        if self.boom:
            raise self.boom
        return self.caps

    def close(self):
        self.closed = True


STATE = {"agent_id": "a", "agent_key": "k"}


def test_recorder_scoped_sync_after_the_delay():
    cloud, drv = Cloud(), Driver()
    t = capability_sync.defer(SimpleNamespace(), STATE, cloud, lambda c: (drv, None),
                              recorder_id="rec-1", delay=0.0)
    t.join(5)
    assert cloud.calls == [("wl_sync_recorder_capabilities",
                            {"p_recorder_id": "rec-1", "p_agent_id": "a", "p_agent_key": "k",
                             "p_capabilities": CAPS})]
    assert drv.closed


def test_site_level_sync_for_the_single_recorder_path():
    cloud = Cloud()
    assert capability_sync.sync_once(SimpleNamespace(), STATE, cloud, lambda c: (Driver(), None))
    assert cloud.calls[0][0] == "wl_sync_capabilities"


def test_nothing_runs_when_the_agent_stops_first():
    cloud, opened = Cloud(), []
    stop = threading.Event()
    stop.set()
    capability_sync.defer(SimpleNamespace(), STATE, cloud,
                          lambda c: opened.append(c) or (Driver(), None), stop=stop).join(5)
    assert opened == [] and cloud.calls == []


def test_a_failing_recorder_or_missing_config_never_escapes():
    cloud = Cloud()
    assert not capability_sync.sync_once(SimpleNamespace(), STATE, cloud,
                                         lambda c: (Driver(boom=RuntimeError("x")), None))

    def no_config(_c):
        raise SystemExit("no recorder configured")
    assert not capability_sync.sync_once(SimpleNamespace(), STATE, cloud, no_config)
    assert cloud.calls == []


def test_the_default_delay_keeps_it_off_the_startup_path():
    assert capability_sync.CAPABILITY_SYNC_DELAY_SECONDS >= 60
