#!/usr/bin/env python3
"""Tenant isolation: a site's queued events and health never upload as another site.

Setup re-run on an enrolled PC with ANOTHER site's code quarantined only the recorder registry;
spool.sqlite, health.sqlite and last_live.json stayed, and queued rows (channels, not cameras)
would have been ingested as the new site (credential-invariants audit G9-a). site_runtime
stamps the data with its site; the Agent sets another site's data aside before opening any
queue; Setup stamps an older install's unstamped data with the prior site first.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import site_runtime  # noqa: E402


def _seed(d: Path):
    for name in ("spool.sqlite", "spool.sqlite-wal", "health.sqlite", "last_live.json",
                 "analytics_spool.sqlite", "analytics_bootstrap_sent.json"):
        (d / name).write_text("site A data", encoding="utf-8")
    (d / "recorder_identity.json").write_text("{}", encoding="utf-8")


def test_another_sites_data_is_set_aside_not_deleted_and_never_reopened(tmp_path):
    _seed(tmp_path)
    site_runtime.write_stamp(tmp_path, "site-A", "tenant-A")
    moved = site_runtime.ensure_runtime_belongs(tmp_path, "site-B", "tenant-B")
    assert {p.name.split(".site-")[0] for p in moved} == {
        "spool.sqlite", "spool.sqlite-wal", "health.sqlite", "last_live.json",
        "analytics_spool.sqlite", "analytics_bootstrap_sent.json"}
    assert all(p.exists() and p.read_text(encoding="utf-8") == "site A data" for p in moved)
    assert not (tmp_path / "spool.sqlite").exists()
    assert (tmp_path / "recorder_identity.json").exists()   # Setup's fresh recorder identity
    stamp = json.loads((tmp_path / site_runtime.STAMP_NAME).read_text(encoding="utf-8"))
    assert stamp["site_id"] == "site-B"


def test_the_same_site_keeps_its_backlog(tmp_path):
    _seed(tmp_path)
    site_runtime.write_stamp(tmp_path, "site-A", "tenant-A")
    assert site_runtime.ensure_runtime_belongs(tmp_path, "site-A", "tenant-A") == []
    assert (tmp_path / "spool.sqlite").exists()


def test_an_older_agents_unstamped_queue_is_adopted_on_a_same_site_upgrade(tmp_path):
    _seed(tmp_path)
    assert site_runtime.ensure_runtime_belongs(tmp_path, "site-A", "tenant-A") == []
    assert (tmp_path / "spool.sqlite").exists()


def test_setup_moving_an_older_install_to_another_site_catches_its_queue(tmp_path):
    _seed(tmp_path)
    assert site_runtime.stamp_prior_site(tmp_path, {"site_id": "site-A", "tenant_id": "tenant-A"})
    moved = site_runtime.ensure_runtime_belongs(tmp_path, "site-B", "tenant-B")
    assert moved and not (tmp_path / "spool.sqlite").exists()


def test_an_unreadable_identity_still_names_the_prior_site(tmp_path):
    _seed(tmp_path)
    (tmp_path / "agent_state.json").write_text(json.dumps({"site_id": "site-A"}), encoding="utf-8")
    assert site_runtime.stamp_prior_site(tmp_path, None)
    assert site_runtime.ensure_runtime_belongs(tmp_path, "site-B", None)


def test_data_with_no_identity_at_all_is_never_adopted_by_a_new_site(tmp_path):
    _seed(tmp_path)                               # e.g. left after an uninstall
    assert site_runtime.stamp_prior_site(tmp_path, None)
    assert site_runtime.ensure_runtime_belongs(tmp_path, "site-B", None)
    assert not (tmp_path / "spool.sqlite").exists()


def test_the_agent_checks_before_opening_any_queue():
    text = (ROOT / "agent" / "watchlog_agent.py").read_text(encoding="utf-8")
    check = text.index("site_runtime.ensure_runtime_belongs(cfg.state_path.parent")
    assert check < text.index("    cmd_run(cfg, state, cloud, once=args.once")
    setup = (ROOT / "agent" / "setup_backend.py").read_text(encoding="utf-8")
    assert setup.index("site_runtime.stamp_prior_site(") < setup.index(
        "state = establish_identity(cloud, state_path, enrollment_code, device, progress)")
