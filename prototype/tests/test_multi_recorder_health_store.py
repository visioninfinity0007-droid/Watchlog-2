"""Recorder-scoped durable local health-store contracts."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

from health_store import HealthStore  # noqa: E402


def test_same_channel_on_two_recorders_does_not_share_last_state():
    with tempfile.TemporaryDirectory() as td:
        store = HealthStore(Path(td) / "health.sqlite", "agent-1")
        try:
            a = "11111111-1111-1111-1111-111111111111"
            b = "22222222-2222-2222-2222-222222222222"

            one = store.observe(
                "camera", "1", "offline", "nvr_unreachable", "probe",
                "2026-10-02T12:00:00Z", recorder_id=a,
            )
            two = store.observe(
                "camera", "1", "offline", "nvr_unreachable", "probe",
                "2026-10-02T12:00:01Z", recorder_id=b,
            )
            repeat = store.observe(
                "camera", "1", "offline", "nvr_unreachable", "probe",
                "2026-10-02T12:00:02Z", recorder_id=a,
            )

            assert one is not None and two is not None
            assert repeat is None
            batch = store.export_batch(20)
            assert len(batch["transitions"]) == 2
            assert {x["recorder_id"] for x in batch["transitions"]} == {a, b}
            assert {x["entity"] for x in batch["transitions"]} == {"1"}
        finally:
            store.close()


def test_legacy_singleton_payload_shape_stays_unchanged():
    with tempfile.TemporaryDirectory() as td:
        store = HealthStore(Path(td) / "health.sqlite", "agent-legacy")
        try:
            row = store.observe(
                "camera", "1", "operational", "ok", "probe",
                "2026-10-02T12:01:00Z",
            )
            cp = store.checkpoint(
                "2026-10-02T12:01:01Z", "operational", 1, True
            )
            assert row is not None and "recorder_id" not in row
            assert cp is not None and "recorder_id" not in cp

            batch = store.export_batch(20)
            assert "recorder_id" not in batch["transitions"][0]
            assert "recorder_id" not in batch["checkpoints"][0]
        finally:
            store.close()


def test_recorder_checkpoint_keeps_provenance():
    with tempfile.TemporaryDirectory() as td:
        store = HealthStore(Path(td) / "health.sqlite", "agent-1")
        try:
            rid = "33333333-3333-3333-3333-333333333333"
            row = store.checkpoint(
                "2026-10-02T12:02:00Z", "degraded", 4, False,
                recorder_id=rid,
            )
            assert row["recorder_id"] == rid
            batch = store.export_batch(20)
            assert batch["checkpoints"][0]["recorder_id"] == rid
        finally:
            store.close()


def test_existing_store_schema_is_migrated_without_losing_pending_rows():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "health.sqlite"
        db = sqlite3.connect(path)
        try:
            # Historical schema shape before recorder_id existed.
            db.executescript("""
              create table meta (k text primary key, v text);
              create table last_state (key text primary key, state text not null);
              create table transitions (
                seq integer primary key autoincrement,
                dedupe_key text unique,
                layer text not null,
                entity text not null,
                from_state text,
                to_state text not null,
                reason text not null,
                source text not null,
                device_ts text not null,
                status text not null default 'pending',
                attempts integer not null default 0,
                last_reason text,
                quarantined_at integer,
                created_at integer not null default (strftime('%s','now'))
              );
              create table checkpoints (
                seq integer primary key autoincrement,
                device_ts text not null,
                nvr_state text not null,
                cameras_observed integer not null,
                cycle_ok integer not null default 1,
                status text not null default 'pending',
                attempts integer not null default 0,
                last_reason text,
                quarantined_at integer,
                created_at integer not null default (strftime('%s','now'))
              );
            """)
            db.execute(
                """insert into transitions(
                     dedupe_key,layer,entity,to_state,reason,source,device_ts
                   ) values ('old-id','camera','1','offline','probe_timeout','probe',
                             '2026-10-02T12:03:00Z')"""
            )
            db.execute(
                """insert into checkpoints(device_ts,nvr_state,cameras_observed,cycle_ok)
                   values ('2026-10-02T12:03:01Z','unknown',1,1)"""
            )
            db.commit()
        finally:
            db.close()

        store = HealthStore(path, "agent-1")
        try:
            cols = store._all_columns()
            assert "recorder_id" in cols["transitions"]
            assert "recorder_id" in cols["checkpoints"]
            batch = store.export_batch(20)
            assert len(batch["transitions"]) == 1
            assert len(batch["checkpoints"]) == 1
            assert "recorder_id" not in batch["transitions"][0]
            assert "recorder_id" not in batch["checkpoints"][0]
        finally:
            store.close()


def test_recorder_scope_does_not_change_epoch_dedupe_identity():
    with tempfile.TemporaryDirectory() as td:
        store = HealthStore(Path(td) / "health.sqlite", "agent-1")
        try:
            a = store.observe(
                "camera", "1", "operational", "ok", "probe",
                "2026-10-02T12:04:00Z",
                recorder_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            )
            b = store.observe(
                "camera", "1", "operational", "ok", "probe",
                "2026-10-02T12:04:00Z",
                recorder_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            )
            assert a["dedupe_key"] != b["dedupe_key"]
            assert a["dedupe_key"].startswith(f"agent-1:{store.epoch}:")
            assert b["dedupe_key"].startswith(f"agent-1:{store.epoch}:")
        finally:
            store.close()


def test_cutover_stamp_attributes_legacy_rows_and_last_state_atomically():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "health.sqlite"
        rid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        store = HealthStore(path, "agent-1")
        try:
            tx = store.observe(
                "camera", "1", "offline", "probe_timeout", "probe",
                "2026-10-02T12:05:00Z",
            )
            cp = store.checkpoint(
                "2026-10-02T12:05:01Z", "degraded", 1, True
            )
            assert tx and cp
            dedupe = tx["dedupe_key"]
            result = store.stamp_missing_recorder_id(rid)
            assert result["transitions"] == 1
            assert result["checkpoints"] == 1
            assert result["last_state"] == 1
            assert result["recorder_id"] == rid

            batch = store.export_batch(20)
            assert batch["transitions"][0]["recorder_id"] == rid
            assert batch["checkpoints"][0]["recorder_id"] == rid
            assert batch["transitions"][0]["id"] == dedupe

            keys = store.db.execute(
                "select key,state from last_state order by key"
            ).fetchall()
            assert [(r["key"], r["state"]) for r in keys] == [
                (f"{rid}:camera:1", "offline")
            ]

            # Re-running the cutover is idempotent and does not rewrite identity.
            again = store.stamp_missing_recorder_id(rid)
            assert again["transitions"] == 0
            assert again["checkpoints"] == 0
            assert again["last_state"] == 0
        finally:
            store.close()


def test_cutover_stamp_rejects_conflicting_provenance_without_partial_write():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "health.sqlite"
        target = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        other = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        store = HealthStore(path, "agent-1")
        try:
            store.observe(
                "camera", "1", "offline", "probe_timeout", "probe",
                "2026-10-02T12:06:00Z", recorder_id=other,
            )
            store.observe(
                "camera", "2", "offline", "probe_timeout", "probe",
                "2026-10-02T12:06:01Z",
            )
            store.checkpoint(
                "2026-10-02T12:06:02Z", "unknown", 2, True
            )

            try:
                store.stamp_missing_recorder_id(target)
                assert False, "conflicting recorder provenance must fail closed"
            except ValueError as exc:
                assert "another recorder" in str(exc)

            rows = store.db.execute(
                "select entity,recorder_id from transitions order by seq"
            ).fetchall()
            assert [(r["entity"], r["recorder_id"]) for r in rows] == [
                ("1", other),
                ("2", None),
            ]
            cp = store.db.execute(
                "select recorder_id from checkpoints order by seq"
            ).fetchone()
            assert cp["recorder_id"] is None
            keys = [r["key"] for r in store.db.execute(
                "select key from last_state order by key"
            ).fetchall()]
            assert f"{target}:camera:2" not in keys
        finally:
            store.close()


def test_cutover_stamp_rejects_invalid_target_uuid():
    with tempfile.TemporaryDirectory() as td:
        store = HealthStore(Path(td) / "health.sqlite", "agent-1")
        try:
            try:
                store.stamp_missing_recorder_id("not-a-uuid")
                assert False, "invalid cloud recorder id must fail"
            except ValueError as exc:
                assert "UUID" in str(exc)
        finally:
            store.close()
