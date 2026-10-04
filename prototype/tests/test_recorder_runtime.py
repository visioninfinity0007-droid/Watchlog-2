"""RecorderContext isolation contracts.

No threads/drivers are started here. These tests prove the configuration and
credential isolation that the later collector/health/recovery fan-out will rely on.
"""
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
import recorder_runtime as rt  # noqa: E402
import windows_secret as ws  # noqa: E402


def _install_fake_crypto():
    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError(f"corrupt blob at {path}")
        return json.loads(text[5:])

    cs.write_json_secret = wjs
    cs.read_json_secret = rjs


class _Env:
    def __enter__(self):
        self.saved = os.environ.get("PROGRAMDATA")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self.tmp.name
        _install_fake_crypto()
        return Path(self.tmp.name) / "WatchLog"

    def __exit__(self, *args):
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def _base():
    state_path = Path(os.environ.get("PROGRAMDATA", ".")) / "WatchLog" / "agent_state.json"
    return SimpleNamespace(
        state_path=state_path,
        spool_path=state_path.parent / "spool.sqlite",
        health_store_path=state_path.parent / "health.sqlite",
        last_live_path=state_path.parent / "last_live.json",
        spool_max_rows=1000,
        nvr_url="legacy-url",
        nvr_driver="legacy-driver",
        nvr_username="legacy-user",
        nvr_password="legacy-password",
        heartbeat_seconds=60,
        recovery_enabled=True,
    )


def _seed_two():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    cs.save_recorder_credential(a, "user-a", "pw-a")
    cs.save_recorder_credential(b, "user-b", "pw-b")
    rr.save_registry({
        "schema": rr.REGISTRY_SCHEMA,
        "recorders": [
            {
                "local_id": a,
                "cloud_recorder_id": "11111111-1111-1111-1111-111111111111",
                "display_name": "Recorder A",
                "url": "http://192.0.2.10",
                "driver": "onvif",
                "vendor": "Hikvision",
                "model": "A",
                "is_primary": True,
                "is_configured": True,
            },
            {
                "local_id": b,
                "cloud_recorder_id": "22222222-2222-2222-2222-222222222222",
                "display_name": "Recorder B",
                "url": "http://192.0.2.20",
                "driver": "dahua-cgi",
                "vendor": "Dahua",
                "model": "B",
                "is_primary": False,
                "is_configured": True,
            },
        ],
    })
    return a, b


def test_contexts_bind_independent_credentials_and_urls():
    with _Env():
        a, b = _seed_two()
        base = _base()
        contexts = rt.load_contexts(base)
        assert [c.local_id for c in contexts] == [a, b]

        ca, cb = contexts
        assert ca.config.nvr_url == "http://192.0.2.10"
        assert ca.config.nvr_username == "user-a"
        assert ca.config.nvr_password == "pw-a"
        assert ca.config.nvr_driver == "onvif"

        assert cb.config.nvr_url == "http://192.0.2.20"
        assert cb.config.nvr_username == "user-b"
        assert cb.config.nvr_password == "pw-b"
        assert cb.config.nvr_driver == "dahua-cgi"

        # Shared/global config remains inherited without mutating the original.
        assert ca.config.heartbeat_seconds == 60
        assert cb.config.heartbeat_seconds == 60
        assert base.nvr_url == "legacy-url"
        assert base.nvr_username == "legacy-user"


def test_context_holders_are_isolated():
    with _Env():
        _seed_two()
        ca, cb = rt.load_contexts(_base())
        ca.holder["recorder_live_at"] = 123
        assert "recorder_live_at" not in cb.holder
        assert ca.holder["recorder_local_id"] != cb.holder["recorder_local_id"]


def test_cloud_descriptor_contains_no_secret_or_local_address():
    with _Env():
        _seed_two()
        ca, cb = rt.load_contexts(_base())
        for ctx in (ca, cb):
            payload = ctx.cloud_descriptor()
            raw = json.dumps(payload)
            assert "password" not in raw.lower()
            assert "username" not in raw.lower()
            assert "192.0.2." not in raw
            assert payload["local_key"] == ctx.local_id
            assert payload["display_name"] == ctx.display_name


def test_corrupt_one_context_fails_whole_registry_load_closed():
    with _Env():
        _, b = _seed_two()
        cs.recorder_credential_path(b).write_text("CORRUPT", encoding="utf-8")
        try:
            rt.load_contexts(_base())
            assert False, "registry load must fail rather than omit/fallback Recorder B"
        except ws.SecretError:
            pass


def test_reload_credential_is_recorder_specific():
    with _Env():
        a, b = _seed_two()
        ca, cb = rt.load_contexts(_base())
        cs.save_recorder_credential(a, "user-a2", "pw-a2")
        ca.reload_credential()
        assert ca.config.nvr_username == "user-a2"
        assert ca.config.nvr_password == "pw-a2"
        assert cb.config.nvr_username == "user-b"
        assert cb.config.nvr_password == "pw-b"
        assert ca.credential_generation() != "absent"


def test_exactly_one_primary_required_for_contexts():
    with _Env():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        cs.save_recorder_credential(a, "a", "a")
        cs.save_recorder_credential(b, "b", "b")
        # Save a valid registry first then edit it manually into a corrupt/no-primary
        # state so load_contexts itself is still defensive if registry validation is bypassed.
        rr.save_registry({
            "schema": rr.REGISTRY_SCHEMA,
            "recorders": [{
                "local_id": a, "display_name": "A", "url": "", "driver": "auto",
                "is_primary": True, "is_configured": True,
            }],
        })
        raw = {
            "schema": rr.REGISTRY_SCHEMA,
            "recorders": [
                {"local_id": a, "display_name": "A", "url": "", "driver": "auto",
                 "is_primary": False, "is_configured": True},
                {"local_id": b, "display_name": "B", "url": "", "driver": "auto",
                 "is_primary": False, "is_configured": True},
            ],
        }
        rr.registry_path().write_text(json.dumps(raw), encoding="utf-8")
        try:
            rt.load_contexts(_base())
            assert False, "no-primary registry must fail"
        except ValueError:
            pass


def test_primary_preserves_all_legacy_durable_paths_secondary_is_isolated():
    with _Env():
        a, b = _seed_two()
        base = _base()
        ca, cb = rt.load_contexts(base)

        assert ca.is_primary is True
        assert ca.config.spool_path == base.spool_path
        assert ca.config.health_store_path == base.health_store_path
        assert ca.config.last_live_path == base.last_live_path

        assert cb.config.spool_path != base.spool_path
        assert cb.config.health_store_path != base.health_store_path
        assert cb.config.last_live_path != base.last_live_path
        assert str(b) in str(cb.config.spool_path)
        assert str(b) in str(cb.config.health_store_path)
        assert str(b) in str(cb.config.last_live_path)
        assert cb.config.spool_path.name == "spool.sqlite"
        assert cb.config.health_store_path.name == "health.sqlite"
        assert cb.config.last_live_path.name == "last_live.json"


def test_primary_existing_last_live_marker_stays_at_same_path():
    with _Env():
        _seed_two()
        base = _base()
        base.last_live_path.parent.mkdir(parents=True, exist_ok=True)
        payload = '{"last_live":"2026-10-02T12:00:00+00:00"}'
        base.last_live_path.write_text(payload, encoding="utf-8")

        primary, secondary = rt.load_contexts(base)
        assert primary.config.last_live_path == base.last_live_path
        assert primary.config.last_live_path.read_text(encoding="utf-8") == payload
        assert secondary.config.last_live_path != base.last_live_path


# --- cloud job resolution (MNVR-002 / MNVR-039) ------------------------------

CLOUD_A = "11111111-1111-1111-1111-111111111111"
CLOUD_B = "22222222-2222-2222-2222-222222222222"


def test_job_for_healthy_recorder_does_not_decrypt_a_bad_sibling():
    with _Env():
        _a, b = _seed_two()
        cs.recorder_credential_path(b).write_text("CORRUPT", encoding="utf-8")

        cfg = rt.config_for_cloud_recorder(_base(), CLOUD_A)
        assert cfg.nvr_url == "http://192.0.2.10"
        assert cfg.nvr_username == "user-a" and cfg.nvr_password == "pw-a"
        assert cfg.recorder_cloud_id == CLOUD_A

        # The broken recorder's own jobs still fail closed; never a fallback.
        try:
            rt.config_for_cloud_recorder(_base(), CLOUD_B)
            assert False, "a job for the corrupt recorder must fail closed"
        except ws.SecretError:
            pass


def test_resolver_selects_the_row_before_any_decryption(monkeypatch):
    with _Env():
        a, _b = _seed_two()
        decrypted = []
        real = cs.load_recorder_credential

        def spy(local_id):
            decrypted.append(local_id)
            return real(local_id)

        monkeypatch.setattr(cs, "load_recorder_credential", spy)
        rt.config_for_cloud_recorder(_base(), CLOUD_A)
        assert decrypted == [a]


def test_no_registry_routes_a_recorder_job_to_the_singleton_runtime():
    with _Env():
        base = _base()
        assert not rr.registry_path().exists()
        # 5.0.x singleton behaviour: the DB sends the site's single recorder id
        # with every job, and the registry-less runtime is that recorder.
        assert rt.config_for_cloud_recorder(base, CLOUD_A) is base
        assert rt.config_for_cloud_recorder(base, None) is base


def test_registry_jobs_stay_exact_and_fail_closed():
    with _Env():
        _seed_two()
        base = _base()
        try:
            rt.config_for_cloud_recorder(base, "33333333-3333-3333-3333-333333333333")
            assert False, "an unknown recorder must not resolve"
        except ValueError as exc:
            assert "not mapped" in str(exc)
        try:
            rt.config_for_cloud_recorder(base, None)
            assert False, "a multi-recorder job needs recorder_id"
        except ValueError as exc:
            assert "recorder_id required" in str(exc)


def test_job_for_a_disabled_recorder_is_not_routed():
    with _Env():
        _a, b = _seed_two()
        rr.disable_recorder(b)
        try:
            rt.config_for_cloud_recorder(_base(), CLOUD_B)
            assert False, "a disabled recorder must not receive jobs"
        except ValueError as exc:
            assert "not mapped" in str(exc)
        # With one configured recorder left, a legacy job without recorder_id
        # resolves to it from the registry, not from the legacy singleton.
        cfg = rt.config_for_cloud_recorder(_base(), None)
        assert cfg.nvr_url == "http://192.0.2.10"
        assert cfg.recorder_cloud_id == CLOUD_A


def test_job_claimed_during_startup_binding_waits_for_the_binding(monkeypatch):
    with _Env():
        a = str(uuid.uuid4())
        cs.save_recorder_credential(a, "user-a", "pw-a")
        rr.save_registry({
            "schema": rr.REGISTRY_SCHEMA,
            "recorders": [{
                "local_id": a, "display_name": "Recorder A",
                "url": "http://192.0.2.10", "driver": "onvif",
                "is_primary": True, "is_configured": True,
            }],
        })
        monkeypatch.setattr(rt, "UNBOUND_BINDING_WAIT_SECONDS", 5.0)
        monkeypatch.setattr(rt, "UNBOUND_BINDING_POLL_SECONDS", 0.01)

        sleeps = []

        def bind_while_waiting(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                rr.apply_cloud_mapping({a: CLOUD_A})

        monkeypatch.setattr(rt.time, "sleep", bind_while_waiting)
        cfg = rt.config_for_cloud_recorder(_base(), CLOUD_A)
        assert cfg.recorder_cloud_id == CLOUD_A
        assert cfg.nvr_url == "http://192.0.2.10"
        assert len(sleeps) == 2

        # Once every configured row is bound, an unknown id fails at once.
        sleeps.clear()
        try:
            rt.config_for_cloud_recorder(_base(), CLOUD_B)
            assert False
        except ValueError:
            pass
        assert sleeps == []
