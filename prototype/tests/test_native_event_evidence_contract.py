#!/usr/bin/env python3
"""Static contract for migration 0164 (native-event evidence, timing, attribution, leases).

A later migration that redefines a function silently reverts earlier fixes to it. These checks
read the LATEST definition of each 0164 function across every migration, so a later file that
drops the post-roll guard, the 420 s lease or the server checksum fails here before any
database runs. Behaviour is proven on Postgres by e2e_native_event_evidence_pg.py.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
SQL_0164 = (MIGRATIONS / "0164_native_event_evidence.sql").read_text(encoding="utf-8").lower()


def latest_definition(function: str) -> tuple[str, str]:
    """(file name, body) of the last `create or replace function public.<function>`."""
    found = None
    head = re.compile(r"create\s+or\s+replace\s+function\s+public\." + re.escape(function)
                      + r"\s*\(", re.I)
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for match in head.finditer(text):
            rest = text[match.start():]
            tag = re.search(r"as\s+(\$[a-z_]*\$)", rest, re.I)
            if not tag:
                continue
            start = tag.end()
            end = rest.find(tag.group(1), start)
            found = (path.name, rest[:end + len(tag.group(1))].lower())
    assert found, f"{function} is not defined by any migration"
    return found


def test_claim_waits_for_the_recorded_post_roll_and_keeps_attribution():
    name, body = latest_definition("wl_agent_claim_clip_requests")
    assert name >= "0164", name
    compact = " ".join(body.split())
    assert "r.end_at <= now() - interval '10 seconds'" in compact
    assert "(r.recorder_id is null or r.recorder_id=c.recorder_id)" in compact
    assert "c.recorder_id is distinct from r.recorder_id" in compact
    assert "'clock_source',e.payload->'clock_source'" in compact
    # Everything 0150 established is still there.
    for kept in ("wl_assert_current_agent_authority", "for update of r skip locked",
                 "started_at=now()", "'recorder_id',c.recorder_id",
                 "could not confirm which recorder input"):
        assert kept in compact, kept


def test_stale_clip_lease_is_above_the_driver_worst_case():
    name, body = latest_definition("wl_finalize_stale_incident_clips")
    assert name >= "0164", name
    assert "p_stale_seconds integer default 420" in body
    assert "least(greatest(coalesce(p_stale_seconds, 420), 420), 3600)" in body
    # Hikvision worst case: 120 s lock wait + 90 s export + 2 x 60 s remux = 330 s.
    import sys
    sys.path.insert(0, str(ROOT / "agent"))
    import hikvision_archive as ha
    import incident_evidence as ie
    worst = (ha.CLIP_LOCK_WAIT_SECONDS + ha.CLIP_TOTAL_SECONDS
             + 2 * ie.REMUX_TIMEOUT_SECONDS)
    assert 420 >= worst + 60, worst
    assert "'select public.wl_finalize_stale_incident_clips(420)'" in SQL_0164
    assert "watchlog-finalize-stale-incident-clips" in SQL_0164


def test_completion_recomputes_the_checksum_over_the_stored_chunks():
    name, body = latest_definition("wl_agent_complete_clip")
    assert name >= "0164", name
    compact = " ".join(body.split())
    assert "sha256(string_agg(data,''::bytea order by sequence_no))" in compact
    assert "v_server_sha is distinct from p_sha256" in compact
    mismatch = compact.split("v_server_sha is distinct from p_sha256", 1)[1]
    mismatch = mismatch.split("end if;", 1)[0]
    assert "delete from public.incident_clip_chunks" in mismatch
    assert "status='failed'" in mismatch


def test_native_event_clips_are_off_by_default_and_trigger_based():
    assert ("add column if not exists native_event_clips_enabled boolean not null "
            "default false") in " ".join(SQL_0164.split())
    assert "after insert on public.events" in SQL_0164
    assert "when (new.camera_id is not null)" in SQL_0164
    # wl_ingest_events is not redefined by 0164.
    assert "function public.wl_ingest_events" not in SQL_0164
    _name, body = latest_definition("wl_native_event_clip_request")
    compact = " ".join(body.split())
    assert "new.device_ts - interval '15 seconds'" in compact
    assert "new.device_ts + interval '30 seconds'" in compact
    assert "interval '120 seconds'" in compact
    assert "v_camera_recorder<>new.recorder_id" in compact
    assert "exception when others" in compact


def test_snapshot_capture_provenance_is_recorded():
    assert "add column if not exists capture_source text not null default 'event_time'" \
        in " ".join(SQL_0164.split())
    _name, body = latest_definition("wl_snapshot_capture_provenance")
    assert "snapshot_captured_at" in body and "'live_after_event'" in body


if __name__ == "__main__":
    tests = [v for k, v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} native-event evidence contracts passed")
