"""Disabling a recorder down to one keeps the site uploading (MNVR-003).

FakeCloud emulates the recorder-foundation database rules that matter here:

* an event WITHOUT recorder_id resolves through the legacy single-recorder path,
  which fails closed (42501) while more than one recorder is configured;
* an event WITH recorder_id is accepted only for a configured recorder of the site;
* wl_sync_recorders is a desired-state sync (0154): a non-empty payload must name
  exactly one primary, that primary must be configured, and the continuity
  recorder can never be disabled.

So the disable must reach the cloud before the change is committed locally, the
Agent must keep using recorder-aware ingest with the remaining recorder's id, and
the disabled recorder's queue must be drained while the cloud still accepts it,
or else retained locally and reported, never silently stranded.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import credential_store as cs  # noqa: E402
import multi_recorder_orchestrator as mro  # noqa: E402
import recorder_registry as rr  # noqa: E402
import setup_backend as sb  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402
from drivers.base import Channel, DeviceInfo, Event  # noqa: E402
from spool import Spool  # noqa: E402

CLOUD_A = "aaaaaaaa-0000-4000-8000-00000000000a"
CLOUD_B = "bbbbbbbb-0000-4000-8000-00000000000b"
STATE = {"agent_id": "agent", "agent_key": "key", "site_id": "site", "tenant_id": "tenant"}


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
        self.ini.write_text(
            "[watchlog]\nsupabase_url = https://example.invalid\n"
            "supabase_publishable_key = test\n", encoding="utf-8")
        return self

    def __exit__(self, *_args):
        cs.write_json_secret, cs.read_json_secret = self.saved_crypto
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


class FakeCloud:
    """Emulates the recorder-foundation ingest and lifecycle rules."""

    def __init__(self, local_a, local_b, *, b_configured=True):
        self.by_local = {local_a: CLOUD_A, local_b: CLOUD_B}
        self.configured = {CLOUD_A: True, CLOUD_B: b_configured}
        self.primary = CLOUD_A
        self.calls = []
        self.ingested = []
        self.fail_sync = False

    def __call__(self, *_a, **_k):
        return self

    def names(self):
        return [name for name, _ in self.calls]

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_multi_recorder_agent_contract":
            return {"ok": True, "version": mro.MULTI_RECORDER_CONTRACT_VERSION,
                    "configured_recorders": sum(self.configured.values()),
                    "features": sorted(mro.MULTI_RECORDER_REQUIRED_FEATURES)}
        if name == "wl_sync_recorders":
            if self.fail_sync:
                raise core.CloudError(name, 503, None, "temporarily unavailable")
            rows = kw["p_recorders"]
            if rows and sum(bool(r.get("is_primary")) for r in rows) != 1:
                raise core.CloudError(name, 400, "22023",
                                      "recorder registry requires exactly one primary recorder")
            if any(r.get("is_primary") and not r.get("is_configured", True) for r in rows):
                raise core.CloudError(name, 400, "22023", "primary recorder must be configured")
            out = {}
            for row in rows:
                cloud_id = self.by_local[str(row["local_key"])]
                if cloud_id == CLOUD_A and not row.get("is_configured", True):
                    raise core.CloudError(name, 403, "42501",
                                          "continuity recorder cannot be disabled in contract v4")
                self.configured[cloud_id] = bool(row.get("is_configured", True))
                if row.get("is_primary"):
                    self.primary = cloud_id
                out[str(row["local_key"])] = cloud_id
            return out
        if name == "wl_ingest_events":
            events = kw["p_events"]
            if any(not e.get("recorder_id") for e in events):
                if sum(self.configured.values()) != 1:
                    raise core.CloudError(name, 403, "42501",
                                          "legacy recorder path is ambiguous for multi-recorder site")
            for e in events:
                rid = e.get("recorder_id")
                if rid and not self.configured.get(rid):
                    raise core.CloudError(name, 403, "42501",
                                          "event recorder not configured for this agent site")
            self.ingested.extend(events)
            return {"received": len(events), "inserted": len(events), "skipped": 0}
        if name == "wl_sync_recorder_cameras":
            return {str(c["channel"]): f"cam-{kw['p_recorder_id'][:4]}-{c['channel']}"
                    for c in kw["p_cameras"]}
        if name in ("wl_sync_recorder_capabilities", "wl_heartbeat"):
            return {"ok": True}
        raise AssertionError(f"unexpected RPC {name}")


def _seed_two_bound():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    cs.save_recorder_credential(a, "user-a", "pw-a")
    cs.save_recorder_credential(b, "user-b", "pw-b")
    rr.save_registry({
        "schema": rr.REGISTRY_SCHEMA,
        "recorders": [
            {"local_id": a, "cloud_recorder_id": CLOUD_A, "display_name": "Recorder A",
             "url": "http://192.0.2.10", "driver": "hikvision", "is_primary": True,
             "continuity_owner": True, "is_configured": True},
            {"local_id": b, "cloud_recorder_id": CLOUD_B, "display_name": "Recorder B",
             "url": "http://192.0.2.20", "driver": "dahua-cgi", "is_primary": False,
             "continuity_owner": False, "is_configured": True},
        ],
    })
    return a, b


def _queue_for(root: Path, local_id: str, recorder_id: str, count: int) -> Path:
    path = root / "recorders" / local_id / "spool.sqlite"
    spool = Spool(path)
    try:
        for i in range(count):
            spool.add({"channel": "1", "event_type": "motion", "recorder_id": recorder_id,
                       "device_ts": f"2026-10-02T12:00:0{i}Z",
                       "agent_ts": f"2026-10-02T12:00:0{i}Z", "payload": {}})
    finally:
        spool.close()
    return path


def _queued(path: Path) -> int:
    spool = Spool(path)
    try:
        return spool.count()
    finally:
        spool.close()


def _activation_ok(monkeypatch):
    monkeypatch.setattr(sb, "ensure_background_agent",
                        lambda *a, **k: {"started": True, "detail": "test"})
    monkeypatch.setattr(sb, "confirm_background_agent",
                        lambda *a, **k: {"confirmed": True, "detail": "test"})
    # The enrolled Agent identity Setup acts with (DPAPI is Windows-only).
    monkeypatch.setattr(sb, "_load_existing_identity", lambda _path: dict(STATE))


def test_disable_drains_then_syncs_cloud_before_committing_locally(monkeypatch):
    with _Env() as env:
        a, b = _seed_two_bound()
        queue = _queue_for(env.root, b, CLOUD_B, 2)
        cloud = FakeCloud(a, b)
        monkeypatch.setattr(core, "Cloud", cloud)
        _activation_ok(monkeypatch)

        seen_local_state = []
        real_sync = cloud.call

        def spy(name, **kw):
            if name == "wl_sync_recorders":
                seen_local_state.append(rr.recorder(b)["is_configured"])
            return real_sync(name, **kw)

        cloud.call = spy
        out = sb.disable_managed_recorder(env.ini, b)

        names = cloud.names()
        # B's queue went up while B was still configured in the cloud ...
        assert [e["recorder_id"] for e in cloud.ingested] == [CLOUD_B, CLOUD_B]
        assert names.index("wl_ingest_events") < names.index("wl_sync_recorders")
        assert _queued(queue) == 0
        assert out["retained_events"] == 0
        # ... then the cloud learned B is disabled before the PC committed it.
        # The whole planned registry goes up, as the database requires: the
        # configured primary A plus the disabled B.
        sync = [kw for name, kw in cloud.calls if name == "wl_sync_recorders"]
        assert len(sync) == 1
        sent = {r["local_key"]: r for r in sync[0]["p_recorders"]}
        assert set(sent) == {a, b}
        assert sent[a]["is_primary"] is True and sent[a]["is_configured"] is True
        assert sent[b]["is_primary"] is False and sent[b]["is_configured"] is False
        assert seen_local_state == [True]
        assert cloud.configured == {CLOUD_A: True, CLOUD_B: False}
        assert cloud.primary == CLOUD_A
        assert rr.recorder(b)["is_configured"] is False
        assert out["is_configured"] is False


def test_cloud_refusal_leaves_the_recorder_enabled_locally(monkeypatch):
    with _Env() as env:
        a, b = _seed_two_bound()
        cloud = FakeCloud(a, b)
        cloud.fail_sync = True
        monkeypatch.setattr(core, "Cloud", cloud)
        _activation_ok(monkeypatch)
        before = rr.load_registry()

        try:
            sb.disable_managed_recorder(env.ini, b)
            assert False, "an unconfirmed cloud change must not be committed locally"
        except ValueError as exc:
            assert "nothing was changed" in str(exc).lower()
        assert rr.load_registry() == before
        assert cloud.configured[CLOUD_B] is True


def test_disable_never_sends_an_unbound_recorder(monkeypatch):
    """A recorder the Agent has not bound yet has no cloud row; Setup must not
    create one while disabling another recorder."""
    with _Env() as env:
        a, b = _seed_two_bound()
        c = str(uuid.uuid4())
        cs.save_recorder_credential(c, "user-c", "pw-c")
        reg = rr.load_registry()
        reg["recorders"].append(
            {"local_id": c, "cloud_recorder_id": None, "display_name": "Recorder C",
             "url": "http://192.0.2.30", "driver": "onvif", "is_primary": False,
             "continuity_owner": False, "is_configured": True})
        rr.save_registry(reg)
        cloud = FakeCloud(a, b)
        monkeypatch.setattr(core, "Cloud", cloud)
        _activation_ok(monkeypatch)

        sb.disable_managed_recorder(env.ini, b)

        sync = [kw for name, kw in cloud.calls if name == "wl_sync_recorders"]
        assert [{r["local_key"] for r in kw["p_recorders"]} for kw in sync] == [{a, b}]
        assert rr.recorder(b)["is_configured"] is False
        assert rr.recorder(c)["is_configured"] is True
        assert rr.recorder(c)["cloud_recorder_id"] is None


def test_a_partial_echo_is_not_a_confirmation(monkeypatch):
    with _Env() as env:
        a, b = _seed_two_bound()
        cloud = FakeCloud(a, b)
        real = cloud.call

        def only_b(name, **kw):
            out = real(name, **kw)
            if name == "wl_sync_recorders":
                return {b: out[b]}
            return out

        cloud.call = only_b
        monkeypatch.setattr(core, "Cloud", cloud)
        _activation_ok(monkeypatch)
        before = rr.load_registry()

        try:
            sb.disable_managed_recorder(env.ini, b)
            assert False, "an echo missing a sent recorder must not be committed locally"
        except ValueError as exc:
            assert "nothing was changed" in str(exc).lower()
        assert rr.load_registry() == before


def test_undrainable_queue_is_retained_and_reported(monkeypatch):
    with _Env() as env:
        a, b = _seed_two_bound()
        queue = _queue_for(env.root, b, CLOUD_B, 3)
        cloud = FakeCloud(a, b)
        real = cloud.call

        def ingest_down(name, **kw):
            if name == "wl_ingest_events":
                cloud.calls.append((name, kw))
                raise core.CloudError(name, 503, None, "temporarily unavailable")
            return real(name, **kw)

        cloud.call = ingest_down
        monkeypatch.setattr(core, "Cloud", cloud)
        _activation_ok(monkeypatch)

        out = sb.disable_managed_recorder(env.ini, b)
        assert out["retained_events"] == 3
        assert _queued(queue) == 3            # kept on this PC, never deleted
        assert rr.recorder(b)["is_configured"] is False


# --- Agent runtime after the disable ----------------------------------------

def _agent_cfg(root: Path):
    return SimpleNamespace(
        supabase_url="https://example.invalid", publishable_key="test",
        state_path=root / "agent_state.json", spool_path=root / "spool.sqlite",
        spool_max_rows=1000, health_store_path=root / "health.sqlite",
        last_live_path=root / "last_live.json",
        nvr_url="http://192.0.2.10", nvr_driver="hikvision",
        nvr_username="legacy", nvr_password="legacy",
        snapshots=False, snapshot_min_interval=60,
        site_control_enabled=False, site_control_seconds=15,
        analytics_enabled=False, recovery_enabled=False,
        heartbeat_seconds=60, upload_seconds=15, health_seconds=300,
        health_batch=4, health_concurrency=2,
    )


class _LiveDriver:
    name = "hikvision"
    verified_against_hardware = False

    def list_channels(self):
        return [Channel("1", "Gate")]

    def capabilities(self):
        return {"channels": []}

    def stream_events(self, stop):
        yield Event("1", "motion", datetime(2026, 10, 2, 12, 5, tzinfo=timezone.utc),
                    payload={"source": "test"})
        stop.wait(5)

    def get_snapshot(self, _channel):
        return None

    def close(self):
        pass


def test_agent_keeps_recorder_aware_ingest_after_disabling_down_to_one(monkeypatch):
    """The original failure: B disabled locally while the cloud still had it
    configured. Startup must sync B's disabled state before the first upload, and
    every event (legacy backlog and live) must carry Recorder A's id."""
    with _Env() as env:
        a, b = _seed_two_bound()
        rr.disable_recorder(b)                       # local-only change (older Setup)
        retained = _queue_for(env.root, b, CLOUD_B, 2)
        legacy = Spool(env.root / "spool.sqlite")    # pre-cutover singleton backlog
        try:
            legacy.add({"channel": "2", "event_type": "motion",
                        "device_ts": "2026-10-02T11:00:00Z",
                        "agent_ts": "2026-10-02T11:00:00Z", "payload": {}})
        finally:
            legacy.close()

        cloud = FakeCloud(a, b, b_configured=True)
        logs = []
        monkeypatch.setattr(core, "log", lambda msg: logs.append(str(msg)))
        monkeypatch.setattr(core, "open_driver", lambda cfg: (
            _LiveDriver(), DeviceInfo(vendor="Hikvision", model="A", driver="hikvision")))
        monkeypatch.setattr(core, "health_cycle", lambda *a, **k: None)
        monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
        monkeypatch.setattr(core, "ONCE_COLLECT_SECONDS", 0.3)
        monkeypatch.setattr(core.vision, "build", lambda *_a, **_k: None)

        cfg = _agent_cfg(env.root)
        analytics_agent.enhanced_cmd_run(cfg, STATE, cloud, once=True)

        names = cloud.names()
        assert "wl_ingest_events" in names
        first_ingest = names.index("wl_ingest_events")
        disabled_sync = [
            i for i, (name, kw) in enumerate(cloud.calls)
            if name == "wl_sync_recorders"
            and any(r["local_key"] == b and not r["is_configured"] for r in kw["p_recorders"])
        ]
        assert disabled_sync and disabled_sync[0] < first_ingest
        assert cloud.configured == {CLOUD_A: True, CLOUD_B: False}

        assert len(cloud.ingested) == 2               # legacy backlog + live event
        assert {e.get("recorder_id") for e in cloud.ingested} == {CLOUD_A}
        assert _queued(env.root / "spool.sqlite") == 0

        # The disabled recorder's queue is retained, not deleted, and reported.
        assert _queued(retained) == 2
        assert any("Recorder B" in line and "2 queued" in line and "retained" in line
                   for line in logs)
