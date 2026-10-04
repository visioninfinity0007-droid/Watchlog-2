"""Post-install Manage Recorders lifecycle contracts for the consolidated 5.1 runtime."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import setup_backend as sb  # noqa: E402
import windows_secret as ws  # noqa: E402


def _install_fake_crypto():
    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        raw = Path(path).read_text(encoding="utf-8")
        if not raw.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(raw[5:])

    cs.write_json_secret = wjs
    cs.read_json_secret = rjs


class _Env:
    def __enter__(self):
        self.saved = os.environ.get("PROGRAMDATA")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self.tmp.name
        _install_fake_crypto()
        self.root = Path(self.tmp.name) / "WatchLog"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ini = self.root / "watchlog.ini"
        self.ini.write_text("[watchlog]\n", encoding="utf-8")
        return self

    def __exit__(self, *_args):
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def _seed_two():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    ca, cb = str(uuid.uuid4()), str(uuid.uuid4())
    cs.save_recorder_credential(a, "user-a", "pw-a")
    cs.save_recorder_credential(b, "user-b", "pw-b")
    rr.save_registry({
        "schema": rr.REGISTRY_SCHEMA,
        "recorders": [
            {
                "local_id": a, "cloud_recorder_id": ca,
                "display_name": "Recorder A", "url": "http://192.0.2.10",
                "driver": "onvif", "is_primary": True,
                "continuity_owner": True, "is_configured": True,
            },
            {
                "local_id": b, "cloud_recorder_id": cb,
                "display_name": "Recorder B", "url": "http://192.0.2.20",
                "driver": "onvif", "is_primary": False,
                "continuity_owner": False, "is_configured": True,
            },
        ],
    })
    return a, b


class _LifecycleCloud:
    """Contract-v4 cloud that echoes the registry's own recorder bindings."""

    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_multi_recorder_agent_contract":
            return {"ok": True, "version": sb.MULTI_RECORDER_SETUP_CONTRACT_VERSION,
                    "features": sorted(sb.MULTI_RECORDER_SETUP_FEATURES)}
        if name == "wl_sync_recorders":
            bound = {row["local_id"]: row["cloud_recorder_id"] for row in rr.recorders()}
            return {row["local_key"]: bound[row["local_key"]] for row in kw["p_recorders"]}
        raise AssertionError(name)


def _activation_ok(monkeypatch):
    monkeypatch.setattr(
        sb, "ensure_background_agent",
        lambda *a, **k: {"started": True, "detail": "test"},
    )
    monkeypatch.setattr(
        sb, "confirm_background_agent",
        lambda *a, **k: {"confirmed": True, "detail": "test"},
    )
    cloud = _LifecycleCloud()
    monkeypatch.setattr(
        sb, "_lifecycle_cloud",
        lambda _config_path: (cloud, {"agent_id": "agent", "agent_key": "key"}),
    )
    return cloud


def test_continuity_owner_cannot_be_disabled_after_primary_promotion(monkeypatch):
    with _Env() as env:
        a, b = _seed_two()
        _activation_ok(monkeypatch)

        promoted = sb.make_managed_recorder_primary(env.ini, b)
        assert promoted["is_primary"] is True
        assert promoted["continuity_owner"] is False

        before = rr.load_registry()
        try:
            sb.disable_managed_recorder(env.ini, a)
            assert False, "continuity owner must not be disableable in 5.1"
        except ValueError as exc:
            assert "original watchlog recorder cannot be disabled" in str(exc).lower()

        assert rr.load_registry() == before
        rows = {row["local_id"]: row for row in rr.recorders()}
        assert rows[a]["is_configured"] is True
        assert rows[a]["continuity_owner"] is True
        assert rows[b]["is_primary"] is True
        assert cs.load_recorder_credential(a)["password"] == "pw-a"


def test_secondary_recorder_can_be_disabled_and_reenabled(monkeypatch):
    with _Env() as env:
        _a, b = _seed_two()
        _activation_ok(monkeypatch)

        disabled = sb.disable_managed_recorder(env.ini, b)
        assert disabled["is_configured"] is False
        assert disabled["continuity_owner"] is False

        enabled = sb.enable_managed_recorder(env.ini, b)
        assert enabled["is_configured"] is True
        assert enabled["continuity_owner"] is False


def test_primary_change_activation_failure_restores_registry(monkeypatch):
    with _Env() as env:
        a, b = _seed_two()
        monkeypatch.setattr(
            sb, "ensure_background_agent",
            lambda *args, **kwargs: {"started": False, "detail": "simulated"},
        )

        before = rr.load_registry()
        try:
            sb.make_managed_recorder_primary(env.ini, b)
            assert False, "failed activation must raise"
        except ValueError:
            pass
        assert rr.load_registry() == before
        assert rr.primary_recorder()["local_id"] == a


def test_reenable_requires_its_own_credential(monkeypatch):
    with _Env() as env:
        _a, b = _seed_two()
        _activation_ok(monkeypatch)
        sb.disable_managed_recorder(env.ini, b)
        cs.delete_recorder_credential(b)

        try:
            sb.enable_managed_recorder(env.ini, b)
            assert False, "missing recorder credential must fail closed"
        except ws.SecretError:
            pass
        assert rr.recorder(b)["is_configured"] is False


def test_rename_preserves_secret_and_restarts(monkeypatch):
    with _Env() as env:
        a, _b = _seed_two()
        _activation_ok(monkeypatch)
        before = cs.recorder_credential_path(a).read_bytes()
        renamed = sb.rename_managed_recorder(env.ini, a, "Front Building Recorder")
        assert renamed["display_name"] == "Front Building Recorder"
        assert cs.recorder_credential_path(a).read_bytes() == before
        assert renamed["activation"]["agent_start"]["started"] is True


def test_verified_credential_repair_updates_only_target(monkeypatch):
    with _Env():
        a, b = _seed_two()
        proven = {
            "url": "http://192.0.2.20",
            "vendor": "Dahua",
            "model": "TEST-B2",
            "firmware": "2.0",
            "driver": "dahua-cgi",
            "serial": "SERIAL-B",
            "channels": [{"channel": "1", "name": "Camera 1"}],
            "verified_against_hardware": True,
        }
        out = sb.repair_managed_recorder_credential(
            b, "user-b2", "pw-b2", verified_recorder=proven
        )
        assert out["model"] == "TEST-B2"
        assert cs.load_recorder_credential(b)["password"] == "pw-b2"
        assert cs.load_recorder_credential(a)["password"] == "pw-a"


def test_failed_credential_proof_never_overwrites_working_secret(monkeypatch):
    with _Env():
        _a, b = _seed_two()

        def fail(*_args, **_kwargs):
            raise ValueError("The recorder rejected that username or password.")

        monkeypatch.setattr(sb, "test_recorder", fail)
        try:
            sb.repair_managed_recorder_credential(b, "user-b", "wrong")
            assert False, "failed proof must raise"
        except ValueError:
            pass
        assert cs.load_recorder_credential(b)["password"] == "pw-b"


def test_manager_list_never_exposes_credentials():
    with _Env() as env:
        _seed_two()
        rows = sb.list_managed_recorders(env.ini)
        raw = json.dumps(rows)
        assert "pw-a" not in raw and "pw-b" not in raw
        assert "user-a" not in raw and "user-b" not in raw
        assert len(rows) == 2
        assert sum(1 for row in rows if row["continuity_owner"]) == 1
