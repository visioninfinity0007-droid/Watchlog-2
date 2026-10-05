"""Agent 5.1.0 first install with more than one recorder (audit WP-12).

The first-run Setup used to connect exactly one recorder: the "Add another recorder"
path was reachable only after an install, from Manage Recorders, and finalize_install
staged one registry row whose camera choices (camera_profiles_json) were keyed by
channel alone. These tests drive the real finalize_install against a fake WatchLog
that behaves like the multi-recorder RPC surface:

  * two recorders that share channel numbers become two registry rows, each with its
    own DPAPI credential, both bound to their own WatchLog recorder before the
    background Agent starts;
  * each recorder's cameras are created on its own recorder with that recorder's
    names and Monitor/Ignore choices; the channel-keyed purpose bootstrap only ever
    sees the first recorder's cameras and is never re-sent by the Agent once a
    second recorder exists;
  * camera choices are stored per recorder (keyed by recorder, then channel);
  * a single-recorder install is exactly what it was.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import setup_backend as sb  # noqa: E402
import windows_secret as ws  # noqa: E402

PUBLIC = {"supabase_url": "https://example.invalid", "supabase_publishable_key": "k"}
SITE = {"agent_id": "agent-1", "agent_key": "key-1", "site_id": "site-1", "tenant_id": "t"}
FEATURES = sorted(sb.MULTI_RECORDER_SETUP_FEATURES)


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


class FakeWatchLog:
    """The multi-recorder RPC surface, enough to prove ordering and scoping."""

    def __init__(self, *, contract_version=4, drop_channel_for=None):
        self.calls: list[tuple[str, dict]] = []
        self.contract_version = contract_version
        self.drop_channel_for = drop_channel_for
        self.recorders: dict[str, str] = {}          # local_key -> cloud recorder id
        self.legacy_recorder: str | None = None       # created lazily by wl_sync_cameras
        self.cameras: dict[tuple[str, str], dict] = {}

    def names(self):
        return [name for name, _ in self.calls]

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_multi_recorder_agent_contract":
            return {"ok": True, "version": self.contract_version, "features": FEATURES}
        if name == "wl_sync_cameras":
            if len(self.recorders) > 1:
                raise AssertionError("legacy camera sync after a second recorder exists")
            self.legacy_recorder = self.legacy_recorder or str(uuid.uuid4())
            return self._upsert(self.legacy_recorder, kw["p_cameras"])
        if name == "wl_sync_recorders":
            payload = kw["p_recorders"]
            primaries = [row for row in payload if row["is_primary"] and row["is_configured"]]
            assert len(primaries) == 1, "a recorder sync names exactly one configured primary"
            out = {}
            for row in payload:
                key = row["local_key"]
                if key not in self.recorders:
                    # The legacy-default recorder is adopted by the primary's first sync.
                    if row["is_primary"] and self.legacy_recorder \
                            and self.legacy_recorder not in self.recorders.values():
                        self.recorders[key] = self.legacy_recorder
                    else:
                        self.recorders[key] = str(uuid.uuid4())
                out[key] = self.recorders[key]
            return out
        if name == "wl_sync_recorder_cameras":
            assert kw["p_recorder_id"] in self.recorders.values()
            cams = kw["p_cameras"]
            if self.drop_channel_for == kw["p_recorder_id"]:
                cams = cams[1:]
            return self._upsert(kw["p_recorder_id"], cams)
        if name in ("wl_sync_capabilities", "wl_sync_recorder_capabilities"):
            return {"ok": True}
        if name == "wl_agent_bootstrap_analytics":
            return {"ok": True, "version": 7, "updated_cameras": len(kw["p_camera_profiles"])}
        raise AssertionError(f"unexpected RPC {name}")

    def _upsert(self, recorder_id, cameras):
        out = {}
        for cam in cameras:
            key = (recorder_id, str(cam["channel"]))
            row = self.cameras.setdefault(key, {"id": str(uuid.uuid4()),
                                                "name": cam.get("name"),
                                                "is_configured": cam.get("is_configured")})
            out[str(cam["channel"])] = row["id"]
        return out


def _patch(monkeypatch, cloud, *, prior=None, state=SITE):
    started = []
    monkeypatch.setattr(sb, "_load_existing_identity", lambda _p: dict(prior) if prior else None)
    monkeypatch.setattr(sb, "establish_identity", lambda *a, **k: dict(state))
    monkeypatch.setattr(sb.core, "heartbeat",
                        lambda *a, **k: cloud.calls.append(("wl_heartbeat", {})))
    monkeypatch.setattr(sb.core, "Cloud", lambda *a, **k: cloud)
    monkeypatch.setattr(sb, "ensure_background_agent",
                        lambda *a, **k: started.append(True) or {"started": True, "detail": "t"})
    monkeypatch.setattr(sb, "_seed_recorder_identity", lambda *a, **k: None)
    monkeypatch.setattr(sb, "_clear_consumed_code", lambda *a, **k: True)
    monkeypatch.setattr(sb, "test_recorder", _no_probe)
    return started


def _no_probe(*_a, **_k):
    raise AssertionError("finalize_install must reuse each recorder's verified login")


def _recorder(url, serial, names, vendor="Hikvision"):
    return {
        "url": url, "vendor": vendor, "model": "NVR", "firmware": "1.0",
        "driver": "hikvision-isapi" if vendor == "Hikvision" else "dahua-cgi",
        "serial": serial, "verified_against_hardware": True, "capabilities": None,
        "channels": [{"channel": str(i + 1), "name": name} for i, name in enumerate(names)],
    }


A = _recorder("http://192.0.2.10", "SER-A", ["Gate", "Till"])
B = _recorder("http://192.0.2.20", "SER-B", ["Yard", "Store"], vendor="Dahua")
C = _recorder("http://192.0.2.30", "SER-C", ["Back door"])


def _profiles(rec, ignore=()):
    out = sb.default_camera_profiles(rec)
    for prof in out:
        if prof["channel"] in ignore:
            prof["monitored"] = False
    return out


def _extra(rec, name, user, pw, ignore=()):
    return {"address": rec["url"].split("//")[1], "username": user, "password": pw,
            "display_name": name, "verified_recorder": rec, "profiles": _profiles(rec, ignore)}


def _finalize(env, additional=None, primary=A, **kw):
    return sb.finalize_install(
        env.ini, PUBLIC, "WL-CODE", primary["url"].split("//")[1], "admin-a", "pw-a", "retail",
        _profiles(primary), verified_recorder=primary,
        additional_recorders=additional, **kw)


def _rows_by_url():
    return {row["url"]: row for row in rr.recorders()}


def test_two_recorders_sharing_channels_become_two_bound_rows_with_their_own_cameras(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        started = _patch(monkeypatch, cloud)

        out = _finalize(env, [_extra(B, "Warehouse", "admin-b", "pw-b", ignore=("2",))],
                        primary_display_name="Shop")

        rows = _rows_by_url()
        a, b = rows["http://192.0.2.10"], rows["http://192.0.2.20"]
        assert len(rows) == 2
        assert a["continuity_owner"] and a["is_primary"] and a["display_name"] == "Shop"
        assert not b["continuity_owner"] and not b["is_primary"] and b["display_name"] == "Warehouse"
        assert (b["vendor"], b["driver"], b["identity_fingerprint"]) == ("Dahua", "dahua-cgi",
                                                                         "serial:SER-B")
        # Bound to two different WatchLog recorders before the background Agent starts.
        assert a["cloud_recorder_id"] and b["cloud_recorder_id"]
        assert a["cloud_recorder_id"] != b["cloud_recorder_id"]
        assert a["cloud_recorder_id"] == cloud.legacy_recorder       # adopted, not forked
        assert started == [True]
        # One DPAPI credential per recorder; the legacy singleton stays the first recorder's.
        assert cs.load_recorder_credential(a["local_id"])["password"] == "pw-a"
        assert cs.load_recorder_credential(b["local_id"])["username"] == "admin-b"
        assert cs.load_recorder_credential(b["local_id"])["password"] == "pw-b"
        assert cs.load_nvr_credential(env.ini)["password"] == "pw-a"

        # Channel 1 and 2 exist twice: once per recorder, with that recorder's choices.
        assert cloud.cameras[(b["cloud_recorder_id"], "1")]["name"] == "Yard"
        assert cloud.cameras[(b["cloud_recorder_id"], "1")]["is_configured"] is True
        assert cloud.cameras[(b["cloud_recorder_id"], "2")]["is_configured"] is False
        assert cloud.cameras[(a["cloud_recorder_id"], "1")]["name"] == "Gate"
        assert len(cloud.cameras) == 4

        names = cloud.names()
        # The continuity recorder is bound on its own first, then the whole registry.
        syncs = [kw["p_recorders"] for n, kw in cloud.calls if n == "wl_sync_recorders"]
        assert [r["local_key"] for r in syncs[0]] == [a["local_id"]]
        assert {r["local_key"] for r in syncs[-1]} == {a["local_id"], b["local_id"]}
        assert next(r for r in syncs[-1] if r["local_key"] == b["local_id"])["display_name"] \
            == "Warehouse"
        # The channel-keyed purpose bootstrap runs once, before any second recorder exists,
        # with the first recorder's profiles only.
        boots = [kw for n, kw in cloud.calls if n == "wl_agent_bootstrap_analytics"]
        assert len(boots) == 1
        assert [p["name"] for p in boots[0]["p_camera_profiles"]] == ["Gate", "Till"]
        assert names.index("wl_agent_bootstrap_analytics") < names.index("wl_sync_recorders")
        assert names.index("wl_sync_cameras") < names.index("wl_sync_recorders")
        # Recorder-scoped calls need this PC to be the site's most recently seen Agent.
        assert names.index("wl_heartbeat") < names.index("wl_sync_recorders")
        # ...and the Agent will not re-send it after the second recorder exists.
        marker = json.loads((env.root / "analytics_bootstrap_sent.json").read_text("utf-8"))
        assert marker["site_id"] == "site-1"

        # Camera choices are kept per recorder, keyed by recorder then channel.
        for row, rec in ((a, A), (b, B)):
            saved = json.loads((env.root / "recorders" / row["local_id"] /
                                "camera_profiles.json").read_text("utf-8"))
            assert saved["local_id"] == row["local_id"]
            assert [p["name"] for p in saved["profiles"]] == [c["name"] for c in rec["channels"]]
        # The legacy channel-keyed profiles stay the first recorder's only.
        assert "Yard" not in env.ini.read_text("utf-8")

        assert out["recorder_count"] == 2 and out["camera_count"] == 4
        by_name = {r["display_name"]: r for r in out["recorders"]}
        assert by_name["Shop"]["cloud_linked"] and by_name["Warehouse"]["cloud_linked"]
        assert by_name["Shop"]["purposes_applied"] is True
        # No WatchLog call can apply a purpose to one recorder's camera yet: say so.
        assert by_name["Warehouse"]["purposes_applied"] is False
        assert "pw-b" not in json.dumps(out)


def test_three_recorders_each_get_their_own_row_and_cameras(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)

        out = _finalize(env, [_extra(B, "Recorder 2", "b", "pw-b"),
                              _extra(C, "Recorder 3", "c", "pw-c")])

        rows = _rows_by_url()
        assert len(rows) == 3 and all(r["cloud_recorder_id"] for r in rows.values())
        assert len({r["cloud_recorder_id"] for r in rows.values()}) == 3
        assert cs.load_recorder_credential(rows["http://192.0.2.30"]["local_id"])["password"] \
            == "pw-c"
        assert out["recorder_count"] == 3 and out["camera_count"] == 5


def test_a_single_recorder_install_is_unchanged(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)

        out = _finalize(env)

        (row,) = rr.recorders()
        assert row["cloud_recorder_id"] is None          # bound later by the Agent, as before
        assert row["display_name"] == "Primary Recorder"
        assert cloud.names() == ["wl_sync_cameras", "wl_agent_bootstrap_analytics",
                                 "wl_heartbeat"]
        assert not (env.root / "analytics_bootstrap_sent.json").exists()
        assert not (env.root / "recorders").exists()
        assert "recorders" not in out and "recorder_count" not in out


def test_the_same_recorder_twice_is_refused_before_anything_is_written(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)
        twin = dict(B, url="HTTP://192.0.2.10:80/", serial="")

        with pytest.raises(ValueError, match="same recorder"):
            _finalize(env, [_extra(twin, "Again", "b", "pw-b")])

        assert cloud.calls == []
        assert not env.ini.exists() and not rr.registry_path().exists()


def test_recorders_need_different_names(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)

        with pytest.raises(ValueError, match="different name"):
            _finalize(env, [_extra(B, "Shop", "b", "pw-b")], primary_display_name=" shop ")
        assert cloud.calls == [] and not env.ini.exists()


def test_a_site_without_the_multi_recorder_backend_is_refused_and_restored(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog(contract_version=3)
        started = _patch(monkeypatch, cloud)

        with pytest.raises(ValueError, match="multi-recorder"):
            _finalize(env, [_extra(B, "Warehouse", "b", "pw-b")])

        assert not rr.registry_path().exists() or rr.recorders() == []
        assert not env.ini.exists()                       # legacy store put back
        assert cs.load_nvr_credential(env.ini) is None
        assert "wl_sync_cameras" not in cloud.names() and started == []


def test_a_failed_secondary_write_rolls_back_this_runs_rows_and_the_legacy_store(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)
        real_add = rr.add_recorder

        def add(**kw):
            if kw["url"] == C["url"]:
                raise OSError("disk full")
            return real_add(**kw)

        monkeypatch.setattr(rr, "add_recorder", add)

        with pytest.raises(ValueError, match="could not prepare"):
            _finalize(env, [_extra(B, "Recorder 2", "b", "pw-b"),
                            _extra(C, "Recorder 3", "c", "pw-c")])

        (kept,) = rr.recorders()
        assert kept["url"] == "http://192.0.2.10"
        # Recorder 2 was added in this run and rolled back with its credential.
        blobs = {p.stem for p in cs.recorder_secrets_dir().glob("*.dpapi")}
        assert blobs == {kept["local_id"]}
        assert not env.ini.exists()
        assert "wl_sync_recorders" not in cloud.names()


def test_retry_after_binding_reuses_every_recorder_instead_of_failing(monkeypatch):
    """Connect failed after the recorders were bound (for example the heartbeat). The
    technician presses Retry: the same recorders must not become duplicates."""
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)
        first_attempt = {"n": 0}

        def flaky_heartbeat(*_a, **_k):
            first_attempt["n"] += 1
            if first_attempt["n"] == 2:            # the final confirmation, after binding
                raise RuntimeError("timeout")

        monkeypatch.setattr(sb.core, "heartbeat", flaky_heartbeat)
        extra = [_extra(B, "Warehouse", "admin-b", "pw-b")]
        with pytest.raises(ValueError, match="final connection"):
            _finalize(env, extra)
        before = {u: (r["local_id"], r["cloud_recorder_id"]) for u, r in _rows_by_url().items()}

        _patch(monkeypatch, cloud, prior=SITE)
        monkeypatch.setattr(sb.core, "heartbeat", lambda *a, **k: None)
        extra[0]["password"] = "pw-b2"
        first_calls = len(cloud.calls)
        out = _finalize(env, extra)

        after = {u: (r["local_id"], r["cloud_recorder_id"]) for u, r in _rows_by_url().items()}
        assert after == before and len(after) == 2
        b_id = before["http://192.0.2.20"][0]
        assert cs.load_recorder_credential(b_id)["password"] == "pw-b2"
        assert out["recorder_count"] == 2
        # The continuity recorder is already bound: its cameras go to its own recorder,
        # and the ambiguous channel-keyed paths are not used again.
        second = [n for n, _ in cloud.calls[first_calls:]]
        assert "wl_sync_cameras" not in second
        assert "wl_agent_bootstrap_analytics" not in second
        assert second.count("wl_sync_recorder_cameras") == 2


def test_a_stale_registry_is_quarantined_by_a_multi_recorder_install(monkeypatch):
    with _Env() as env:
        old_a, old_b = str(uuid.uuid4()), str(uuid.uuid4())
        rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": [
            {"local_id": old_a, "cloud_recorder_id": str(uuid.uuid4()), "display_name": "Old A",
             "url": "http://192.0.2.10", "is_primary": True, "continuity_owner": True},
            {"local_id": old_b, "cloud_recorder_id": str(uuid.uuid4()), "display_name": "Old B",
             "url": "http://192.0.2.20"},
        ]})
        # Uninstall removed the identity and Secrets; recorders.json stayed behind.
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud, prior=None)

        _finalize(env, [_extra(B, "Warehouse", "b", "pw-b")])

        rows = _rows_by_url()
        assert len(rows) == 2 and old_b not in {r["local_id"] for r in rows.values()}
        assert all(r["cloud_recorder_id"] for r in rows.values())
        assert len(list(env.root.glob("recorders.json.quarantine-*"))) == 1


def test_an_incomplete_camera_sync_on_a_second_recorder_is_reported(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)
        real = cloud.call

        def call(name, **kw):
            if name == "wl_sync_recorder_cameras":
                cloud.drop_channel_for = kw["p_recorder_id"]
            return real(name, **kw)

        cloud.call = call
        with pytest.raises(sb.AgentSyncError) as err:
            _finalize(env, [_extra(B, "Warehouse", "b", "pw-b")])
        assert err.value.category == "CAMERA_SYNC_PARTIAL"


# --- pure helpers the Setup window uses -------------------------------------------------

def test_cameras_are_listed_grouped_by_recorder_with_overlapping_channels():
    entries = [{"display_name": "Shop", "verified_recorder": A},
               {"display_name": "", "verified_recorder": B}]
    assert sb.camera_rows_by_recorder(entries) == [
        ("Shop", "1", "Gate"), ("Shop", "2", "Till"),
        ("Recorder 2", "1", "Yard"), ("Recorder 2", "2", "Store"),
    ]


def test_default_recorder_names():
    assert sb.default_recorder_name(0) == "Primary Recorder"
    assert sb.default_recorder_name(1) == "Recorder 2"
    assert sb.default_recorder_name(4) == "Recorder 5"


def test_a_chosen_recorder_is_found_again_by_address_or_serial():
    chosen = [{"address": "192.0.2.10", "verified_recorder": A},
              {"address": "192.0.2.20", "verified_recorder": B}]
    assert sb.find_install_duplicate(chosen, {"address": "192.0.2.20"}) == 1
    moved = dict(A, url="http://192.0.2.99")
    assert sb.find_install_duplicate(chosen, {"address": "192.0.2.99",
                                              "verified_recorder": moved}) == 0
    assert sb.find_install_duplicate(chosen, {"address": "192.0.2.30",
                                              "verified_recorder": C}) is None


def test_default_camera_profiles_match_the_connectivity_first_defaults():
    assert sb.default_camera_profiles(A) == [
        {"channel": "1", "name": "Gate", "purpose": "custom", "monitored": True,
         "analytics_enabled": True},
        {"channel": "2", "name": "Till", "purpose": "custom", "monitored": True,
         "analytics_enabled": True},
    ]
    assert sb.default_camera_profiles(None) == []


# --- a binding answer lost after WatchLog committed it (review AII-3) ---------------------

def _lose_first_full_binding_answer(cloud, error):
    """The whole-registry wl_sync_recorders commits, then its answer is lost once."""
    real = cloud.call
    lost = {"done": False}

    def call(name, **kw):
        if name == "wl_sync_recorders" and len(kw["p_recorders"]) > 1 and not lost["done"]:
            lost["done"] = True
            real(name, **kw)                       # WatchLog committed it...
            raise error                            # ...and the answer never arrived
        return real(name, **kw)

    cloud.call = call


def test_a_lost_binding_answer_keeps_the_rows_so_retry_does_not_fork_recorders(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)
        _lose_first_full_binding_answer(cloud, RuntimeError("read timed out"))
        extra = [_extra(B, "Warehouse", "admin-b", "pw-b")]

        with pytest.raises(ValueError, match="could not link"):
            _finalize(env, extra)

        kept = _rows_by_url()
        # The outcome is unknown: the further recorder's row and credential stay, so a
        # Retry re-sends the same local key instead of creating a second recorder.
        assert set(kept) == {"http://192.0.2.10", "http://192.0.2.20"}
        b_id = kept["http://192.0.2.20"]["local_id"]
        assert cs.load_recorder_credential(b_id)["password"] == "pw-b"

        _patch(monkeypatch, cloud, prior=SITE)
        out = _finalize(env, extra)

        rows = _rows_by_url()
        assert len(rows) == 2 and rows["http://192.0.2.20"]["local_id"] == b_id
        assert len(cloud.recorders) == 2                     # no orphan WatchLog recorder
        assert set(cloud.recorders) == {r["local_id"] for r in rows.values()}
        assert out["recorder_count"] == 2


def test_a_server_error_answer_is_also_an_unknown_binding_outcome(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)
        _lose_first_full_binding_answer(
            cloud, sb.core.CloudError("wl_sync_recorders", 504, None, "gateway timeout"))

        with pytest.raises(ValueError, match="could not link"):
            _finalize(env, [_extra(B, "Warehouse", "admin-b", "pw-b")])

        assert len(_rows_by_url()) == 2


def test_a_definite_binding_refusal_still_rolls_back_this_runs_rows(monkeypatch):
    with _Env() as env:
        cloud = FakeWatchLog()
        _patch(monkeypatch, cloud)
        real = cloud.call

        def call(name, **kw):
            if name == "wl_sync_recorders" and len(kw["p_recorders"]) > 1:
                cloud.calls.append((name, kw))
                # WatchLog refused it (its transaction rolled back): nothing committed.
                raise sb.core.CloudError(name, 400, "P0001", "recorder limit reached")
            return real(name, **kw)

        cloud.call = call
        with pytest.raises(ValueError, match="could not link"):
            _finalize(env, [_extra(B, "Warehouse", "admin-b", "pw-b")])

        (kept,) = rr.recorders()
        assert kept["url"] == "http://192.0.2.10"
        blobs = {p.stem for p in cs.recorder_secrets_dir().glob("*.dpapi")}
        assert blobs == {kept["local_id"]}
