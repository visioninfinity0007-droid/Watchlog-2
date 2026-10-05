"""Immutable recorder continuity vs movable preferred-primary contracts."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import recorder_runtime as runtime  # noqa: E402
import windows_secret as ws  # noqa: E402


def _install_fake_crypto():
    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(text[5:])

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
        return self

    def __exit__(self, *_args):
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def _base(root: Path):
    return SimpleNamespace(
        state_path=root / "agent_state.json",
        spool_path=root / "events.sqlite",
        health_store_path=root / "health.sqlite",
        last_live_path=root / "last_live.json",
        spool_max_rows=1000,
        nvr_url="legacy",
        nvr_driver="auto",
        nvr_username="legacy",
        nvr_password="legacy",
    )


def _seed_old_v1_registry(root: Path):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    ca = str(uuid.uuid4())
    cb = str(uuid.uuid4())
    cs.save_recorder_credential(a, "a", "pw-a")
    cs.save_recorder_credential(b, "b", "pw-b")
    # Deliberately omit continuity_owner to simulate a pre-0154 5.1 candidate
    # registry. Backward-compatible validation must infer it from the then-fixed
    # primary exactly once.
    rr.registry_path().write_text(json.dumps({
        "schema": rr.REGISTRY_SCHEMA,
        "recorders": [
            {
                "local_id": a,
                "cloud_recorder_id": ca,
                "display_name": "Recorder A",
                "url": "http://192.0.2.10",
                "driver": "onvif",
                "is_primary": True,
                "is_configured": True,
            },
            {
                "local_id": b,
                "cloud_recorder_id": cb,
                "display_name": "Recorder B",
                "url": "http://192.0.2.20",
                "driver": "onvif",
                "is_primary": False,
                "is_configured": True,
            },
        ],
    }), encoding="utf-8")
    return a, b, ca, cb


def test_old_registry_backfills_continuity_from_original_primary():
    with _Env() as env:
        a, b, _ca, _cb = _seed_old_v1_registry(env.root)
        rows = {r["local_id"]: r for r in rr.recorders()}
        assert rows[a]["continuity_owner"] is True
        assert rows[b]["continuity_owner"] is False


def test_primary_promotion_never_moves_continuity_or_legacy_paths():
    with _Env() as env:
        a, b, _ca, _cb = _seed_old_v1_registry(env.root)
        base = _base(env.root)

        before = {ctx.local_id: ctx for ctx in runtime.load_contexts(base)}
        assert before[a].is_primary is True
        assert before[a].continuity_owner is True
        assert before[a].config.spool_path == base.spool_path
        assert before[a].config.health_store_path == base.health_store_path
        assert before[a].config.last_live_path == base.last_live_path
        assert before[b].config.spool_path != base.spool_path

        promoted = rr.make_primary(b)
        assert promoted["is_primary"] is True
        rows = {r["local_id"]: r for r in rr.recorders()}
        assert rows[a]["is_primary"] is False
        assert rows[a]["continuity_owner"] is True
        assert rows[b]["is_primary"] is True
        assert rows[b]["continuity_owner"] is False

        after = {ctx.local_id: ctx for ctx in runtime.load_contexts(base)}
        assert after[a].config.spool_path == base.spool_path
        assert after[a].config.health_store_path == base.health_store_path
        assert after[a].config.last_live_path == base.last_live_path
        assert after[b].config.spool_path != base.spool_path
        assert after[b].config.health_store_path != base.health_store_path
        assert after[b].config.last_live_path != base.last_live_path


def test_continuity_owner_cannot_be_disabled_after_primary_promotion():
    with _Env() as env:
        a, b, _ca, _cb = _seed_old_v1_registry(env.root)
        base = _base(env.root)
        rr.make_primary(b)

        before = rr.load_registry()
        try:
            rr.disable_recorder(a)
            assert False, "continuity owner must stay configured in 5.1"
        except ValueError as exc:
            assert "original watchlog recorder cannot be disabled" in str(exc).lower()

        assert rr.load_registry() == before
        contexts = {ctx.local_id: ctx for ctx in runtime.load_contexts(base)}
        assert set(contexts) == {a, b}
        assert contexts[a].continuity_owner is True
        assert contexts[a].config.spool_path == base.spool_path
        assert contexts[b].is_primary is True
        assert contexts[b].config.spool_path != base.spool_path


def test_ordinary_secondary_recorder_can_still_be_disabled():
    with _Env() as env:
        a, b, _ca, _cb = _seed_old_v1_registry(env.root)
        base = _base(env.root)

        disabled = rr.disable_recorder(b)
        assert disabled["is_configured"] is False
        assert disabled["continuity_owner"] is False

        contexts = runtime.load_contexts(base)
        assert len(contexts) == 1
        assert contexts[0].local_id == a
        assert contexts[0].continuity_owner is True

        enabled = rr.enable_recorder(b)
        assert enabled["is_configured"] is True
        again = {ctx.local_id: ctx for ctx in runtime.load_contexts(base)}
        assert set(again) == {a, b}


def test_primary_change_requires_every_configured_recorder_cloud_bound():
    with _Env() as env:
        a, b, _ca, _cb = _seed_old_v1_registry(env.root)
        payload = rr.load_registry()
        for row in payload["recorders"]:
            if row["local_id"] == b:
                row["cloud_recorder_id"] = None
        rr.save_registry(payload)

        try:
            rr.make_primary(b)
            assert False, "unbound recorder must not become preferred primary"
        except ValueError as exc:
            assert "linked" in str(exc).lower()

        rows = {r["local_id"]: r for r in rr.recorders()}
        assert rows[a]["is_primary"] is True
        assert rows[a]["continuity_owner"] is True


def test_continuity_owner_is_local_only_and_not_agent_mutable_payload():
    with _Env() as env:
        _seed_old_v1_registry(env.root)
        # First save persists inferred continuity_owner locally.
        rr.save_registry(rr.load_registry())
        descriptors = rr.registry_cloud_descriptors()
        assert descriptors
        assert all("continuity_owner" not in row for row in descriptors)
        assert sum(1 for r in rr.recorders() if r["continuity_owner"]) == 1
