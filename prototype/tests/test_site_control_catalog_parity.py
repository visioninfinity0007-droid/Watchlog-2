#!/usr/bin/env python3
"""Site Control catalog parity between the database and the Agent (MNVR-069).

The database must never accept a Site Control action that the Agent cannot run.
A read action the Agent does not implement is claimed and then always completed
as failed with `unsupported_read_action`, so the server catalog would promise
something WatchLog cannot deliver.

This compares the FINAL migration chain (the last migration that defines each
function wins) with prototype/agent/site_control.py:
  * the read catalog in wl_site_command_enqueue == site_control.READ_ACTIONS;
  * the safe-write catalog in wl_site_command_propose_write maps exactly the
    Agent's WRITE_ACTIONS to the Agent's WRITE_CAPABILITY keys.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
sys.path.insert(0, str(ROOT / "agent"))

import site_control  # noqa: E402


def _final_definition(function: str) -> tuple[str, str]:
    """Return (file name, body) of the last migration that defines `function`."""
    pattern = re.compile(
        r"create\s+or\s+replace\s+function\s+public\." + re.escape(function) + r"\s*\(",
        re.I,
    )
    found = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        starts = [m.start() for m in pattern.finditer(text)]
        if starts:
            found = (path.name, text, starts[-1])
    assert found, f"no migration defines public.{function}"
    name, text, start = found
    # The body ends at the next top-level `create or replace function`.
    nxt = re.search(r"\ncreate\s+or\s+replace\s+function\s", text[start + 1:], re.I)
    end = start + 1 + nxt.start() if nxt else len(text)
    return name, text[start:end]


def _read_catalog() -> tuple[str, set[str]]:
    name, body = _final_definition("wl_site_command_enqueue")
    m = re.search(r"p_action\s+not\s+in\s*\((.*?)\)", body, re.I | re.S)
    assert m, f"{name}: wl_site_command_enqueue has no read-catalog check"
    return name, set(re.findall(r"'([a-z_]+)'", m.group(1)))


def _write_catalog() -> tuple[str, dict[str, str]]:
    name, body = _final_definition("wl_site_command_propose_write")
    m = re.search(r"v_cap\s*:=\s*case\s+p_action(.*?)\bend\b", body, re.I | re.S)
    assert m, f"{name}: wl_site_command_propose_write has no safe-write catalog"
    pairs = re.findall(r"when\s+'([a-z_]+)'\s+then\s+'([a-z_]+)'", m.group(1), re.I)
    return name, dict(pairs)


def test_database_read_catalog_matches_agent_read_actions():
    name, db_actions = _read_catalog()
    agent_actions = set(site_control.READ_ACTIONS)
    assert db_actions, f"{name}: empty read catalog"
    assert db_actions - agent_actions == set(), (
        f"{name} accepts read actions the Agent cannot run: "
        f"{sorted(db_actions - agent_actions)}"
    )
    assert agent_actions - db_actions == set(), (
        f"Agent read actions missing from {name}: {sorted(agent_actions - db_actions)}"
    )


def test_every_database_read_action_executes_on_the_agent():
    """No action in the server catalog ends as unsupported_read_action."""
    class Driver:
        def probe(self):
            return None

        def list_channels(self):
            return []

        def get_clock(self):
            return {"supported": False}

        def current_faults(self):
            return {"supported": False}

        def capabilities(self):
            return {"channels": []}

        def recording_status(self, _channel):
            return {"supported": False}

        def storage_status(self):
            return {"supported": False}

        def get_snapshot(self, _channel):
            return None

    _, db_actions = _read_catalog()
    for action in sorted(db_actions):
        out = site_control.execute_read(Driver(), action, {"channel": "1"})
        assert out.get("error") != "unsupported_read_action", (action, out)


def test_database_write_catalog_matches_agent_write_plane():
    name, db_writes = _write_catalog()
    assert set(db_writes) == set(site_control.WRITE_ACTIONS), (
        name, sorted(db_writes), sorted(site_control.WRITE_ACTIONS))
    assert db_writes == dict(site_control.WRITE_CAPABILITY), (
        name, db_writes, dict(site_control.WRITE_CAPABILITY))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} Site Control catalog parity checks passed")
