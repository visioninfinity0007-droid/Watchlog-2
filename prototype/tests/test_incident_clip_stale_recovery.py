#!/usr/bin/env python3
"""Contract for server-side recovery of abandoned incident clip requests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "supabase" / "migrations" / "0142_stale_incident_clip_recovery.sql").read_text(
    encoding="utf-8"
).lower()


def test_stale_clip_finalizer_is_server_only_and_bounded():
    assert "wl_finalize_stale_incident_clips" in SQL
    assert "status = 'processing'" in SQL
    assert "started_at < now() - make_interval" in SQL
    assert "status = 'failed'" in SQL
    assert "claimed_by_agent_id = null" in SQL
    assert "delete from public.incident_clip_chunks" in SQL
    assert "revoke all on function public.wl_finalize_stale_incident_clips(integer)" in SQL
    assert "from public, anon, authenticated" in SQL
    assert "to service_role" in SQL


def test_stale_clip_finalizer_runs_independently_of_agent_polling():
    assert "watchlog-finalize-stale-incident-clips" in SQL
    assert "'*/2 * * * *'" in SQL
    assert "pg_available_extensions" in SQL
    assert "exception when others" in SQL


if __name__ == "__main__":
    tests = [v for k, v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} stale incident clip recovery contracts passed")
