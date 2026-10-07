#!/usr/bin/env python3
"""Migration 0163 (remote acceptance) static contract, no database.

* wl_site_command_enqueue is the 0150 definition restated: the ONLY difference is the read
  catalog gaining the Agent's remote-maintenance actions; the deny-list regex, the read-tier
  gate, the tenant check and the recorder routing are byte-identical;
* wl_agent_complete_command is not redefined (its signature stays the 0064 one); the run is
  recorded by a trigger on the command's completion;
* the new RPCs are authenticated-facing only, owner/admin gated in the site's own account,
  and acceptance_runs is RLS-protected and read-only for members.
The real-Postgres behaviour is e2e_site_acceptance_pg.py.
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
sys.path.insert(0, str(ROOT / "agent"))

import site_control  # noqa: E402

M0150 = MIGRATIONS / "0150_multi_recorder_camera_job_routing.sql"
M0163 = MIGRATIONS / "0163_remote_acceptance_test.sql"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _function(text: str, name: str) -> str:
    start = text.index(f"create or replace function public.{name}(")
    nxt = re.search(r"\ncreate\s+or\s+replace\s+function\s|\n-- -{20,}", text[start + 1:])
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


def test_enqueue_is_0150_verbatim_except_the_read_catalog():
    old = _function(_text(M0150), "wl_site_command_enqueue").splitlines()
    new = _function(_text(M0163), "wl_site_command_enqueue").splitlines()
    removed = [l for l in difflib.ndiff(old, new) if l.startswith("- ")]
    added = [l for l in difflib.ndiff(old, new) if l.startswith("+ ")]
    assert removed == ["-     'get_storage_status','request_snapshot','inspect_recorder'"], removed
    assert added == [
        "+     'get_storage_status','request_snapshot','inspect_recorder',",
        "+     'run_full_acceptance_test','run_recording_check','run_archive_check',",
        "+     'collect_diagnostics','refresh_inventory','refresh_capabilities',",
        "+     'reconnect_recorder','restart_agent'",
    ], added


def test_deny_list_is_unchanged_and_blocks_none_of_the_new_actions():
    deny_line = re.compile(r"if p_action ~\* '(.*?)' then")
    old = deny_line.search(_function(_text(M0150), "wl_site_command_enqueue")).group(1)
    new = deny_line.search(_function(_text(M0163), "wl_site_command_enqueue")).group(1)
    assert old == new == ("(firmware|format|factory|reset|reboot|deleterec|delete_rec|adduser"
                          "|user_|network_|password|wipe|erase)")
    deny = re.compile(new, re.I)
    assert not [a for a in site_control.MAINTENANCE_ACTIONS if deny.search(a)]


def test_enqueue_grants_are_unchanged():
    tail = _text(M0163)
    assert ("revoke all on function public.wl_site_command_enqueue(\n  uuid,text,jsonb,text,text\n"
            ") from public,anon,authenticated,service_role;") in tail
    assert ("grant execute on function public.wl_site_command_enqueue(\n  uuid,text,jsonb,text,"
            "text\n) to authenticated,service_role;") in tail


def test_completion_rpc_is_not_redefined_and_a_trigger_records_runs():
    text = _text(M0163)
    assert "function public.wl_agent_complete_command" not in text
    assert re.search(r"create trigger site_commands_record_acceptance_run\s+after update of "
                     r"status on public\.site_commands", text)
    assert "new.action = 'run_full_acceptance_test'" in text
    assert "new.status = 'succeeded'" in text


def test_new_rpcs_are_owner_admin_gated_and_authenticated_only():
    text = _text(M0163)
    run = _function(text, "wl_site_run_acceptance")
    assert "public.wl_is_member(v_tenant)" in run
    assert "m.tenant_id=v_tenant" in run and "array['owner','admin']" in run
    assert "wl_my_role()" not in run.replace("account-blind wl_my_role()", "")
    assert "wl_current_site_agent(p_site_id)" in run
    assert "'run_full_acceptance_test'" in run and "'read'" in run
    result = _function(text, "wl_site_acceptance_result")
    assert "public.wl_is_member(v_cmd.tenant_id)" in result
    for sig in ("wl_site_run_acceptance(uuid,uuid)", "wl_site_acceptance_result(uuid)"):
        assert (f"revoke all on function public.{sig}\n  from public,anon,authenticated,"
                "service_role;") in text
        assert f"grant execute on function public.{sig}\n  to authenticated,service_role;" in text


def test_acceptance_runs_is_rls_protected_and_read_only():
    text = _text(M0163)
    assert "alter table public.acceptance_runs enable row level security;" in text
    assert "revoke all on table public.acceptance_runs from public, anon, authenticated;" in text
    assert "grant select on table public.acceptance_runs to authenticated;" in text
    assert "using (public.wl_is_member(tenant_id));" in text
    assert not re.search(r"grant\s+(insert|update|delete|all)\s+on\s+table\s+public\."
                         r"acceptance_runs", text, re.I)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
