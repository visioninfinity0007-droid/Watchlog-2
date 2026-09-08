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


def _quarantine_n(s, n):
    """Observe n distinct transitions and quarantine them all; return their dedupe keys (seq order)."""
    keys = []
    for i in range(n):
        r = s.observe("camera_recording", str(i), "not_recording", "not_recording", "probe", f"t{i}")
        keys.append(r["dedupe_key"])
    s.quarantine_transitions([(k, "missing_state") for k in keys])
    return keys


def _quarantine_cp_n(s, n):
    """Create n checkpoints and quarantine them all; return their local seqs (creation order)."""
    seqs = [s.checkpoint(f"c{i}", "ok", 8)["seq"] for i in range(n)]
    s.quarantine_checkpoints([(seq, "invalid_timestamp") for seq in seqs])
    return seqs


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


def test_store_preserves_layer_for_reconcile_split(tmp_path):
    # increment 6: recording/storage transitions ride the SAME durable store, tagged by layer so
    # reconcile can route them to the right RPC.
    s = new(tmp_path)
    s.observe("camera_recording", "3", "not_recording", "not_recording", "probe", "t1")
    s.observe("nvr_storage", AID, "fault", "storage_fault", "probe", "t2")
    layers = {t["layer"] for t in s.export_batch(100)["transitions"]}
    assert layers == {"camera_recording", "nvr_storage"}


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


# --- increment 6: reconcile-disposition parking (quarantine poison / bounded-retry transient) ------

def test_quarantine_parks_poison_out_of_pending(tmp_path):
    s = new(tmp_path)
    r = s.observe("camera_recording", "1", "not_recording", "not_recording", "probe", "t1")
    s.quarantine_transitions([(r["dedupe_key"], "missing_state")])
    assert s.pending_transitions(100) == []                          # no longer retried
    q = s.quarantined_transitions(100)
    assert len(q) == 1 and q[0]["last_reason"] == "missing_state"    # parked + reason observable


def test_quarantined_row_excluded_from_export_batch(tmp_path):
    s = new(tmp_path)
    r = s.observe("camera_recording", "1", "not_recording", "not_recording", "probe", "t1")
    s.quarantine_transitions([(r["dedupe_key"], "wrong_layer")])
    assert s.export_batch(100)["transitions"] == []                  # never re-sent to the cloud


def test_defer_is_bounded_then_quarantines(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID, max_transient_attempts=2)
    r = s.observe("camera_recording", "1", "not_recording", "not_recording", "probe", "t1")
    dk = r["dedupe_key"]
    s.defer_transitions([(dk, "unmapped_channel")])                  # attempt 1 -> still retryable
    assert dk in {t["dedupe_key"] for t in s.pending_transitions(100)} and s.quarantined_count() == 0
    s.defer_transitions([(dk, "unmapped_channel")])                  # attempt 2 -> hits bound
    assert s.pending_transitions(100) == []                          # no endless retry
    q = s.quarantined_transitions(100)
    assert len(q) == 1 and q[0]["dedupe_key"] == dk and q[0]["last_reason"] == "unmapped_channel"


def test_quarantine_checkpoint_parks_out_of_pending(tmp_path):
    s = new(tmp_path)
    c = s.checkpoint("t1", "ok", 8)
    s.quarantine_checkpoints([(c["seq"], "invalid_timestamp")])
    assert s.pending_checkpoints(100) == []                          # not retried
    q = s.quarantined_checkpoints(100)
    assert len(q) == 1 and q[0]["last_reason"] == "invalid_timestamp"


def test_quarantined_checkpoint_excluded_from_export_batch(tmp_path):
    s = new(tmp_path)
    c = s.checkpoint("t1", "ok", 8)
    s.quarantine_checkpoints([(c["seq"], "missing_id")])
    assert s.export_batch(100)["checkpoints"] == []                  # never re-sent to the cloud


def test_defer_then_uploaded_clears_without_quarantine(tmp_path):
    # if a deferred (transient) row is later accepted, marking it uploaded wins over any bound.
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID, max_transient_attempts=2)
    r = s.observe("camera_recording", "1", "not_recording", "not_recording", "probe", "t1")
    s.defer_transitions([(r["dedupe_key"], "unmapped_channel")])     # attempt 1
    s.mark_transitions_uploaded([r["dedupe_key"]])                   # mapping appeared, accepted
    assert s.pending_transitions(100) == [] and s.quarantined_count() == 0


def test_migrate_adds_columns_to_pre_existing_store(tmp_path):
    import sqlite3
    p = tmp_path / "health.sqlite"
    # a store file written by an OLDER agent: transitions WITHOUT attempts/last_reason.
    db = sqlite3.connect(p)
    db.executescript(
        "create table meta (k text primary key, v text);"
        "create table last_state (key text primary key, state text not null);"
        "create table transitions (seq integer primary key autoincrement, dedupe_key text unique,"
        " layer text not null, entity text not null, from_state text, to_state text not null,"
        " reason text not null, source text not null, device_ts text not null,"
        " status text not null default 'pending', created_at integer not null default 0);"
        "create table checkpoints (seq integer primary key autoincrement, device_ts text not null,"
        " nvr_state text not null, cameras_observed integer not null, cycle_ok integer not null default 1,"
        " status text not null default 'pending', created_at integer not null default 0);"
        "insert into transitions (dedupe_key, layer, entity, from_state, to_state, reason, source,"
        " device_ts) values ('old:1','camera_recording','1',null,'not_recording','not_recording',"
        " 'probe','t1');"
        "insert into checkpoints (device_ts, nvr_state, cameras_observed) values ('t1','ok',8);")
    db.commit(); db.close()
    s = HealthStore(p, agent_id=AID)                                 # opens + migrates, never raises
    assert {t["dedupe_key"] for t in s.pending_transitions(100)} == {"old:1"}   # old rows intact
    s.quarantine_transitions([("old:1", "wrong_layer")])            # new transition path works
    old_cp_seq = s.pending_checkpoints(100)[0]["seq"]
    s.quarantine_checkpoints([(old_cp_seq, "invalid_timestamp")])   # new checkpoint path works too
    assert s.pending_transitions(100) == [] and s.quarantined_count() == 1
    assert s.pending_checkpoints(100) == [] and len(s.quarantined_checkpoints(100)) == 1


# --- increment 6: QUARANTINE retention is bounded + observable (must not escape storage limits) ----

def test_quarantined_excluded_from_retry_and_export(tmp_path):
    s = new(tmp_path)
    k = _quarantine_n(s, 1)[0]
    assert s.pending_transitions(100) == []                          # not retried
    assert s.export_batch(100)["transitions"] == []                  # not re-sent
    assert [t["dedupe_key"] for t in s.quarantined_transitions(100)] == [k]


def test_quarantined_transitions_survive_restart(tmp_path):
    p = tmp_path / "health.sqlite"
    s = HealthStore(p, agent_id=AID)
    k = _quarantine_n(s, 1)[0]
    s.close()
    s2 = HealthStore(p, agent_id=AID)                                # reopen
    assert [t["dedupe_key"] for t in s2.quarantined_transitions(100)] == [k]
    assert s2.pending_transitions(100) == []                         # not resurrected as pending


def test_quarantined_under_bounds_retained_for_forensics(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID,
                    max_quarantine_age_days=3650, max_quarantined_transitions=100)
    _quarantine_n(s, 5)
    s.compact()
    assert s.quarantined_count() == 5 and s.quarantined_transitions_pruned_count() == 0   # under both bounds


def test_quarantine_count_overflow_prunes_oldest_observably(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID,
                    max_quarantine_age_days=3650, max_quarantined_transitions=3)
    keys = _quarantine_n(s, 5)                                       # keep newest 3, prune oldest 2
    s.compact()
    remaining = {t["dedupe_key"] for t in s.quarantined_transitions(100)}
    assert remaining == set(keys[2:]) and s.quarantined_count() == 3
    assert s.quarantined_transitions_pruned_count() == 2                         # the drop is COUNTED, not silent


def test_quarantine_age_overflow_prunes_observably(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID, max_quarantine_age_days=0)
    _quarantine_n(s, 3)                                              # quarantined now; age 0 -> all overdue
    s.compact()
    assert s.quarantined_count() == 0 and s.quarantined_transitions_pruned_count() == 3


def test_quarantine_pruning_never_deletes_pending(tmp_path):
    # aggressive quarantine bounds must NEVER reach an unresolved (pending) row.
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID,
                    max_quarantine_age_days=0, max_quarantined_transitions=0)
    pend = s.observe("camera", "keep", "offline", "video_loss", "native", "t0")["dedupe_key"]
    _quarantine_n(s, 3)
    s.compact()
    assert s.quarantined_count() == 0                                # all quarantined pruned
    assert [t["dedupe_key"] for t in s.pending_transitions(100)] == [pend]   # pending untouched
    assert s.quarantined_transitions_pruned_count() == 3


# --- increment 6: CHECKPOINT quarantine is independently bounded + observable ----------------------

def test_checkpoint_quarantine_count_overflow_prunes_oldest(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID,
                    max_quarantine_age_days=3650, max_quarantined_checkpoints=3)
    seqs = _quarantine_cp_n(s, 5)
    s.compact()
    remaining = {c["seq"] for c in s.quarantined_checkpoints(100)}
    assert remaining == set(seqs[2:]) and len(remaining) == 3          # newest 3 kept
    assert s.quarantined_checkpoints_pruned_count() == 2               # observable, per-table


def test_checkpoint_quarantine_age_overflow_prunes(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID, max_quarantine_age_days=0)
    _quarantine_cp_n(s, 3)
    s.compact()
    assert len(s.quarantined_checkpoints(100)) == 0
    assert s.quarantined_checkpoints_pruned_count() == 3


def test_generic_checkpoint_bound_never_silently_deletes_quarantine(tmp_path):
    # once status can be quarantined, the generic max_checkpoints bound must EXCLUDE quarantine from
    # both its count and its deletion — otherwise it would drop quarantine without the quarantine counter.
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID, max_checkpoints=3,
                    max_quarantine_age_days=3650, max_quarantined_checkpoints=100)
    q = _quarantine_cp_n(s, 2)                                         # 2 quarantined (forensic)
    up = [s.checkpoint(f"u{i}", "ok", 8)["seq"] for i in range(5)]     # 5 uploaded (continuity)
    s.mark_checkpoints_uploaded(up)
    s.compact()
    assert {c["seq"] for c in s.quarantined_checkpoints(100)} == set(q)   # quarantine untouched...
    assert s.quarantined_checkpoints_pruned_count() == 0                  # ...and NOT counted as a quarantine prune
    nonq = s.total_checkpoints() - len(s.quarantined_checkpoints(100))
    assert nonq <= 3                                                      # generic bound trimmed only non-quarantine


def test_pending_checkpoint_survives_generic_compaction(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=AID, max_checkpoints=1)
    keep = s.checkpoint("keep", "ok", 8)["seq"]                        # pending continuity
    s.mark_checkpoints_uploaded([s.checkpoint(f"u{i}", "ok", 8)["seq"] for i in range(3)])
    s.compact()
    assert keep in {c["seq"] for c in s.pending_checkpoints(100)}      # uploaded dropped first, pending kept


def test_both_quarantine_prune_counters_survive_restart(tmp_path):
    p = tmp_path / "health.sqlite"
    s = HealthStore(p, agent_id=AID, max_quarantine_age_days=0)        # age 0 -> everything prunes
    _quarantine_n(s, 2)
    _quarantine_cp_n(s, 2)
    s.compact()
    assert s.quarantined_transitions_pruned_count() == 2
    assert s.quarantined_checkpoints_pruned_count() == 2
    s.close()
    s2 = HealthStore(p, agent_id=AID)                                  # reopen -> durable counters
    assert s2.quarantined_transitions_pruned_count() == 2
    assert s2.quarantined_checkpoints_pruned_count() == 2


def test_migration_backfills_fresh_quarantine_window(tmp_path):
    # a row quarantined by an OLD agent (no quarantined_at) must get a FRESH window at migration, not
    # be pruned immediately because the original observation is ancient.
    import sqlite3
    p = tmp_path / "health.sqlite"
    db = sqlite3.connect(p)
    db.executescript(
        "create table meta (k text primary key, v text);"
        "create table last_state (key text primary key, state text not null);"
        "create table transitions (seq integer primary key autoincrement, dedupe_key text unique,"
        " layer text not null, entity text not null, from_state text, to_state text not null,"
        " reason text not null, source text not null, device_ts text not null,"
        " status text not null default 'pending', created_at integer not null default 0);"
        "create table checkpoints (seq integer primary key autoincrement, device_ts text not null,"
        " nvr_state text not null, cameras_observed integer not null, cycle_ok integer not null default 1,"
        " status text not null default 'pending', created_at integer not null default 0);"
        # already-quarantined, created long ago (created_at=0), no quarantined_at column yet
        "insert into transitions (dedupe_key, layer, entity, from_state, to_state, reason, source,"
        " device_ts, status) values ('old:1','camera_recording','1',null,'not_recording',"
        " 'missing_state','probe','t1','quarantined');"
        "insert into checkpoints (device_ts, nvr_state, cameras_observed, status)"
        " values ('t1','ok',8,'quarantined');")
    db.commit(); db.close()
    s = HealthStore(p, agent_id=AID, max_quarantine_age_days=90)       # opens + migrates + backfills
    s.compact()
    # ancient created_at must NOT cause immediate pruning — backfilled quarantined_at gives a window
    assert s.quarantined_count() == 1 and len(s.quarantined_checkpoints(100)) == 1
    assert s.quarantined_transitions_pruned_count() == 0
    assert s.quarantined_checkpoints_pruned_count() == 0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
