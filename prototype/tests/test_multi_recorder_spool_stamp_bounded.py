#!/usr/bin/env python3
"""Continuity spool stamping is bounded and incremental (MNVR-020).

Every multi-recorder start stamps queued singleton events with the continuity recorder's
cloud id. It used to read the whole spool (inline stills included) into memory and parse
every row on every start: a long outage's backlog could end in MemoryError and SystemExit
for the whole site. Stamping must read the spool in bounded batches, keep the
all-or-nothing rule for a malformed or foreign row, and on a later start look only at rows
queued since the last stamp.
"""
from __future__ import annotations

import json
import sys
import tracemalloc
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import spool as spool_mod  # noqa: E402
from spool import Spool  # noqa: E402

RID = "11111111-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
STILL = "A" * (64 * 1024)          # an inline still is ~64-300 KB of base64


def _event(n, **extra):
    row = {"channel": "1", "event_type": "motion", "n": n,
           "device_ts": "2026-10-02T12:00:00Z", "agent_ts": "2026-10-02T12:00:00Z",
           "payload": {}, "snapshot_b64": STILL}
    row.update(extra)
    return row


class CountingJson:
    """json stand-in that counts how many spool payloads are parsed."""

    def __init__(self):
        self.loads_calls = 0

    def loads(self, raw, *a, **kw):
        self.loads_calls += 1
        return json.loads(raw, *a, **kw)

    def dumps(self, obj, *a, **kw):
        return json.dumps(obj, *a, **kw)


def test_stamping_a_large_backlog_keeps_memory_bounded(tmp_path):
    spool = Spool(tmp_path / "spool.sqlite")
    try:
        for n in range(400):                      # ~26 MB of queued stills
            spool.add(_event(n))
        tracemalloc.start()
        try:
            stamped = spool.stamp_missing_recorder_id(RID)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert stamped == 400
        assert peak < 12 * 1024 * 1024, f"stamping held {peak // (1024 * 1024)} MB at once"
        _, rows = spool.take(500)
        assert len(rows) == 400 and all(r["recorder_id"] == RID for r in rows)
    finally:
        spool.close()


def test_a_second_start_reads_only_rows_queued_since_the_last_stamp(tmp_path, monkeypatch):
    spool = Spool(tmp_path / "spool.sqlite")
    try:
        for n in range(50):
            spool.add(_event(n))
        assert spool.stamp_missing_recorder_id(RID) == 50

        counter = CountingJson()
        monkeypatch.setattr(spool_mod, "json", counter)
        assert spool.stamp_missing_recorder_id(RID) == 0
        assert counter.loads_calls == 0, "an unchanged spool must not be re-read"

        monkeypatch.setattr(spool_mod, "json", json)
        spool.add(_event(50))                     # e.g. queued after the stamp
        spool.add(_event(51, recorder_id=RID))    # already stamped by the collector
        counter = CountingJson()
        monkeypatch.setattr(spool_mod, "json", counter)
        assert spool.stamp_missing_recorder_id(RID) == 1
        assert counter.loads_calls <= 2 + 1, counter.loads_calls
        monkeypatch.setattr(spool_mod, "json", json)
        _, rows = spool.take(100)
        assert all(r["recorder_id"] == RID for r in rows)
    finally:
        spool.close()


def test_the_stamp_marker_survives_a_restart(tmp_path, monkeypatch):
    path = tmp_path / "spool.sqlite"
    spool = Spool(path)
    for n in range(20):
        spool.add(_event(n))
    assert spool.stamp_missing_recorder_id(RID) == 20
    spool.close()

    spool = Spool(path)
    try:
        counter = CountingJson()
        monkeypatch.setattr(spool_mod, "json", counter)
        assert spool.stamp_missing_recorder_id(RID) == 0
        assert counter.loads_calls == 0
    finally:
        spool.close()


def test_a_malformed_row_still_changes_nothing(tmp_path):
    spool = Spool(tmp_path / "spool.sqlite")
    try:
        for n in range(70):                       # spans more than one batch
            spool.add(_event(n))
        spool.db.execute("insert into spool(payload) values (?)", ("not-json",))
        with pytest.raises(json.JSONDecodeError):
            spool.stamp_missing_recorder_id(RID)
        raw = [r[0] for r in spool.db.execute("select payload from spool order by id")]
        assert all('"recorder_id"' not in r for r in raw), "no partial stamping"
    finally:
        spool.close()


def test_a_row_for_another_recorder_still_aborts_before_any_write(tmp_path):
    spool = Spool(tmp_path / "spool.sqlite")
    try:
        for n in range(70):
            spool.add(_event(n))
        spool.add(_event(70, recorder_id="22222222-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
        with pytest.raises(ValueError, match="another recorder"):
            spool.stamp_missing_recorder_id(RID)
        raw = [r[0] for r in spool.db.execute("select payload from spool order by id")]
        assert sum(RID in r for r in raw) == 0
    finally:
        spool.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
