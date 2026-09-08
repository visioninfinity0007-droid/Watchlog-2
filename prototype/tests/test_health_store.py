#!/usr/bin/env python3
"""Phase A — increment 5: durable local health store (transitions + checkpoints).

A dedicated SQLite/WAL store, SEPARATE from the event spool, that survives cloud outages and
restarts and never crashes the agent. Records transitions only on change (with provenance +
observed time + a stable dedupe id) and periodic observation checkpoints; holds no image bytes
and no secrets; retention/compaction is bounded.

Red before health_store.py exists.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import health_store  # noqa: E402
from health_store import HealthStore  # noqa: E402

AID = "agent-abc"


def new(tmp) -> HealthStore:
    return HealthStore(tmp / "health.sqlite", agent_id=AID)


def test_transition_written_when_cloud_unavailable(tmp_path):
    s = new(tmp_path)
    row = s.observe(layer="camera", entity="1", new_state="offline",
                    reason="video_loss", source="native", device_ts="2026-09-07T10:00:00Z")
    assert row is not None and row["to_state"] == "offline" and row["source"] == "native"
    assert row["dedupe_key"].startswith(AID + ":")
    assert len(s.pending_transitions(100)) == 1


def test_no_transition_when_state_unchanged(tmp_path):
    s = new(tmp_path)
    s.observe("camera", "1", "operational", "ok", "probe", "2026-09-07T10:00:00Z")
    again = s.observe("camera", "1", "operational", "ok", "probe", "2026-09-07T10:01:00Z")
    assert again is None                      # steady state -> no new transition (idempotent)
    assert len(s.pending_transitions(100)) == 1


def test_change_then_change_back_are_two_transitions(tmp_path):
    s = new(tmp_path)
    s.observe("camera", "1", "operational", "ok", "probe", "t1")
    s.observe("camera", "1", "offline", "video_loss", "native", "t2")
    s.observe("camera", "1", "operational", "ok", "probe", "t3")
    assert len(s.pending_transitions(100)) == 3


def test_checkpoint_persists(tmp_path):
    s = new(tmp_path)
    s.checkpoint(device_ts="2026-09-07T10:00:00Z", nvr_state="ok", cameras_observed=8)
    cps = s.pending_checkpoints(100)
    assert len(cps) == 1 and cps[0]["nvr_state"] == "ok" and cps[0]["cameras_observed"] == 8


def test_pending_then_mark_uploaded(tmp_path):
    s = new(tmp_path)
    r = s.observe("camera", "2", "degraded", "probe_timeout", "probe", "t1")
    s.mark_transitions_uploaded([r["dedupe_key"]])
    assert s.pending_transitions(100) == []


def test_restart_preserves_unsent_records(tmp_path):
    s = new(tmp_path)
    r = s.observe("camera", "3", "offline", "video_loss", "native", "t1")
    key = r["dedupe_key"]
    s.checkpoint("t1", "ok", 8)
    s.close()
    s2 = HealthStore(tmp_path / "health.sqlite", agent_id=AID)   # reopen
    assert [t["dedupe_key"] for t in s2.pending_transitions(100)] == [key]
    assert len(s2.pending_checkpoints(100)) == 1


def test_dedupe_key_is_stable_and_monotonic_across_restart(tmp_path):
    s = new(tmp_path)
    k1 = s.observe("camera", "1", "offline", "video_loss", "native", "t1")["dedupe_key"]
    s.close()
    s2 = HealthStore(tmp_path / "health.sqlite", agent_id=AID)
    k2 = s2.observe("camera", "1", "operational", "ok", "probe", "t2")["dedupe_key"]
    # id is "<agent>:<epoch>:<seq>" — intact restart keeps the epoch and keeps climbing the seq
    assert k1.split(":")[1] == k2.split(":")[1]                     # same epoch
    assert int(k2.split(":")[-1]) > int(k1.split(":")[-1])          # seq monotonic


# --- store epoch: makes dedupe ids rebuild-proof (the corruption-collision fix) ----

def test_intact_restart_preserves_epoch(tmp_path):
    s = new(tmp_path); e1 = s.epoch; s.close()
    s2 = HealthStore(tmp_path / "health.sqlite", agent_id=AID)
    assert s2.epoch == e1 and e1


def test_corruption_rebuild_mints_a_new_epoch(tmp_path):
    p = tmp_path / "health.sqlite"
    s = HealthStore(p, agent_id=AID); e1 = s.epoch; s.close()
    p.write_bytes(b"corrupt \x00\x01\x02 not a sqlite database")
    s2 = HealthStore(p, agent_id=AID)
    assert s2.epoch and s2.epoch != e1


def test_transition_seq_resets_after_rebuild_but_id_globally_distinct(tmp_path):
    p = tmp_path / "health.sqlite"
    s = HealthStore(p, agent_id=AID)
    s.observe("camera", "1", "offline", "video_loss", "native", "t1")
    s.observe("camera", "1", "operational", "ok", "probe", "t2")
    old = {t["dedupe_key"] for t in s.pending_transitions(100)}
    old_epoch = s.epoch
    s.close()
    p.write_bytes(b"corrupt \x00 not a db")
    s2 = HealthStore(p, agent_id=AID)
    k = s2.observe("camera", "1", "offline", "video_loss", "native", "t3")["dedupe_key"]
    assert k.split(":")[-1] == "1"          # local AUTOINCREMENT restarted at 1
    assert s2.epoch != old_epoch            # but a new epoch was minted
    assert k not in old                      # so the global id can NEVER collide with old rows


def test_checkpoint_id_globally_distinct_after_rebuild(tmp_path):
    p = tmp_path / "health.sqlite"
    s = HealthStore(p, agent_id=AID)
    s.checkpoint("t1", "ok", 8)
    old_id = s.export_batch(10)["checkpoints"][0]["id"]
    s.close()
    p.write_bytes(b"corrupt \x00 not a db")
    s2 = HealthStore(p, agent_id=AID)
    s2.checkpoint("t2", "ok", 8)
    new_c = s2.export_batch(10)["checkpoints"][0]
    assert new_c["seq"] == 1                        # local seq restarted
    assert new_c["id"] != old_id                    # server identity still distinct (new epoch)
    assert new_c["store_epoch"] == s2.epoch


def test_pending_overflow_is_observable_not_silent(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID, max_pending_transitions=3)
    for i in range(6):                              # 6 distinct-entity changes -> 6 pending
        s.observe("camera", str(i), "offline", "video_loss", "native", f"t{i}")
    s.compact()
    assert len(s.pending_transitions(100)) == 3     # bounded to the ceiling
    assert s.overflow_count() == 3                   # and the drop is COUNTED (observable), not silent


def test_export_transitions_carry_store_epoch_for_durable_ordering(tmp_path):
    s = new(tmp_path)
    s.observe("camera", "1", "offline", "video_loss", "native", "t1")
    t = s.export_batch(100)["transitions"][0]
    assert t["store_epoch"] == s.epoch and t["seq"] == int(t["id"].split(":")[-1])


def test_no_image_or_blob_columns(tmp_path):
    s = new(tmp_path)
    cols = s._all_columns()                   # {table: [colnames]}
    flat = " ".join(c.lower() for t in cols.values() for c in t)
    for banned in ("image", "blob", "jpeg", "snapshot", "bytes", "frame"):
        assert banned not in flat, f"health store must not carry {banned}"


def test_secrets_never_enter_the_ledger_or_batch(tmp_path):
    s = new(tmp_path)
    # even if a caller passes junk, only the defined fields are stored; export carries no secrets
    s.observe("camera", "1", "offline", "video_loss", "native", "t1")
    s.checkpoint("t1", "ok", 8)
    import json
    blob = json.dumps(s.export_batch(100)).lower()
    for secret in ("password", "authorization", "http://", "rtsp", "agent_key", "admin"):
        assert secret not in blob


def test_locked_db_fails_safe_not_crash(tmp_path, monkeypatch):
    s = new(tmp_path)

    class Boom:
        def execute(self, *a, **k):
            import sqlite3
            raise sqlite3.OperationalError("database is locked")
        def commit(self): pass

    monkeypatch.setattr(s, "db", Boom())
    # a write failure must return safely, never raise
    assert s.observe("camera", "1", "offline", "video_loss", "native", "t1") is None


def test_corrupt_store_file_is_fail_safe(tmp_path):
    p = tmp_path / "health.sqlite"
    p.write_bytes(b"this is not a sqlite database at all \x00\x01\x02")
    s = HealthStore(p, agent_id=AID)          # must not raise on a corrupt file
    # and it must be usable afterwards (rebuilt)
    assert s.observe("camera", "1", "offline", "video_loss", "native", "t1") is not None


def test_compaction_is_bounded(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID,
                    max_checkpoints=50, max_transition_age_days=3650)
    for i in range(120):
        s.checkpoint(f"t{i}", "ok", 8)
        s.mark_checkpoints_uploaded([c["seq"] for c in s.pending_checkpoints(1000)])
    s.compact()
    # uploaded checkpoints are collapsed to the bound; never grows without limit
    assert s.total_checkpoints() <= 50


def test_compaction_never_drops_unsent_transition(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID, max_transition_age_days=0)
    r = s.observe("camera", "1", "offline", "video_loss", "native", "t1")  # pending, "old"
    s.compact()
    # an UNRESOLVED (pending) transition is never dropped just for being old
    assert [t["dedupe_key"] for t in s.pending_transitions(100)] == [r["dedupe_key"]]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
