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
                "version": 2,
                "configured_recorders": 1,
                "features": [
                    "recorders",
                    "recorder_cameras",
                    "recorder_events",
                    "recorder_health",
                    "recorder_recovery",
                    "recorder_reconciliation",
                    "recorder_capabilities",
                    "recorder_job_routing",
                ],
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
        assert probe_calls == [a, b]

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
