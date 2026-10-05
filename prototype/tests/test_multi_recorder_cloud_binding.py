"""Multi-recorder cloud binding + preflight contracts."""
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
import multi_recorder_orchestrator as mro  # noqa: E402
import recorder_registry as rr  # noqa: E402
from spool import Spool  # noqa: E402
from health_store import HealthStore  # noqa: E402
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


class Env:
    def __enter__(self):
        self.saved = os.environ.get("PROGRAMDATA")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self.tmp.name
        _install_fake_crypto()
        self.root = Path(self.tmp.name) / "WatchLog"
        return self

    def __exit__(self, *args):
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def seed_two(root):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    cs.save_recorder_credential(a, "a-user", "a-pw")
    cs.save_recorder_credential(b, "b-user", "b-pw")
    rr.save_registry({
        "schema": rr.REGISTRY_SCHEMA,
        "recorders": [
            {
                "local_id": a, "display_name": "Recorder A",
                "url": "http://192.0.2.10", "driver": "onvif",
                "is_primary": True, "is_configured": True,
            },
            {
                "local_id": b, "display_name": "Recorder B",
                "url": "http://192.0.2.20", "driver": "onvif",
                "is_primary": False, "is_configured": True,
            },
        ],
    })
    base = SimpleNamespace(
        state_path=root / "agent_state.json",
        spool_path=root / "spool.sqlite",
        health_store_path=root / "health.sqlite",
        last_live_path=root / "last_live.json",
        spool_max_rows=1000,
        nvr_url="legacy", nvr_driver="auto",
        nvr_username="legacy", nvr_password="legacy",
        heartbeat_seconds=60, recovery_enabled=True,
    )
    return a, b, base


class FakeCloud:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_multi_recorder_agent_contract":
            return {
                "ok": True,
                "version": mro.MULTI_RECORDER_CONTRACT_VERSION,
                "configured_recorders": 1,
                "features": sorted(mro.MULTI_RECORDER_REQUIRED_FEATURES),
            }
        if name == "wl_sync_recorders":
            return {
                str(row["local_key"]): self.mapping[str(row["local_key"])]
                for row in kw["p_recorders"]
                if str(row["local_key"]) in self.mapping
            }
        if name == "wl_sync_recorder_cameras":
            return {str(c["channel"]): f"camera-{kw['p_recorder_id']}-{c['channel']}"
                    for c in kw["p_cameras"]}
        if name == "wl_sync_recorder_capabilities":
            return {"ok": True}
        raise AssertionError(name)


class Ch:
    def __init__(self, channel, name):
        self.channel, self.name = channel, name


class Driver:
    name = "onvif"

    def __init__(self, label):
        self.label = label
        self.closed = False

    def list_channels(self):
        return [Ch("1", f"{self.label} Camera 1")]

    def capabilities(self):
        return {
            "channels": [{
                "channel": "1",
                "analytics": [{"label": "motion", "supported": True, "active": True}],
            }]
        }

    def close(self):
        self.closed = True


def info(vendor, model, serial):
    return SimpleNamespace(vendor=vendor, model=model, firmware="V1", serial=serial)


def test_all_cloud_identities_bind_before_per_recorder_probe():
    with Env() as env:
        a, b, base = seed_two(env.root)
        mapping = {
            a: "11111111-1111-1111-1111-111111111111",
            b: "22222222-2222-2222-2222-222222222222",
        }
        cloud = FakeCloud(mapping)
        probe_calls = []

        def open_driver(cfg):
            probe_calls.append(cfg.recorder_local_id)
            if cfg.recorder_local_id == a:
                return Driver("A"), info("Hikvision", "A", "SER-A")
            return Driver("B"), info("Dahua", "B", "SER-B")

        prepared = mro.prepare_recorders(
            base, {"agent_id": "agent", "agent_key": "key"}, cloud, open_driver
        )

        assert len(prepared) == 2
        assert [x.context.local_id for x in prepared] == [a, b]
        assert all(x.error is None for x in prepared)
        # Probes run side by side (MNVR-021); each recorder is probed exactly once.
        assert sorted(probe_calls) == sorted([a, b])

        # Multi-recorder cutover binds primary first, stamps legacy backlog,
        # then syncs the complete set before any probe.
        assert cloud.calls[0][0] == "wl_multi_recorder_agent_contract"
        identity_calls = [(name, kw) for name, kw in cloud.calls if name == "wl_sync_recorders"]
        assert len(identity_calls) == 2
        assert [r["local_key"] for r in identity_calls[0][1]["p_recorders"]] == [a]
        assert {r["local_key"] for r in identity_calls[1][1]["p_recorders"]} == {a, b}

        name, args = identity_calls[0]
        raw = json.dumps(args["p_recorders"])
        assert "192.0.2." not in raw
        assert "a-user" not in raw and "a-pw" not in raw
        assert "b-user" not in raw and "b-pw" not in raw

        assert rr.recorder(a)["cloud_recorder_id"] == mapping[a]
        assert rr.recorder(b)["cloud_recorder_id"] == mapping[b]

        camera_calls = [kw for name, kw in cloud.calls if name == "wl_sync_recorder_cameras"]
        assert {kw["p_recorder_id"] for kw in camera_calls} == set(mapping.values())
        assert all(kw["p_cameras"][0]["channel"] == "1" for kw in camera_calls)

        capability_calls = [
            kw for name, kw in cloud.calls if name == "wl_sync_recorder_capabilities"
        ]
        assert {kw["p_recorder_id"] for kw in capability_calls} == set(mapping.values())

        assert rr.recorder(a)["vendor"] == "Hikvision"
        assert rr.recorder(b)["vendor"] == "Dahua"


def test_incomplete_cloud_contract_aborts_before_any_identity_mutation():
    with Env() as env:
        a, b, base = seed_two(env.root)

        class ContractCloud(FakeCloud):
            def call(self, name, **kw):
                if name == "wl_multi_recorder_agent_contract":
                    self.calls.append((name, kw))
                    return {
                        "ok": True,
                        "version": 1,
                        "features": ["recorders", "recorder_cameras"],
                    }
                return super().call(name, **kw)

        mapping = {
            a: "11111111-1111-1111-1111-111111111111",
            b: "22222222-2222-2222-2222-222222222222",
        }
        cloud = ContractCloud(mapping)
        probes = []
        try:
            mro.prepare_recorders(
                base, {"agent_id": "agent", "agent_key": "key"}, cloud,
                lambda cfg: probes.append(cfg) or (_ for _ in ()).throw(AssertionError()),
            )
            assert False, "incomplete backend contract must fail closed"
        except RuntimeError as exc:
            assert "contract version" in str(exc) or "contract missing" in str(exc)

        assert probes == []
        assert rr.recorder(a)["cloud_recorder_id"] is None
        assert rr.recorder(b)["cloud_recorder_id"] is None
        assert [name for name, _ in cloud.calls] == [
            "wl_multi_recorder_agent_contract"
        ]


def test_missing_configured_continuity_context_fails_before_cloud_mutation(monkeypatch):
    """Runtime must agree with contract-v4 local/DB continuity invariant.

    A corrupted/hand-edited local state that leaves the immutable continuity
    recorder outside configured runtime contexts must stop before any legacy
    spool/health stamping or secondary identity sync.
    """
    with Env() as env:
        a, b, base = seed_two(env.root)
        rows = rr.load_registry()
        # Persist the inferred continuity owner first, then emulate a corrupted
        # runtime loader that omitted it despite the registry saying configured.
        rr.save_registry(rows)
        contexts = runtime_contexts = __import__("recorder_runtime").load_contexts(base)
        continuity = next(ctx for ctx in contexts if ctx.continuity_owner)
        others = [ctx for ctx in contexts if ctx.local_id != continuity.local_id]
        assert len(others) == 1

        mapping = {
            a: "11111111-1111-1111-1111-111111111111",
            b: "22222222-2222-2222-2222-222222222222",
        }
        cloud = FakeCloud(mapping)

        try:
            mro.bind_cloud_identities(
                cloud,
                {"agent_id": "agent", "agent_key": "key"},
                others,
                base_cfg=base,
            )
            assert False, "missing continuity context must fail closed"
        except RuntimeError as exc:
            assert "continuity recorder is missing" in str(exc).lower()

        assert cloud.calls == []
        assert rr.recorder(a)["cloud_recorder_id"] is None
        assert rr.recorder(b)["cloud_recorder_id"] is None


def test_incomplete_cloud_mapping_fails_before_any_probe():
    with Env() as env:
        a, b, base = seed_two(env.root)
        cloud = FakeCloud({a: "11111111-1111-1111-1111-111111111111"})
        probes = []

        try:
            mro.prepare_recorders(
                base, {"agent_id": "agent", "agent_key": "key"}, cloud,
                lambda cfg: probes.append(cfg) or (_ for _ in ()).throw(AssertionError()),
            )
            assert False, "partial cloud mapping must fail closed"
        except RuntimeError:
            pass

        assert probes == []
        # Primary may already be safely adopted/stamped; secondary is never bound
        # when the full mapping is incomplete.
        assert rr.recorder(a)["cloud_recorder_id"] == "11111111-1111-1111-1111-111111111111"
        assert rr.recorder(b)["cloud_recorder_id"] is None


def test_one_unreachable_recorder_does_not_erase_other_preflight():
    with Env() as env:
        a, b, base = seed_two(env.root)
        mapping = {
            a: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            b: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }
        cloud = FakeCloud(mapping)

        def open_driver(cfg):
            if cfg.recorder_local_id == a:
                return Driver("A"), info("Hikvision", "A", "SER-A")
            raise RuntimeError("recorder unreachable")

        prepared = mro.prepare_recorders(
            base, {"agent_id": "agent", "agent_key": "key"}, cloud, open_driver
        )
        by_local = {x.context.local_id: x for x in prepared}
        assert by_local[a].error is None
        assert by_local[a].channels[0]["channel"] == "1"
        assert by_local[b].error and "recorder unreachable" in by_local[b].error

        # Both identities were still bound before probing, so the unreachable
        # recorder can reconnect later without being confused with Recorder A.
        assert rr.recorder(a)["cloud_recorder_id"] == mapping[a]
        assert rr.recorder(b)["cloud_recorder_id"] == mapping[b]


def test_camera_sync_failure_is_recorder_local_after_identity_binding():
    with Env() as env:
        a, b, base = seed_two(env.root)
        mapping = {
            a: "aaaaaaaa-1111-1111-1111-111111111111",
            b: "bbbbbbbb-2222-2222-2222-222222222222",
        }

        class Cloud(FakeCloud):
            def call(self, name, **kw):
                if name == "wl_sync_recorder_cameras" and kw["p_recorder_id"] == mapping[b]:
                    self.calls.append((name, kw))
                    raise RuntimeError("camera sync unavailable")
                return super().call(name, **kw)

        cloud = Cloud(mapping)
        prepared = mro.prepare_recorders(
            base, {"agent_id": "agent", "agent_key": "key"}, cloud,
            lambda cfg: (
                Driver("A" if cfg.recorder_local_id == a else "B"),
                info("Hikvision" if cfg.recorder_local_id == a else "Dahua",
                     "A" if cfg.recorder_local_id == a else "B",
                     "SER-A" if cfg.recorder_local_id == a else "SER-B"),
            ),
        )
        by_local = {x.context.local_id: x for x in prepared}
        assert by_local[a].error is None
        assert by_local[b].error and "camera sync unavailable" in by_local[b].error
        assert rr.recorder(b)["cloud_recorder_id"] == mapping[b]


def test_legacy_primary_spool_is_stamped_before_secondary_cloud_sync():
    with Env() as env:
        a, b, base = seed_two(env.root)
        spool = Spool(base.spool_path, base.spool_max_rows)
        try:
            spool.add({
                "channel": "1",
                "event_type": "motion",
                "device_ts": "2026-10-02T12:00:00Z",
                "agent_ts": "2026-10-02T12:00:00Z",
                "payload": {},
            })
            spool.add({
                "channel": "2",
                "event_type": "motion",
                "device_ts": "2026-10-02T12:00:01Z",
                "agent_ts": "2026-10-02T12:00:01Z",
                "payload": {},
            })
        finally:
            spool.close()

        mapping = {
            a: "11111111-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            b: "22222222-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }
        cloud = FakeCloud(mapping)
        mro.prepare_recorders(
            base, {"agent_id": "agent", "agent_key": "key"}, cloud,
            lambda cfg: (
                Driver("A" if cfg.recorder_local_id == a else "B"),
                info("Hikvision" if cfg.recorder_local_id == a else "Dahua",
                     "A" if cfg.recorder_local_id == a else "B",
                     "SER-A" if cfg.recorder_local_id == a else "SER-B"),
            ),
        )

        spool = Spool(base.spool_path, base.spool_max_rows)
        try:
            _, rows = spool.take(10)
        finally:
            spool.close()
        assert len(rows) == 2
        assert all(row["recorder_id"] == mapping[a] for row in rows)

        identity_calls = [kw for name, kw in cloud.calls if name == "wl_sync_recorders"]
        assert len(identity_calls) == 2
        assert len(identity_calls[0]["p_recorders"]) == 1
        assert len(identity_calls[1]["p_recorders"]) == 2


def test_malformed_legacy_spool_aborts_before_secondary_cloud_sync():
    with Env() as env:
        a, b, base = seed_two(env.root)
        spool = Spool(base.spool_path, base.spool_max_rows)
        try:
            spool.db.execute("insert into spool(payload) values (?)", ("not-json",))
        finally:
            spool.close()

        mapping = {
            a: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            b: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }
        cloud = FakeCloud(mapping)
        probes = []
        try:
            mro.prepare_recorders(
                base, {"agent_id": "agent", "agent_key": "key"}, cloud,
                lambda cfg: probes.append(cfg) or (_ for _ in ()).throw(AssertionError()),
            )
            assert False, "malformed legacy backlog must stop cutover"
        except json.JSONDecodeError:
            pass

        identity_calls = [kw for name, kw in cloud.calls if name == "wl_sync_recorders"]
        assert len(identity_calls) == 1
        assert [r["local_key"] for r in identity_calls[0]["p_recorders"]] == [a]
        assert rr.recorder(a)["cloud_recorder_id"] == mapping[a]
        assert rr.recorder(b)["cloud_recorder_id"] is None
        assert probes == []


def test_legacy_primary_health_is_stamped_before_secondary_cloud_sync():
    with Env() as env:
        a, b, base = seed_two(env.root)
        store = HealthStore(base.health_store_path, "agent")
        try:
            store.observe(
                "camera", "1", "offline", "probe_timeout", "probe",
                "2026-10-02T12:10:00Z",
            )
            store.checkpoint(
                "2026-10-02T12:10:01Z", "degraded", 1, True
            )
        finally:
            store.close()

        base.last_live_path.write_text(
            '{"last_live":"2026-10-02T12:00:00+00:00"}',
            encoding="utf-8",
        )

        mapping = {
            a: "aaaaaaaa-1111-1111-1111-111111111111",
            b: "bbbbbbbb-2222-2222-2222-222222222222",
        }
        cloud = FakeCloud(mapping)
        mro.prepare_recorders(
            base, {"agent_id": "agent", "agent_key": "key"}, cloud,
            lambda cfg: (
                Driver("A" if cfg.recorder_local_id == a else "B"),
                info("Hikvision" if cfg.recorder_local_id == a else "Dahua",
                     "A" if cfg.recorder_local_id == a else "B",
                     "SER-A" if cfg.recorder_local_id == a else "SER-B"),
            ),
        )

        store = HealthStore(base.health_store_path, "agent")
        try:
            batch = store.export_batch(20)
            assert len(batch["transitions"]) == 1
            assert len(batch["checkpoints"]) == 1
            assert batch["transitions"][0]["recorder_id"] == mapping[a]
            assert batch["checkpoints"][0]["recorder_id"] == mapping[a]

            states = store.db.execute(
                "select key from last_state order by key"
            ).fetchall()
            assert [row["key"] for row in states] == [
                f"{mapping[a]}:camera:1"
            ]
        finally:
            store.close()

        # Primary cutover reuses the original durable files; it does not move
        # the outage boundary or strand the pre-upgrade health queue.
        assert base.health_store_path.exists()
        assert base.last_live_path.exists()
        assert "2026-10-02T12:00:00" in base.last_live_path.read_text(encoding="utf-8")

        identity_calls = [kw for name, kw in cloud.calls if name == "wl_sync_recorders"]
        assert len(identity_calls) == 2
        assert [r["local_key"] for r in identity_calls[0]["p_recorders"]] == [a]
        assert {r["local_key"] for r in identity_calls[1]["p_recorders"]} == {a, b}


def test_conflicting_primary_health_identity_aborts_before_secondary_cloud_sync():
    with Env() as env:
        a, b, base = seed_two(env.root)
        other = "cccccccc-3333-3333-3333-333333333333"
        store = HealthStore(base.health_store_path, "agent")
        try:
            store.observe(
                "camera", "1", "offline", "probe_timeout", "probe",
                "2026-10-02T12:20:00Z", recorder_id=other,
            )
            # Also include one legacy row to prove the cutover validates the
            # entire store before changing any NULL provenance.
            store.observe(
                "camera", "2", "offline", "probe_timeout", "probe",
                "2026-10-02T12:20:01Z",
            )
        finally:
            store.close()

        mapping = {
            a: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            b: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }
        cloud = FakeCloud(mapping)
        probes = []
        try:
            mro.prepare_recorders(
                base, {"agent_id": "agent", "agent_key": "key"}, cloud,
                lambda cfg: probes.append(cfg) or (_ for _ in ()).throw(AssertionError()),
            )
            assert False, "conflicting health provenance must stop cutover"
        except ValueError as exc:
            assert "another recorder" in str(exc)

        identity_calls = [kw for name, kw in cloud.calls if name == "wl_sync_recorders"]
        assert len(identity_calls) == 1
        assert [r["local_key"] for r in identity_calls[0]["p_recorders"]] == [a]
        assert rr.recorder(a)["cloud_recorder_id"] == mapping[a]
        assert rr.recorder(b)["cloud_recorder_id"] is None
        assert probes == []

        store = HealthStore(base.health_store_path, "agent")
        try:
            rows = store.db.execute(
                "select entity,recorder_id from transitions order by seq"
            ).fetchall()
            # No partial stamping: the legacy row stays NULL because validation
            # failed before the transaction began.
            assert [(r["entity"], r["recorder_id"]) for r in rows] == [
                ("1", other),
                ("2", None),
            ]
        finally:
            store.close()


# --- failure isolation at startup (MNVR-009, MNVR-021) ----------------------------

import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402

STATE = {"agent_id": "agent", "agent_key": "key", "site_id": "site", "tenant_id": "tenant"}
MAPPING_IDS = (
    "aaaaaaaa-0000-4000-8000-000000000001",
    "bbbbbbbb-0000-4000-8000-000000000002",
)


def _bound_two(root):
    a, b, base = seed_two(root)
    rr.apply_cloud_mapping({a: MAPPING_IDS[0], b: MAPPING_IDS[1]})
    return a, b, base


def test_one_corrupt_recorder_credential_does_not_block_sibling():
    with Env() as env:
        a, b, base = seed_two(env.root)
        cs.recorder_credential_path(b).write_text("CORRUPT", encoding="utf-8")
        cloud = FakeCloud({a: MAPPING_IDS[0], b: MAPPING_IDS[1]})
        probed = []

        def open_driver(cfg):
            probed.append(cfg.recorder_local_id)
            return Driver("A"), info("Hikvision", "A", "SER-A")

        prepared = mro.prepare_recorders(base, STATE, cloud, open_driver)

        by_local = {x.context.local_id: x for x in prepared}
        assert probed == [a], "a recorder without a usable login is never contacted"
        assert by_local[a].error is None and by_local[a].camera_mapping
        assert by_local[b].error and "login" in by_local[b].error
        assert by_local[b].context.credential_error
        assert by_local[b].context.config.nvr_password == ""
        # Identity is non-secret: both recorders are still bound to WatchLog.
        assert rr.recorder(a)["cloud_recorder_id"] == MAPPING_IDS[0]
        assert rr.recorder(b)["cloud_recorder_id"] == MAPPING_IDS[1]


def test_offline_recorder_probe_does_not_delay_its_sibling():
    with Env() as env:
        a, b, base = seed_two(env.root)
        cloud = FakeCloud({a: MAPPING_IDS[0], b: MAPPING_IDS[1]})
        release = threading.Event()
        started = {}
        t0 = time.monotonic()

        def open_driver(cfg):
            started[cfg.recorder_local_id] = time.monotonic() - t0
            if cfg.recorder_local_id == a:
                release.wait(5)           # A's connect hangs (offline recorder)
                raise RuntimeError("recorder unreachable")
            return Driver("B"), info("Dahua", "B", "SER-B")

        threading.Timer(1.5, release.set).start()
        prepared = mro.prepare_recorders(base, STATE, cloud, open_driver)

        assert started[b] < 0.5, f"B's probe waited {started[b]:.2f}s behind A"
        by_local = {x.context.local_id: x for x in prepared}
        assert by_local[b].error is None
        assert "unreachable" in (by_local[a].error or "")


def test_probe_wait_returns_promptly_and_late_results_are_delivered():
    with Env() as env:
        a, b, base = seed_two(env.root)
        cloud = FakeCloud({a: MAPPING_IDS[0], b: MAPPING_IDS[1]})
        release = threading.Event()

        def open_driver(cfg):
            if cfg.recorder_local_id == a:
                release.wait(5)
            return Driver("X"), info("Hikvision", "X", "SER-X")

        t0 = time.monotonic()
        prepared = mro.prepare_recorders(base, STATE, cloud, open_driver, probe_wait=0)
        assert time.monotonic() - t0 < 0.5
        late = {x.context.local_id: x for x in prepared}[a]
        assert late.pending and late.camera_mapping is None
        seen = []
        late.on_ready(lambda item: seen.append(item.camera_mapping))
        release.set()
        assert late.ready.wait(5)
        assert not late.pending and late.error is None
        assert seen and seen[0] == {"1": f"camera-{MAPPING_IDS[0]}-1"}


def _offline_cfg(base):
    base.analytics_enabled = False
    base.supabase_url = "https://cloud.invalid"
    base.publishable_key = "pk"
    base.health_batch = 4
    base.health_concurrency = 1
    base.health_seconds = 300
    base.upload_seconds = 15
    base.recovery_seconds = 300
    base.snapshots = False
    return base


class _Offline:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        raise requests.ConnectionError("network not ready")


def _run_offline_boot(monkeypatch, base, cloud):
    import analytics_agent
    import multi_recorder_fanout as fanout
    import watchlog_agent as core

    def fake_collector(cfg, spool, stop, holder):
        holder["recorder_live_at"] = time.monotonic()
        spool.add({"recorder_id": cfg.recorder_cloud_id, "channel": "1",
                   "event_type": "motion", "device_ts": "2026-10-05T09:00:00Z",
                   "agent_ts": "2026-10-05T09:00:00Z", "payload": {}})
        stop.wait()

    captured = {}
    real_run = fanout.run

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(core, "collector", fake_collector)
    monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0.2)
    monkeypatch.setattr(core, "health_cycle", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "heartbeat", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "upload_once", lambda *_a, **_k: (_ for _ in ()).throw(
        RuntimeError("cloud unreachable")))
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    monkeypatch.setattr(core.vision, "build", lambda *_a, **_k: None)
    monkeypatch.setattr(fanout, "run", capture)
    analytics_agent.enhanced_cmd_run(base, STATE, cloud, once=True)
    return captured


def test_cloud_unreachable_at_boot_starts_bound_recorders(monkeypatch):
    with Env() as env:
        a, b, base = _bound_two(env.root)
        cloud = _Offline()

        captured = _run_offline_boot(monkeypatch, _offline_cfg(base), cloud)

        prepared = captured["prepared_recorders"]
        assert {p.context.cloud_recorder_id for p in prepared} == set(MAPPING_IDS)
        assert callable(captured.get("recorder_check")), "binding is retried in the background"
        queued = {}
        for item in prepared:
            spool = Spool(item.context.config.spool_path, 1000)
            try:
                queued[item.context.cloud_recorder_id] = [
                    row["recorder_id"] for row in spool.take(10)[1]]
            finally:
                spool.close()
        assert queued == {MAPPING_IDS[0]: [MAPPING_IDS[0]], MAPPING_IDS[1]: [MAPPING_IDS[1]]}
        assert rr.recorder(a)["cloud_recorder_id"] == MAPPING_IDS[0]


def test_offline_boot_with_an_unbound_recorder_still_fails_closed(monkeypatch):
    with Env() as env:
        a, b, base = seed_two(env.root)
        rr.apply_cloud_mapping({a: MAPPING_IDS[0]})          # B was never bound
        with pytest.raises(SystemExit, match="preflight did not complete"):
            _run_offline_boot(monkeypatch, _offline_cfg(base), _Offline())


def test_definitive_refusal_still_fails_closed_with_saved_bindings(monkeypatch):
    import watchlog_agent as core

    class Refused(_Offline):
        def call(self, name, **kw):
            self.calls.append((name, kw))
            raise core.CloudError(name, 400, "P0001", "recorder registry refused")

    with Env() as env:
        _a, _b, base = _bound_two(env.root)
        with pytest.raises(SystemExit, match="preflight did not complete"):
            _run_offline_boot(monkeypatch, _offline_cfg(base), Refused())


def test_background_bind_tolerates_a_corrupt_sibling_credential(monkeypatch):
    import analytics_agent
    with Env() as env:
        a, b, base = _bound_two(env.root)
        cs.recorder_credential_path(b).write_text("CORRUPT", encoding="utf-8")
        monkeypatch.setattr(analytics_agent, "RECORDER_RECHECK_SECONDS", (0.01,))
        cloud = FakeCloud({a: MAPPING_IDS[0], b: MAPPING_IDS[1]})
        restart = {}
        analytics_agent._retry_recorder_preflight(base, STATE, cloud, threading.Event(),
                                                  restart, "bind")
        assert restart == {}
        assert "wl_sync_recorders" in [name for name, _ in cloud.calls]


def test_offline_recorder_does_not_hold_back_its_siblings_collector(monkeypatch):
    """WP-8 acceptance: with B unreachable at start, A's collector starts within 0.5 s."""
    import multi_recorder_fanout as fanout
    import watchlog_agent as core

    with Env() as env:
        a, b, base = seed_two(env.root)
        base = _offline_cfg(base)
        cloud = FakeCloud({a: MAPPING_IDS[0], b: MAPPING_IDS[1]})
        release = threading.Event()

        def open_driver(cfg):
            if cfg.recorder_local_id == b:
                release.wait(5)           # B's connect hangs
                raise RuntimeError("recorder unreachable")
            return Driver("A"), info("Hikvision", "A", "SER-A")

        started = {}
        t0 = time.monotonic()

        def fake_collector(cfg, spool, stop, holder):
            started[cfg.recorder_local_id] = time.monotonic() - t0
            stop.wait()

        monkeypatch.setattr(core, "collector", fake_collector)
        monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0.1)
        monkeypatch.setattr(core, "upload_once", lambda *_a, **_k: 0)
        monkeypatch.setattr(core, "health_cycle", lambda *_a, **_k: None)
        monkeypatch.setattr(core, "heartbeat", lambda *_a, **_k: None)
        monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
        try:
            import analytics_agent
            prepared = mro.prepare_recorders(
                base, STATE, cloud, open_driver,
                probe_wait=analytics_agent.MULTI_RECORDER_PROBE_WAIT_SECONDS)
            fanout.run(base, STATE, cloud, once=True, prepared_recorders=prepared,
                       detector=None, analytics_worker=lambda *_a: None,
                       archive_worker=lambda *_a: None)
        finally:
            release.set()
        assert started[a] < 0.5, f"A's collector waited {started[a]:.2f}s behind B"
        assert b in started


def test_only_recorder_without_a_readable_login_holds_instead_of_crash_looping(monkeypatch):
    import analytics_agent
    import watchlog_agent as core

    with Env() as env:
        a = str(uuid.uuid4())
        cs.save_recorder_credential(a, "a-user", "a-pw")
        rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": [{
            "local_id": a, "display_name": "Recorder A", "url": "http://192.0.2.10",
            "driver": "onvif", "is_primary": True, "is_configured": True,
        }]})
        cs.recorder_credential_path(a).write_text("CORRUPT", encoding="utf-8")
        base = _offline_cfg(SimpleNamespace(
            state_path=env.root / "agent_state.json", spool_path=env.root / "spool.sqlite",
            health_store_path=env.root / "health.sqlite",
            last_live_path=env.root / "last_live.json", spool_max_rows=1000,
            nvr_url="legacy", nvr_driver="auto", nvr_username="legacy",
            nvr_password="legacy", heartbeat_seconds=60, recovery_enabled=False))
        cloud = FakeCloud({a: MAPPING_IDS[0]})
        opened = []
        monkeypatch.setattr(core, "open_driver", lambda cfg: opened.append(cfg) or (
            Driver("A"), info("Hikvision", "A", "SER-A")))

        with pytest.raises(SystemExit, match="login on this PC cannot be read"):
            analytics_agent.enhanced_cmd_run(base, STATE, cloud, once=True)
        assert opened == [], "an empty login is never tried against the recorder"
        assert rr.recorder(a)["cloud_recorder_id"] == MAPPING_IDS[0]

        # Run mode holds and re-checks; once Setup repairs the login it restarts cleanly.
        health = []
        monkeypatch.setattr(core, "update_runtime_health", lambda **kw: health.append(kw))
        monkeypatch.setattr(analytics_agent, "REGISTRY_RECHECK_SECONDS", 0)
        checks = []

        def sleep(_seconds):
            checks.append(1)
            if len(checks) == 2:
                cs.save_recorder_credential(a, "a-user", "a-pw2")
            if len(checks) > 5:
                raise AssertionError("never released")

        monkeypatch.setattr(analytics_agent.time, "sleep", sleep)
        with pytest.raises(SystemExit, match="restarting"):
            analytics_agent.enhanced_cmd_run(base, STATE, cloud, once=False)
        assert health[0] == {"recorder_credential": "unavailable"}
        assert health[-1] == {"recorder_credential": "ok"}
        assert opened == []
