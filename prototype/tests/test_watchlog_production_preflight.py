#!/usr/bin/env python3
"""Contract tests for the WatchLog production preflight utility."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "watchlog_production_preflight.py"

spec = importlib.util.spec_from_file_location("watchlog_production_preflight", TOOL)
assert spec and spec.loader
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def base_markers() -> dict[str, bool]:
    return {
        "entitlement_0023": True,
        "reporting_0023": True,
        "monitoring_rules_0024": False,
        "site_type_0024": False,
        "camera_purpose_0024": False,
        "platform_admins_0029": False,
        "platform_me_0029": False,
        "analytic_key_0030": False,
        "recipient_destinations_0031": False,
        "site_health_0033": False,
        "billing_owner_policies_0036": False,
    }


def test_exact_0023_boundary() -> None:
    assert preflight.classify_boundary(base_markers()) == "0023_exact_candidate"


def test_partial_boundary_stops() -> None:
    markers = base_markers()
    markers["monitoring_rules_0024"] = True
    assert (
        preflight.classify_boundary(markers)
        == "partial_after_0023_stop_and_reconcile"
    )


def test_0036_candidate_requires_smoke() -> None:
    markers = base_markers()
    for key in list(markers):
        markers[key] = True
    assert (
        preflight.classify_boundary(markers)
        == "0036_candidate_requires_authz_smoke"
    )


def test_target_guard_accepts_watchlog() -> None:
    preflight.validate_target(
        preflight.EXPECTED_PROJECT_REF,
        f"db.{preflight.EXPECTED_PROJECT_REF}.supabase.co",
        f"postgres.{preflight.EXPECTED_PROJECT_REF}",
    )
    preflight.validate_target(
        preflight.EXPECTED_PROJECT_REF,
        "aws-0-ap-south-1.pooler.supabase.com",
        f"postgres.{preflight.EXPECTED_PROJECT_REF}",
    )


def test_target_guard_rejects_other_project() -> None:
    try:
        preflight.validate_target(
            "jssitaduuhjvyznldfoc",
            "db.jssitaduuhjvyznldfoc.supabase.co",
            "postgres.jssitaduuhjvyznldfoc",
        )
    except RuntimeError as exc:
        assert "expected WatchLog project" in str(exc)
    else:
        raise AssertionError("wrong Supabase project was not rejected")


def test_target_guard_rejects_mismatched_connection_metadata() -> None:
    try:
        preflight.validate_target(
            preflight.EXPECTED_PROJECT_REF,
            f"db.{preflight.EXPECTED_PROJECT_REF}.supabase.co",
            "postgres.jssitaduuhjvyznldfoc",
        )
    except RuntimeError as exc:
        assert "database user does not match" in str(exc)
    else:
        raise AssertionError("mismatched database user was not rejected")


def test_tool_is_structurally_read_only() -> None:
    source = TOOL.read_text(encoding="utf-8").lower()
    assert "begin transaction read only" in source
    assert "conn.execute(\"rollback\")" in source
    forbidden = (
        "create table ",
        "alter table ",
        "drop table ",
        "truncate ",
        "insert into ",
        "delete from ",
    )
    for token in forbidden:
        assert token not in source, f"preflight contains forbidden SQL token: {token}"


if __name__ == "__main__":
    tests = [
        test_exact_0023_boundary,
        test_partial_boundary_stops,
        test_0036_candidate_requires_smoke,
        test_target_guard_accepts_watchlog,
        test_target_guard_rejects_other_project,
        test_target_guard_rejects_mismatched_connection_metadata,
        test_tool_is_structurally_read_only,
    ]
    for test in tests:
        test()
    print(f"OK: {len(tests)} production preflight contract tests passed")
