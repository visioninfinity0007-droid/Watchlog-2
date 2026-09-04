#!/usr/bin/env python3
"""Contract tests for the WatchLog production preflight utility."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
TOOL=ROOT/"tools/watchlog_production_preflight.py"
spec=importlib.util.spec_from_file_location("watchlog_production_preflight",TOOL)
assert spec and spec.loader
preflight=importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def base_markers():
    return {
        "entitlement_0023":True,
        "reporting_0023":True,
        "monitoring_rules_0024":False,
        "site_type_0024":False,
        "camera_purpose_0024":False,
        "platform_admins_0029":False,
        "platform_me_0029":False,
        "analytic_key_0030":False,
        "recipient_destinations_0031":False,
        "site_health_0033":False,
        "billing_owner_policies_0036":False,
        "commercial_invoices_0037":False,
        "support_sessions_0037":False,
        "platform_commercial_0037":False,
        "account_status_0038":False,
        "my_account_0038":False,
        "account_lifecycle_0038":False,
    }


def set_through_0036(m):
    for key in (
        "monitoring_rules_0024","site_type_0024","camera_purpose_0024",
        "platform_admins_0029","platform_me_0029","analytic_key_0030",
        "recipient_destinations_0031","site_health_0033","billing_owner_policies_0036",
    ): m[key]=True


def set_0037(m):
    for key in ("commercial_invoices_0037","support_sessions_0037","platform_commercial_0037"):
        m[key]=True


def set_0038(m):
    for key in ("account_status_0038","my_account_0038","account_lifecycle_0038"):
        m[key]=True


def test_exact_0023_boundary():
    assert preflight.classify_boundary(base_markers())=="0023_exact_candidate"


def test_partial_after_0023_stops():
    m=base_markers();m["monitoring_rules_0024"]=True
    assert preflight.classify_boundary(m)=="partial_after_0023_stop_and_reconcile"


def test_exact_0036_boundary():
    m=base_markers();set_through_0036(m)
    assert preflight.classify_boundary(m)=="0036_exact_candidate"


def test_exact_0037_boundary():
    m=base_markers();set_through_0036(m);set_0037(m)
    assert preflight.classify_boundary(m)=="0037_exact_candidate"


def test_partial_after_0036_stops():
    m=base_markers();set_through_0036(m);m["commercial_invoices_0037"]=True
    assert preflight.classify_boundary(m)=="partial_after_0036_stop_and_reconcile"


def test_0038_candidate_requires_smoke():
    m=base_markers();set_through_0036(m);set_0037(m);set_0038(m)
    assert preflight.classify_boundary(m)=="0038_candidate_requires_authz_smoke"


def test_target_guard_accepts_watchlog():
    preflight.validate_target(preflight.EXPECTED_PROJECT_REF,f"db.{preflight.EXPECTED_PROJECT_REF}.supabase.co",f"postgres.{preflight.EXPECTED_PROJECT_REF}")
    preflight.validate_target(preflight.EXPECTED_PROJECT_REF,"aws-0-ap-south-1.pooler.supabase.com",f"postgres.{preflight.EXPECTED_PROJECT_REF}")


def test_target_guard_rejects_other_project():
    try:
        preflight.validate_target("jssitaduuhjvyznldfoc","db.jssitaduuhjvyznldfoc.supabase.co","postgres.jssitaduuhjvyznldfoc")
    except RuntimeError as exc:
        assert "expected WatchLog project" in str(exc)
    else: raise AssertionError("wrong Supabase project was not rejected")


def test_target_guard_rejects_mismatched_connection_metadata():
    try:
        preflight.validate_target(preflight.EXPECTED_PROJECT_REF,f"db.{preflight.EXPECTED_PROJECT_REF}.supabase.co","postgres.jssitaduuhjvyznldfoc")
    except RuntimeError as exc:
        assert "database user does not match" in str(exc)
    else: raise AssertionError("mismatched database user was not rejected")


def test_tool_is_structurally_read_only():
    source=TOOL.read_text(encoding="utf-8").lower()
    assert "begin transaction read only" in source
    assert 'conn.execute("rollback")' in source
    for token in ("create table ","alter table ","drop table ","truncate ","insert into ","delete from "):
        assert token not in source,f"preflight contains forbidden SQL token: {token}"


if __name__=="__main__":
    tests=[test_exact_0023_boundary,test_partial_after_0023_stops,test_exact_0036_boundary,test_exact_0037_boundary,test_partial_after_0036_stops,test_0038_candidate_requires_smoke,test_target_guard_accepts_watchlog,test_target_guard_rejects_other_project,test_target_guard_rejects_mismatched_connection_metadata,test_tool_is_structurally_read_only]
    for test in tests:test()
    print(f"OK: {len(tests)} production preflight contract tests passed")
