"""Registry trust (MNVR-043 registry part).

%ProgramData%\\WatchLog is not ACL-hardened yet (installer package). Until it is:
* the registry is published through an unpredictable, exclusively created temp
  file, so a pre-created recorders.json.tmp cannot hijack the write;
* a registry a non-administrator could have planted is rejected as untrusted;
* a malformed or untrusted registry fails that recorder set closed with a clear
  local status instead of crash-looping the Agent through its launcher.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402

FOREIGN_USER = "S-1-5-21-1111111111-2222222222-3333333333-1001"


class _Env:
    def __enter__(self):
        self.saved = os.environ.get("PROGRAMDATA")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self.tmp.name
        self.saved_crypto = (cs.write_json_secret, cs.read_json_secret)

        def wjs(path, obj):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

        def rjs(path):
            text = Path(path).read_text(encoding="utf-8")
            if not text.startswith("JSON:"):
                raise ws.SecretError("corrupt")
            return json.loads(text[5:])

        cs.write_json_secret, cs.read_json_secret = wjs, rjs
        self.root = Path(self.tmp.name) / "WatchLog"
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *_args):
        cs.write_json_secret, cs.read_json_secret = self.saved_crypto
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def _one_row():
    local_id = str(uuid.uuid4())
    cs.save_recorder_credential(local_id, "admin", "pw")
    return {"schema": rr.REGISTRY_SCHEMA, "recorders": [{
        "local_id": local_id, "display_name": "Recorder", "url": "http://192.0.2.10",
        "driver": "hikvision", "is_primary": True, "is_configured": True,
    }]}


def _owner(monkeypatch, owner_sid, *, current="S-1-5-18", admin=False):
    monkeypatch.setattr(rr, "_file_owner_sid", lambda _path: owner_sid)
    monkeypatch.setattr(rr, "_current_user_sid", lambda: current)
    monkeypatch.setattr(rr, "_is_local_admin", lambda _sid: admin)


def test_a_planted_temp_file_cannot_hijack_the_registry_write():
    with _Env():
        planted = rr.registry_path().with_suffix(".json.tmp")
        planted.write_text("attacker-controlled", encoding="utf-8")

        rr.save_registry(_one_row())

        assert planted.read_text(encoding="utf-8") == "attacker-controlled"
        assert len(rr.recorders()) == 1
        leftovers = [p.name for p in rr.registry_path().parent.iterdir()
                     if p.name not in ("recorders.json", "recorders.json.tmp", "Secrets")]
        assert leftovers == []


def test_registry_owned_by_a_standard_user_is_untrusted(monkeypatch):
    with _Env():
        rr.save_registry(_one_row())
        _owner(monkeypatch, FOREIGN_USER, admin=False)
        with pytest.raises(rr.RegistryUntrusted, match="administrator"):
            rr.load_registry()
        # Untrusted is still a registry error for every existing caller.
        assert issubclass(rr.RegistryUntrusted, ValueError)


@pytest.mark.parametrize("owner,current,admin", [
    ("S-1-5-18", "S-1-5-18", False),                 # written by the SYSTEM Agent
    ("S-1-5-32-544", "S-1-5-18", False),             # owned by Administrators
    (FOREIGN_USER, "S-1-5-18", True),                # an administrator account (elevated Setup)
    (FOREIGN_USER, FOREIGN_USER, False),             # the reader's own file: no boundary crossed
    (None, "S-1-5-18", False),                       # owner unknown: best effort, not blocked
])
def test_trusted_or_unknown_owners_load(monkeypatch, owner, current, admin):
    with _Env():
        rr.save_registry(_one_row())
        _owner(monkeypatch, owner, current=current, admin=admin)
        assert len(rr.recorders()) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows owner lookup")
def test_windows_owner_lookup_sees_this_process_as_owner():
    with _Env():
        rr.save_registry(_one_row())
        owner = rr._file_owner_sid(rr.registry_path())
        assert owner and owner.startswith("S-1-")
        assert rr.registry_owner_trusted(rr.registry_path()) is True


def _agent_cfg(root: Path):
    return SimpleNamespace(state_path=root / "agent_state.json", spool_path=root / "spool.sqlite",
                           health_store_path=root / "health.sqlite",
                           last_live_path=root / "last_live.json")


class _NoCloud:
    def call(self, name, **_kw):
        raise AssertionError(f"no cloud call may be made while the registry is held: {name}")


def test_untrusted_registry_holds_the_agent_instead_of_crash_looping(monkeypatch):
    with _Env() as env:
        rr.save_registry(_one_row())
        _owner(monkeypatch, FOREIGN_USER, admin=False)
        health, sleeps = [], []
        monkeypatch.setattr(core, "update_runtime_health", lambda **kw: health.append(kw))
        monkeypatch.setattr(analytics_agent, "REGISTRY_RECHECK_SECONDS", 0.01)

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 3:                     # Setup repairs/quarantines it
                _owner(monkeypatch, "S-1-5-18")

        monkeypatch.setattr(analytics_agent.time, "sleep", fake_sleep)

        with pytest.raises(SystemExit) as exit_info:
            analytics_agent.enhanced_cmd_run(_agent_cfg(env.root), {"agent_id": "a"},
                                             _NoCloud(), once=False)

        assert len(sleeps) == 3                      # held in-process, not relaunched
        assert "valid again" in str(exit_info.value)
        assert health and health[0]["recorder_registry"] == "needs_repair"
        assert health[-1]["recorder_registry"] == "ok"


def test_malformed_registry_is_held_with_a_clear_status(monkeypatch):
    with _Env() as env:
        rr.registry_path().write_text("{not json", encoding="utf-8")
        logs, health, sleeps = [], [], []
        monkeypatch.setattr(core, "log", lambda msg: logs.append(str(msg)))
        monkeypatch.setattr(core, "update_runtime_health", lambda **kw: health.append(kw))
        monkeypatch.setattr(analytics_agent, "REGISTRY_RECHECK_SECONDS", 0.01)

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:                     # Setup quarantined the file
                rr.registry_path().unlink()

        monkeypatch.setattr(analytics_agent.time, "sleep", fake_sleep)

        with pytest.raises(SystemExit):
            analytics_agent.enhanced_cmd_run(_agent_cfg(env.root), {"agent_id": "a"},
                                             _NoCloud(), once=False)

        assert len(sleeps) == 2
        assert health[0]["recorder_registry"] == "needs_repair"
        assert any("recorder configuration" in line and "Setup" in line for line in logs)
