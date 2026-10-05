"""A broken legacy recorder credential on a registry site stops no recorder.

On a site whose recorders.json configures recorders, each recorder logs in with its own
DPAPI credential; the legacy nvr_credential.dpapi is only the continuity recorder's mirror
(rollback to 5.0.x). A corrupt legacy blob used to make Config.__init__ exit FATAL, which
stopped every recorder, and every boot probed and camera-synced the legacy ini recorder with
the legacy login and no identity check. The 5.0.x no-registry path stays unchanged.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402
from drivers import DriverError  # noqa: E402

LOCAL_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LOCAL_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class _NtOs:
    """``os`` as watchlog_agent sees it on Windows, where the credential store is read."""
    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


def _fake_crypto(monkeypatch):
    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(text[5:])

    monkeypatch.setattr(cs, "write_json_secret", wjs)
    monkeypatch.setattr(cs, "read_json_secret", rjs)


def _registry(monkeypatch, rows):
    _fake_crypto(monkeypatch)
    for row in rows:
        cs.save_recorder_credential(row["local_id"], f"user-{row['display_name']}",
                                    f"pw-{row['display_name']}")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": rows})


def _row(local_id, name, url, primary):
    return {"local_id": local_id, "cloud_recorder_id": None, "display_name": name,
            "url": url, "driver": "onvif", "is_primary": primary, "is_configured": True,
            "continuity_owner": primary}


def _corrupt_legacy(monkeypatch):
    def broken(_ini=None):
        raise cs.SecretError("legacy blob is corrupt")
    monkeypatch.setattr(core, "os", _NtOs())
    monkeypatch.setattr(cs, "load_nvr_credential", broken)


def _legacy_cfg(tmp_path):
    cfg = object.__new__(core.Config)
    cfg._ini_path = tmp_path / "watchlog.ini"
    cfg.nvr_url = "http://legacy"
    cfg.nvr_driver = "onvif"
    cfg.nvr_username = "legacy-user"
    cfg.nvr_password = "from-ini"
    cfg.state_path = tmp_path / "agent_state.json"
    cfg.spool_path = tmp_path / "spool.sqlite"
    cfg.health_store_path = tmp_path / "health.sqlite"
    cfg.last_live_path = tmp_path / "last_live.json"
    return cfg


def test_no_registry_keeps_the_fatal_5_0_x_behaviour(monkeypatch, tmp_path):
    _corrupt_legacy(monkeypatch)
    assert not rr.registry_path().exists()
    with pytest.raises(SystemExit, match="FATAL: the recorder credential could not be read"):
        _legacy_cfg(tmp_path).load_recorder_credential()


def test_registry_site_degrades_only_the_legacy_login(monkeypatch, tmp_path):
    _registry(monkeypatch, [_row(LOCAL_A, "a", "http://legacy", True),
                            _row(LOCAL_B, "b", "http://b", False)])
    _corrupt_legacy(monkeypatch)
    cfg = _legacy_cfg(tmp_path)
    cfg.load_recorder_credential()                  # no SystemExit: nothing is stopped
    assert cfg.legacy_credential_error == "SecretError"
    assert cfg.nvr_password == ""

    # The legacy Config never signs in with the empty login (no false "wrong password").
    def never(*_a, **_k):
        raise AssertionError("the recorder must not be contacted")
    monkeypatch.setattr(core, "autodetect", never)
    monkeypatch.setattr(core, "build", never)
    with pytest.raises(DriverError, match="legacy recorder login"):
        core.open_driver(cfg)

    # Every recorder, the continuity one included, still loads its own login.
    import recorder_runtime
    contexts = recorder_runtime.load_contexts(cfg, degrade_credential_errors=True)
    assert [(c.config.nvr_username, c.credential_error) for c in contexts] == [
        ("user-a", None), ("user-b", None)]


def test_registry_site_with_a_corrupt_continuity_login_degrades_only_that_recorder(
        monkeypatch, tmp_path):
    _registry(monkeypatch, [_row(LOCAL_A, "a", "http://legacy", True),
                            _row(LOCAL_B, "b", "http://b", False)])
    cs.recorder_credential_path(LOCAL_A).write_text("CORRUPT", encoding="utf-8")
    _corrupt_legacy(monkeypatch)
    cfg = _legacy_cfg(tmp_path)
    cfg.load_recorder_credential()
    import recorder_runtime
    a, b = recorder_runtime.load_contexts(cfg, degrade_credential_errors=True)
    assert a.continuity_owner and a.credential_error == "SecretError"
    assert b.credential_error is None and b.config.nvr_password == "pw-b"


def _run_main(monkeypatch, tmp_path):
    """Run watchlog_agent.main() to the run loop with a fake cloud; return what it did."""
    seen = {"opened": [], "cloud": [], "run": None}
    cfg = SimpleNamespace(
        supabase_url="https://example.invalid", publishable_key="pk", enrollment_code="",
        nvr_url="http://legacy", nvr_driver="onvif", nvr_username="legacy-user",
        nvr_password="legacy-pw", state_path=tmp_path / "agent_state.json",
        spool_path=tmp_path / "spool.sqlite", health_store_path=tmp_path / "health.sqlite",
        last_live_path=tmp_path / "last_live.json", require_cloud=lambda: None,
        require_nvr=lambda: None)

    class Driver:
        def list_channels(self):
            return [SimpleNamespace(channel="1", name="Gate")]

        def capabilities(self):
            return None

        def close(self):
            pass

    def open_driver(c):
        seen["opened"].append((c.nvr_url, c.nvr_username))
        return Driver(), SimpleNamespace(vendor="V", model="M", driver="onvif", serial=None)

    class Cloud:
        def __init__(self, *_a):
            pass

        def call(self, fn, **_kw):
            seen["cloud"].append(fn)
            return []

    monkeypatch.setattr(sys, "argv", ["watchlog-agent"])
    monkeypatch.setattr(core, "Config", lambda *_a, **_k: cfg)
    monkeypatch.setattr(core, "load_state", lambda _p: {"agent_id": "agent", "agent_key": "k"})
    monkeypatch.setattr(core, "Cloud", Cloud)
    monkeypatch.setattr(core, "open_driver", open_driver)
    monkeypatch.setattr(core, "cmd_run",
                        lambda *a, **k: seen.__setitem__("run", k))
    core.main()
    return seen


def test_no_registry_boot_probes_and_syncs_the_ini_recorder(monkeypatch, tmp_path):
    seen = _run_main(monkeypatch, tmp_path)
    assert seen["opened"] == [("http://legacy", "legacy-user")]
    assert seen["cloud"] == ["wl_sync_cameras"]
    assert seen["run"]["channels"] == [{"channel": "1", "name": "Gate"}]


def test_multi_recorder_boot_skips_the_legacy_probe_and_site_sync(monkeypatch, tmp_path):
    _registry(monkeypatch, [_row(LOCAL_A, "a", "http://legacy", True),
                            _row(LOCAL_B, "b", "http://b", False)])
    seen = _run_main(monkeypatch, tmp_path)
    assert seen["opened"] == []
    assert seen["cloud"] == []
    assert seen["run"]["device"] is None and seen["run"]["channels"] == []


def test_single_registry_recorder_boot_uses_its_own_login_and_address(monkeypatch, tmp_path):
    _registry(monkeypatch, [_row(LOCAL_A, "a", "http://a", True)])
    seen = _run_main(monkeypatch, tmp_path)
    assert seen["opened"] == [("http://a", "user-a")]
