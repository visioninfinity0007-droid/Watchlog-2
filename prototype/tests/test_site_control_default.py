#!/usr/bin/env python3
"""Site Control is ON by default on the PC (5.1.3); the owner's cloud switch is the gate.

A site must be remotely testable without anyone editing watchlog.ini on the customer's PC.
The agent therefore polls for Site Control commands unless the ini explicitly opts out, and
wl_agent_claim_command hands it nothing while sites.site_control_enabled is off (default).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import agent_core  # noqa: E402

BASE = """[watchlog]
supabase_url = https://example.supabase.co
supabase_publishable_key = sb_publishable_test
nvr_url = http://192.0.2.10
nvr_username = admin
nvr_password = test
"""


@pytest.fixture(autouse=True)
def _no_credential_store(monkeypatch):
    # The recorder credential lives in the DPAPI store on Windows; not under test here.
    monkeypatch.setattr(agent_core.Config, "load_recorder_credential", lambda self: None)


def _cfg(tmp_path, monkeypatch, extra=""):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    for key in list(os.environ):
        if key.startswith("WATCHLOG_"):
            monkeypatch.delenv(key, raising=False)
    ini = tmp_path / "watchlog.ini"
    ini.write_text(BASE + extra, encoding="utf-8")
    return agent_core.Config(ini)


def test_absent_setting_means_on(tmp_path, monkeypatch):
    assert _cfg(tmp_path, monkeypatch).site_control_enabled is True


@pytest.mark.parametrize("value", ["false", "False", " FALSE "])
def test_explicit_false_opts_the_pc_out(tmp_path, monkeypatch, value):
    assert _cfg(tmp_path, monkeypatch, f"site_control = {value}\n").site_control_enabled is False


@pytest.mark.parametrize("value", ["true", "True", "yes", ""])
def test_anything_else_stays_on(tmp_path, monkeypatch, value):
    assert _cfg(tmp_path, monkeypatch, f"site_control = {value}\n").site_control_enabled is True


def test_environment_can_opt_out(tmp_path, monkeypatch):
    cfg_dir = tmp_path
    monkeypatch.setenv("WATCHLOG_SITE_CONTROL", "false")
    monkeypatch.setenv("PROGRAMDATA", str(cfg_dir))
    ini = cfg_dir / "watchlog.ini"
    ini.write_text(BASE, encoding="utf-8")
    assert agent_core.Config(ini).site_control_enabled is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
