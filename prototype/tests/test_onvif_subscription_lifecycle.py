#!/usr/bin/env python3
"""MNVR-056: the ONVIF PullPoint subscription honours the granted lifetime,
is renewed in place, and is released on close and before re-subscribing.

A device may grant a shorter TerminationTime than requested, and it stamps
it with its OWN clock, so only TerminationTime - CurrentTime is meaningful.
Every pull point left behind holds a recorder subscription slot until it
expires; enough of them and CreatePullPointSubscription is refused.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import onvif_fake_recorder as fx  # noqa: E402
import watchlog_agent as core  # noqa: E402


def _driver(monkeypatch, **recorder_kw):
    rec = fx.FakeRecorder(**recorder_kw)
    rec.install(monkeypatch)
    drv, _info = core.open_driver(fx.FakeCfg())
    rec.clock = fx.FakeClock()
    drv._monotonic = rec.clock
    return drv, rec


def _empty_pulls(rec, n):
    for _ in range(n):
        rec.queue()


def _lifecycle_ops(rec):
    keep = {"CreatePullPointSubscription", "PullMessages", "Renew", "Unsubscribe"}
    return [op for op in rec.ops() if op in keep]


def test_short_grant_on_a_skewed_device_clock_is_renewed_in_place(monkeypatch):
    # 5-minute grant (half what was asked) stamped by a device clock years off
    # the PC clock. Each pull long-polls for 60 s of monotonic time.
    drv, rec = _driver(monkeypatch, grant_seconds=300, device_now="2020-01-01T00:00:00Z")
    rec.pull_seconds = 60
    _empty_pulls(rec, 4)
    fx.stream(drv, rec)

    ops = _lifecycle_ops(rec)
    assert ops[0] == "CreatePullPointSubscription"
    assert ops.count("CreatePullPointSubscription") == 1
    # Renewed before the 300 s grant ran out, not before the first pull (the
    # skewed clock is irrelevant) and not by creating another pull point.
    assert ops[1] == "PullMessages"
    assert "Renew" in ops
    first_renew = ops.index("Renew")
    assert ops[:first_renew].count("PullMessages") * rec.pull_seconds < 300
    renew = rec.calls_of("Renew")[0]
    assert renew["url"] == "http://192.0.2.10/onvif/subscription/1"
    drv.close()


def test_long_grant_is_not_renewed_early(monkeypatch):
    drv, rec = _driver(monkeypatch, grant_seconds=600)
    rec.pull_seconds = 60
    _empty_pulls(rec, 5)          # 5 min of pulls inside a 10 min grant
    fx.stream(drv, rec)

    assert "Renew" not in rec.ops()
    assert rec.subscriptions == 1
    drv.close()


def test_refused_renew_unsubscribes_before_creating_a_new_pull_point(monkeypatch):
    drv, rec = _driver(monkeypatch, grant_seconds=120)
    rec.pull_seconds = 100
    rec.fail.add("Renew")
    _empty_pulls(rec, 2)
    fx.stream(drv, rec)

    ops = _lifecycle_ops(rec)
    assert ops.count("CreatePullPointSubscription") == 2
    second_create = [i for i, op in enumerate(ops) if op == "CreatePullPointSubscription"][1]
    unsub = [c for c in rec.calls_of("Unsubscribe")
             if c["url"] == "http://192.0.2.10/onvif/subscription/1"]
    assert unsub, "the old pull point was never released"
    assert ops.index("Unsubscribe") < second_create
    # Pulls after the replacement go to the new pull point.
    pulls = rec.calls_of("PullMessages")
    assert pulls[-1]["url"] == "http://192.0.2.10/onvif/subscription/2"
    drv.close()


def test_replaced_pull_point_does_not_re_emit_initialized_state(monkeypatch):
    # The replacement subscription reports the same current state again as
    # Initialized; it must not become a second occurrence (MNVR-027).
    drv, rec = _driver(monkeypatch, grant_seconds=120)
    rec.pull_seconds = 100
    rec.fail.add("Renew")
    motion = fx.notification("tns1:RuleEngine/CellMotionDetector/Motion",
                             "2026-10-04T10:00:00Z",
                             {"VideoSourceConfigurationToken": fx.config_token(1)},
                             {"IsMotion": "true"}, operation="Initialized")
    rec.queue(motion)
    rec.queue(motion)
    events = fx.stream(drv, rec)

    assert events == []
    assert rec.subscriptions >= 2
    drv.close()


def test_close_unsubscribes_the_live_pull_point_once(monkeypatch):
    drv, rec = _driver(monkeypatch)
    _empty_pulls(rec, 1)
    fx.stream(drv, rec)
    drv.close()
    drv.close()

    unsubs = rec.calls_of("Unsubscribe")
    assert [c["url"] for c in unsubs] == ["http://192.0.2.10/onvif/subscription/1"]
    # Best-effort and bounded: a dead link must not stall the reconnect loop.
    assert unsubs[0]["timeout"] is not None and unsubs[0]["timeout"] <= 5


def test_close_without_a_subscription_sends_nothing(monkeypatch):
    drv, rec = _driver(monkeypatch)
    before = list(rec.ops())
    drv.close()
    assert rec.ops() == before


def test_unsubscribe_fault_does_not_break_close(monkeypatch):
    drv, rec = _driver(monkeypatch)
    _empty_pulls(rec, 1)
    fx.stream(drv, rec)
    rec.fail.add("Unsubscribe")
    drv.close()                    # must not raise
    assert rec.sessions_closed == 1


def test_restarting_the_stream_releases_the_previous_pull_point(monkeypatch):
    drv, rec = _driver(monkeypatch)
    _empty_pulls(rec, 1)
    fx.stream(drv, rec)
    _empty_pulls(rec, 1)
    fx.stream(drv, rec)

    ops = _lifecycle_ops(rec)
    second_create = [i for i, op in enumerate(ops) if op == "CreatePullPointSubscription"][1]
    assert "Unsubscribe" in ops[:second_create]
    assert rec.calls_of("Unsubscribe")[0]["url"] == "http://192.0.2.10/onvif/subscription/1"
    drv.close()


def test_subscription_parse_tolerates_missing_times(monkeypatch):
    # No CurrentTime/TerminationTime in the response: fall back to the
    # requested lifetime rather than failing or renewing every pull.
    drv, rec = _driver(monkeypatch)
    monkeypatch.setattr(rec, "_times", lambda: "")
    rec.pull_seconds = 60
    _empty_pulls(rec, 5)
    fx.stream(drv, rec)
    assert "Renew" not in rec.ops()
    assert rec.subscriptions == 1
    drv.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
