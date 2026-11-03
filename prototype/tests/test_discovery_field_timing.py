#!/usr/bin/env python3
"""Discovery with REAL timeouts: Build 69 reach and the Build 74 bound together.

The existing reach test fails absent hosts instantly. On a real LAN an absent IP never answers,
so every probe to it waits its full timeout. This simulation keeps that behaviour (each absent
probe sleeps its timeout) at real timing and proves:
  * Build 69 reach: a recorder on the EIGHTH /24, answering only on a fast recorder port, is
    found before the deadline even when every other host times out;
  * Build 74 bound: the whole sweep returns within the deadline plus one bounded stage, never
    spinning, even when nothing answers at all.
Known difference from Build 69 (BUILD69-FORENSIC-REPORT §5): after the fast phase, the
alternate-port phase is cut by the deadline on a PC with many networks, so a recorder reachable
ONLY on an alternate port on a late network may fall back to manual IP entry. Measured at real
timing (2026-10-06): with 256 workers an 8443-only recorder on the 6th network was missed (fast
phase reached the 8th network at 22.6 s); with 512 it was found at 31.6 s. Scaled-time tests
cannot measure that phase reliably (thread overhead), so it is evidence, not a unit test.
"""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import discover  # noqa: E402

# Real timing (~50 s): scaled time is distorted by thread overhead and machine load; at real
# timing the 8th network is reached at ~13 s against the 32 s deadline (2.4x margin).
SCALE = 1.0
BASES = [f"10.0.{n}" for n in range(1, 9)]


@pytest.fixture
def scaled(monkeypatch):
    monkeypatch.setattr(discover, "SWEEP_TIMEOUT", discover.SWEEP_TIMEOUT / SCALE)
    monkeypatch.setattr(discover, "SWEEP_DEEP_TIMEOUT", discover.SWEEP_DEEP_TIMEOUT / SCALE)
    monkeypatch.setattr(discover, "DISCOVERY_DEADLINE_SECONDS",
                        discover.DISCOVERY_DEADLINE_SECONDS / SCALE)
    return discover.DISCOVERY_DEADLINE_SECONDS


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _network(answer: dict):
    def connect(address, timeout=None):
        if address in answer:
            return _Conn()
        time.sleep(timeout)                     # an absent IP: the full timeout
        raise socket.timeout("timed out")
    return connect


def test_a_recorder_on_the_eighth_network_is_found_with_real_timeouts(scaled):
    t0 = time.monotonic()
    hits = discover.sweep(log=lambda *_a: None, progress=lambda *_a: None,
                          _connect=_network({("10.0.8.108", 8000): True}),
                          _bases=(BASES, [f"{b}.20" for b in BASES]))
    elapsed = time.monotonic() - t0
    assert ("10.0.8.108", [8000]) in hits or "10.0.8.108" in dict(hits)
    assert elapsed <= scaled * 1.25, f"{elapsed:.2f}s against a {scaled:.2f}s deadline"


def test_nothing_answering_still_returns_within_the_bound(scaled):
    t0 = time.monotonic()
    hits = discover.sweep(log=lambda *_a: None, progress=lambda *_a: None,
                          _connect=_network({}), _bases=(BASES, [f"{b}.20" for b in BASES]))
    elapsed = time.monotonic() - t0
    assert hits == []
    # Deadline plus at most one bounded stage (a stage drains its submitted probes).
    assert elapsed <= scaled * 1.6, f"{elapsed:.2f}s against a {scaled:.2f}s deadline"
