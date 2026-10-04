"""A recorder registry left behind by an earlier installation (MNVR-017 registry part).

Uninstall removes the identity, the Secrets directory and the legacy spool but
keeps %ProgramData%\\WatchLog\\recorders.json and the per-recorder state folders.
Setup must quarantine that stale registry (moved aside, never deleted) and stage
the newly proven recorder, instead of failing every reinstall with "Windows could
not prepare this recorder". A registry from the same enrolled site is updated in
place instead (MNVR-012), and an unreadable one is quarantined too.
"""
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
from spool import Spool  # noqa: E402

OLD_CLOUD = "01d00000-0000-4000-8000-000000000001"
OLD_CLOUD_B = "01db0000-0000-4000-8000-000000000002"
SITE_ONE = {"agent_id": "agent-1", "agent_key": "key-1", "site_id": "site-1", "tenant_id": "t"}
SITE_TWO = {"agent_id": "agent-2", "agent_key": "key-2", "site_id": "site-2", "tenant_id": "t"}


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
        self.ini = self.root / "watchlog.ini"
        return self

    def __exit__(self, *_args):
        cs.write_json_secret, cs.read_json_secret = self.saved_crypto
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def _previous_install(root: Path) -> tuple[str, str]:
    """A bound two-recorder registry, then an uninstall (Secrets + identity gone)."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    cs.save_recorder_credential(a, "old-a", "old-pw-a")
    cs.save_recorder_credential(b, "old-b", "old-pw-b")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": [
        {"local_id": a, "cloud_recorder_id": OLD_CLOUD, "display_name": "Old A",
         "url": "http://192.0.2.10", "driver": "hikvision", "is_primary": True,
         "continuity_owner": True, "is_configured": True},
        {"local_id": b, "cloud_recorder_id": OLD_CLOUD_B,
         "display_name": "Old B", "url": "http://192.0.2.20", "driver": "dahua-cgi",
         "is_primary": False, "continuity_owner": False, "is_configured": True},
    ]})
    queue = Spool(root / "recorders" / b / "spool.sqlite")
    try:
        queue.add({"channel": "1", "event_type": "motion", "payload": {}})
    finally:
        queue.close()
    # NSIS uninstall: RMDir /r Secrets, Delete agent_state.json; recorders.json stays.
    for path in sorted((root / "Secrets").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (root / "Secrets").rmdir()
    return a, b


def _patch_setup(monkeypatch, *, prior_state, new_state):
    class Cloud:
        def call(self, _name, **_kw):
            return {}

    monkeypatch.setattr(sb, "_load_existing_identity",
                        lambda _p: dict(prior_state) if prior_state else None)
    monkeypatch.setattr(sb, "establish_identity", lambda *a, **k: dict(new_state))
    monkeypatch.setattr(sb, "sync_cameras", lambda *a, **k: {"1": "cam-1"})
    monkeypatch.setattr(sb.core, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(sb.core, "Cloud", lambda *a, **k: Cloud())
    monkeypatch.setattr(sb, "ensure_background_agent",
                        lambda *a, **k: {"started": True, "detail": "test"})
    monkeypatch.setattr(sb, "_seed_recorder_identity", lambda *a, **k: None)
    monkeypatch.setattr(sb, "_clear_consumed_code", lambda *a, **k: True)


def _finalize(env):
    recorder = {
        "url": "http://192.0.2.50", "vendor": "Hikvision", "model": "DS-NEW",
        "firmware": "1.0", "driver": "hikvision", "serial": "SER-NEW",
        "channels": [{"channel": "1", "name": "Camera 1"}],
        "verified_against_hardware": True,
    }
    return sb.finalize_install(
        env.ini, {"supabase_url": "https://example.invalid", "supabase_publishable_key": "k"},
        "SITE-CODE", "http://192.0.2.50", "admin", "new-pw", "custom", [],
        verified_recorder=recorder,
    )


def _quarantined(root: Path, name: str) -> list[Path]:
    return sorted(root.glob(f"{name}.quarantine-*"))


def _assert_fresh_single_row(old_ids):
    rows = rr.recorders()
    assert len(rows) == 1
    row = rows[0]
    assert row["local_id"] not in old_ids
    assert row["cloud_recorder_id"] is None           # bound later by the new Agent
    assert row["url"] == "http://192.0.2.50"
    assert row["is_primary"] and row["continuity_owner"] and row["is_configured"]
    assert cs.load_recorder_credential(row["local_id"])["password"] == "new-pw"


def test_reinstall_quarantines_the_registry_left_by_uninstall(monkeypatch):
    with _Env() as env:
        a, b = _previous_install(env.root)
        stale_bytes = rr.registry_path().read_bytes()
        _patch_setup(monkeypatch, prior_state=None, new_state=SITE_ONE)

        out = _finalize(env)

        assert out["connected"] is True
        _assert_fresh_single_row({a, b})
        # Moved aside for support, never deleted; the old recorders' queued events
        # can no longer drain into whatever site this PC is now enrolled in.
        (registry_copy,) = _quarantined(env.root, "recorders.json")
        assert registry_copy.read_bytes() == stale_bytes
        (state_copy,) = _quarantined(env.root, "recorders")
        assert (state_copy / b / "spool.sqlite").exists()
        assert not (env.root / "recorders" / b).exists()


def test_moving_the_pc_to_another_site_quarantines_the_old_registry(monkeypatch):
    with _Env() as env:
        env.ini.write_text("[watchlog]\nnvr_url = http://192.0.2.10\n", encoding="utf-8")
        cs.save_nvr_credential("old", "old-pw")
        old = rr.migrate_legacy_singleton(env.ini)
        rr.apply_cloud_mapping({old["local_id"]: OLD_CLOUD})
        _patch_setup(monkeypatch, prior_state=SITE_ONE, new_state=SITE_TWO)

        _finalize(env)

        _assert_fresh_single_row({old["local_id"]})
        assert len(_quarantined(env.root, "recorders.json")) == 1


def test_unreadable_registry_is_quarantined_not_fatal(monkeypatch):
    with _Env() as env:
        rr.registry_path().write_text("{not json", encoding="utf-8")
        _patch_setup(monkeypatch, prior_state=SITE_ONE, new_state=SITE_ONE)

        _finalize(env)

        _assert_fresh_single_row(set())
        (copy,) = _quarantined(env.root, "recorders.json")
        assert copy.read_text(encoding="utf-8") == "{not json"


def test_same_site_rerun_keeps_identity_and_does_not_quarantine(monkeypatch):
    with _Env() as env:
        env.ini.write_text("[watchlog]\nnvr_url = http://192.0.2.10\n", encoding="utf-8")
        cs.save_nvr_credential("old", "old-pw")
        old = rr.migrate_legacy_singleton(env.ini)
        rr.apply_cloud_mapping({old["local_id"]: OLD_CLOUD})
        _patch_setup(monkeypatch, prior_state=SITE_ONE, new_state=SITE_ONE)

        _finalize(env)

        (row,) = rr.recorders()
        assert (row["local_id"], row["cloud_recorder_id"]) == (old["local_id"], OLD_CLOUD)
        assert row["url"] == "http://192.0.2.50"
        assert _quarantined(env.root, "recorders.json") == []
