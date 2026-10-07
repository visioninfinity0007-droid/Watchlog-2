#!/usr/bin/env python3
"""One undeliverable queued event must not hold every event behind it forever.

wl_ingest_events rejects a WHOLE batch when one row is bad: a data error, or a row naming a
recorder not configured for this site (0146, 42501). upload_once retried that batch forever, so
the queue stopped (credential-invariants audit G9-c). Rejected rows are now isolated by halving
the batch, kept aside next to the queue (never deleted, never retried) and every other row is
delivered. Identity, server and transport errors still keep the whole batch for a retry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import watchlog_agent as core  # noqa: E402
from spool import Spool  # noqa: E402

STATE = {"agent_id": "a", "agent_key": "k"}
FOREIGN = "event recorder not configured for this agent site"


class Cloud:
    """Rejects any batch containing a row marked bad, like the server does."""

    def __init__(self, error):
        self.error, self.delivered, self.calls = error, [], 0

    def call(self, fn, **kw):
        self.calls += 1
        events = kw["p_events"]
        if any(e.get("bad") for e in events):
            raise self.error
        self.delivered.extend(e["n"] for e in events)
        return {"received": len(events), "inserted": len(events), "skipped": 0}


def _spool(tmp_path, bad=(7,), n=20):
    sp = Spool(tmp_path / "spool.sqlite")
    for i in range(n):
        sp.add({"n": i, "bad": i in bad})
    return sp


@pytest.mark.parametrize("error", [
    core.CloudError("wl_ingest_events", 403, "42501", FOREIGN),
    core.CloudError("wl_ingest_events", 400, "22007", "invalid input syntax for type timestamp"),
    core.CloudError("wl_ingest_events", 409, "23503", "violates foreign key constraint"),
])
def test_rejected_rows_are_kept_aside_and_everything_else_is_delivered(tmp_path, error):
    sp = _spool(tmp_path, bad=(3, 7))
    cloud = Cloud(error)
    core.upload_once(cloud, STATE, sp)
    assert sorted(cloud.delivered) == [i for i in range(20) if i not in (3, 7)]
    assert sp.count() == 0
    aside = [json.loads(line) for line in
             Path(str(sp.path) + ".rejected.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted(r["event"]["n"] for r in aside) == [3, 7]
    assert all(r["code"] == error.code for r in aside)
    assert cloud.calls < 20, "isolation must halve the batch, not send rows one by one"
    sp.close()


@pytest.mark.parametrize("error", [
    core.CloudError("wl_ingest_events", 403, "28000", "agent not recognised"),
    core.CloudError("wl_ingest_events", 503, None, "service unavailable"),
    core.CloudError("wl_ingest_events", 403, "42501", "a recorder-push agent cannot issue push tokens"),
])
def test_identity_server_and_other_errors_keep_the_whole_batch(tmp_path, error):
    sp = _spool(tmp_path)
    with pytest.raises(core.CloudError):
        core.upload_once(Cloud(error), STATE, sp)
    assert sp.count() == 20
    assert not Path(str(sp.path) + ".rejected.jsonl").exists()
    sp.close()


def test_a_transport_failure_keeps_the_whole_batch(tmp_path):
    sp = _spool(tmp_path)

    class Down:
        def call(self, fn, **kw):
            raise OSError("network unreachable")
    with pytest.raises(OSError):
        core.upload_once(Down(), STATE, sp)
    assert sp.count() == 20
    sp.close()
