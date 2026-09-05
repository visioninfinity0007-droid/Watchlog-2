#!/usr/bin/env python3
"""Contract/unit checks for recorder-native AI and on-demand incident evidence."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "prototype" / "agent"
sys.path.insert(0, str(AGENT))

from drivers.native_recorder import NativeDahuaDriver, NativeHikvisionDriver  # noqa: E402


def test_dahua_native_smd_and_ivs_provenance():
    driver = NativeDahuaDriver("http://127.0.0.1", "admin", "x", timeout=1)
    smart = driver._parse_line("Code=SmartMotionHuman;action=Start;index=0;data={}")
    assert smart is not None
    assert smart.event_type == "person"
    assert smart.payload["native_ai"] is True
    assert smart.payload["source"] == "recorder_native_ai"
    assert smart.payload["native_code"] == "SmartMotionHuman"
    generic = driver._parse_line("Code=VideoMotion;action=Start;index=1;data={}")
    assert generic is not None and generic.event_type == "motion"
    assert not generic.payload.get("native_ai")
    assert generic.payload["source"] == "recorder_event"
    driver.close()


def test_hikvision_native_smart_event_provenance():
    driver = NativeHikvisionDriver("http://127.0.0.1", "admin", "x", timeout=1)
    raw = b"""<?xml version='1.0'?>
    <EventNotificationAlert>
      <eventType>lineDetection</eventType><eventState>active</eventState>
      <channelID>1</channelID><dateTime>2026-09-05T10:00:00Z</dateTime>
      <activePostCount>1</activePostCount>
    </EventNotificationAlert>"""
    event = driver._parse_alert(raw)
    assert event is not None and event.event_type == "line_crossing"
    assert event.payload["native_ai"] is True
    assert event.payload["source"] == "recorder_native_ai"
    driver.close()


def test_packaged_collector_prefers_native_ai():
    src = (AGENT / "native_event_collector.py").read_text(encoding="utf-8")
    assert 'native_ai = bool((ev.payload or {}).get("native_ai"))' in src
    native_block = src.split("if native_ai:", 1)[1].split("elif detector", 1)[0]
    assert "classify_event" not in native_block
    assert "recorder-native AI" in native_block
    assert "classify_event(raw)" in src


def test_dahua_clip_is_on_demand_and_bounded():
    src = (AGENT / "drivers" / "native_recorder.py").read_text(encoding="utf-8")
    compact = src.replace(" ", "")
    assert "/cgi-bin/loadfile.cgi" in src
    assert '"action":"startLoad"' in compact
    assert "CLIP_MAX_BYTES=32*1024*1024" in compact
    assert "getCurrentTime" in src
    assert "continuous" in src.lower()


def test_clip_migrations_are_fail_closed_short_lived_and_physically_pruned():
    migrations = ROOT / "prototype" / "supabase" / "migrations"
    sql40 = (migrations / "0040_incident_footage_requests.sql").read_text(encoding="utf-8")
    sql41 = (migrations / "0041_incident_clip_fail_authz_hardening.sql").read_text(encoding="utf-8")
    lower = sql40.lower()
    for table in ("incident_clip_requests", "incident_clip_chunks"):
        assert f"alter table public.{table} enable row level security" in lower
        assert f"revoke all on table public.{table}" in lower
    for rpc in (
        "wl_request_incident_clip","wl_incident_clip_status","wl_incident_clip_chunk",
        "wl_agent_claim_clip_requests","wl_agent_upload_clip_chunk",
        "wl_agent_complete_clip","wl_agent_fail_clip",
    ):
        assert rpc in sql40
    assert "wl_require_role(array['owner','admin'])" in sql40
    assert "32 mb pilot limit" in lower and "24 hours" in lower and "60 seconds" in lower
    assert "recorder_url" not in lower and "recorder_password" not in lower
    hardened = sql41.lower()
    assert "clip request not claimed by this agent" in hardened
    assert "delete from public.incident_clip_chunks" in hardened
    assert "r.expires_at<=now()" in hardened
    # Claim ownership is resolved before failure-path media deletion.
    assert hardened.index("select * into v_request") < hardened.index("delete from public.incident_clip_chunks")


def test_release_entrypoint_activates_both_policies():
    src = (AGENT / "release_agent.py").read_text(encoding="utf-8")
    assert "app.core.collector = native_event_collector.collector" in src
    assert "incident_evidence.wrap_cmd_run" in src
    assert 'if explicit_setup:' in src
    assert "_setup_validation_complete" in src


if __name__ == "__main__":
    tests = [value for name, value in globals().copy().items()
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} native-NVR/incident-evidence checks passed")
