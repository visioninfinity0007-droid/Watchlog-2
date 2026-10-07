#!/usr/bin/env python3
"""Agent runtime status + capability reporting (5.1.2, agent/runtime_status.py, migration 0162).

- Capabilities are reported from the main loop, independent of analytics: with analytics
  disabled (the real analytics_worker returns at once) the shipped singleton loop still calls
  wl_agent_report_capabilities, and the analytics worker no longer reports them itself.
- site_control_runtime / remote_update_v1 follow the worker registry: advertised while the
  poller is alive and recently succeeded, withdrawn (and re-reported at once) when it dies.
- The runtime status payload: worker rows with recorder scope, one row per recorder (event
  stream up/down/unknown, login unavailable, queue), agent-level fields; bounded; redacted
  (no address, credential or display name); a server without 0162 turns reporting off.
- The multi-recorder fan-out reports each recorder's workers and stream separately.
- The SQL worker/state lists in 0162 match the Agent's.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parents[1] / "agent"
MIGRATION = Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "0162_agent_runtime_status.sql"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import multi_recorder_fanout as fanout  # noqa: E402
import runtime_status  # noqa: E402
import watchlog_agent as core  # noqa: E402
import worker_supervisor as ws  # noqa: E402

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


class FakeCloud:
    def __init__(self, missing=()):
        self.calls = []
        self.missing = set(missing)
        self.lock = threading.Lock()

    def call(self, fn, **params):
        with self.lock:
            self.calls.append((fn, params))
        if fn in self.missing:
            raise core.CloudError(fn, 404, "PGRST202", "Could not find the function")
        if fn == "wl_agent_claim_command":
            return {"command": None}
        return {"ok": True}

    def named(self, fn):
        with self.lock:
            return [p for f, p in self.calls if f == fn]


class Clock:
    def __init__(self, start=5000.0):
        self.now = start

    def __call__(self):
        return self.now


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeSpool:
    def __init__(self, *args, **kwargs):
        self.max_rows = 1000

    def count(self):
        return 7

    def pending_recovery_gap(self):
        return None

    def close(self):
        pass


# ---------------------------------------------------------------- capability independence
def test_capabilities_are_reported_with_analytics_disabled(monkeypatch, tmp_path):
    ws.reset_default()
    cloud = FakeCloud()
    ready = threading.Event()

    def collector(cfg, spool, stop, holder):
        ready.set()
        stop.wait(10)

    def idle(*_a, **_k):
        return None

    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            ready.wait(5)
        elif sleeps["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(analytics_agent.recorder_registry, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(core.vision, "build", lambda _cfg, _log: None)
    monkeypatch.setattr(core, "collector", collector)
    # The REAL command_worker: Site Control is off here, so it exits at once as disabled.
    for name in ("recovery_worker", "health_worker", "health_cycle"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(core, "upload_once", lambda *a, **k: 0)
    monkeypatch.setattr(core, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(analytics_agent, "Spool", FakeSpool)
    monkeypatch.setattr(analytics_agent.time, "sleep", sleep)
    monkeypatch.setattr(analytics_agent.periodic_stills, "periodic_still_worker", idle)
    monkeypatch.setattr(runtime_status, "STATUS_MIN_GAP_SECONDS", 0.0)

    cfg = SimpleNamespace(
        spool_path=tmp_path / "spool.sqlite", spool_max_rows=0, analytics_enabled=False,
        health_batch=4, health_concurrency=2, upload_seconds=15, heartbeat_seconds=0,
        health_seconds=300, recovery_enabled=False, site_control_enabled=False,
        last_live_path=tmp_path / "last_live.json", recovery_threshold_seconds=180)
    # The REAL analytics and archive workers: disabled, they return at once.
    analytics_agent.enhanced_cmd_run(cfg, {"agent_id": "a", "agent_key": "k"}, cloud,
                                     once=False, device=None, channels=[])

    caps = cloud.named("wl_agent_report_capabilities")
    assert caps, "capabilities must be reported even with analytics disabled"
    assert "agent_runtime_status_v1" in caps[0]["p_capabilities"]
    assert "site_control_runtime" not in caps[0]["p_capabilities"]
    reports = cloud.named("wl_report_agent_runtime")
    assert reports, "runtime status is reported from the main loop"
    # The disabled workers exit at once and say so; a later report carries it.
    rows = {w["worker"]: w for w in reports[-1]["p_status"]["workers"]}
    assert rows["analytics"]["state"] == "disabled" and rows["analytics"]["enabled"] is False
    assert rows["archive"]["state"] == "disabled"
    assert rows["site_control"]["state"] == "disabled"
    assert rows["collector"]["critical"] is True
    recorders = reports[0]["p_status"]["recorders"]
    assert len(recorders) == 1 and recorders[0]["spool_depth"] == 7
    ws.reset_default()


def test_analytics_worker_no_longer_reports_capabilities():
    src = (AGENT / "analytics_agent.py").read_text(encoding="utf-8")
    body = src[src.index("def analytics_worker("):src.index("def _archive_backend_missing(")]
    assert "wl_agent_report_capabilities" not in body
    for loop in ("analytics_agent.py", "multi_recorder_fanout.py", "watchlog_agent.py"):
        assert "monitor.step(clock)" in (AGENT / loop).read_text(encoding="utf-8"), loop


def test_poller_capabilities_follow_the_worker_registry():
    clock = Clock()
    sup = ws.Supervisor(clock=clock, log=lambda _m: None, backoff_base=1)
    stop = threading.Event()
    die = threading.Event()

    def poller(stop_event):
        while not stop_event.wait(0.01):
            if die.is_set():
                raise RuntimeError("site control poll thread crashed")
            ws.success()

    cfg = SimpleNamespace(site_control_enabled=True, site_control_seconds=15,
                          update_url="", site_control_last_poll_monotonic=None)
    h = sup.supervised("site_control", poller, args=(stop,), stop=stop)
    h.start()
    assert _wait(lambda: h.state == "running")
    cloud = FakeCloud()
    reporter = runtime_status.CapabilityReporter(
        cloud, {"agent_id": "a", "agent_key": "k"},
        lambda: analytics_agent.runtime_capabilities(cfg, sup))
    assert reporter.maybe_report(clock()) is True
    assert "site_control_runtime" in cloud.named("wl_agent_report_capabilities")[-1]["p_capabilities"]

    die.set()
    assert _wait(lambda: not h.is_alive())
    sup.check(clock())
    assert h.state == "restarting"
    later = clock() + runtime_status.CAPABILITY_MIN_GAP_SECONDS + 1
    assert reporter.maybe_report(later) is True, "a withdrawn capability is reported at once"
    assert "site_control_runtime" not in cloud.named("wl_agent_report_capabilities")[-1]["p_capabilities"]
    stop.set()


def test_unregistered_poller_falls_back_to_its_poll_stamp():
    sup = ws.Supervisor(log=lambda _m: None)
    cfg = SimpleNamespace(site_control_enabled=True, site_control_seconds=15,
                          site_control_last_poll_monotonic=time.monotonic(),
                          update_url="https://x", update_require_signature=False,
                          remote_update_last_poll_monotonic=time.monotonic() - 1000)
    caps = analytics_agent.runtime_capabilities(cfg, sup)
    assert "site_control_runtime" in caps and "remote_update_v1" not in caps


# ---------------------------------------------------------------- payload
def _holder(stream_connected, live, error=None):
    class Driver:
        last_activity_monotonic = time.monotonic() if live else 0.0
    return {"live_driver": Driver(),
            "event_stream": {"connected": stream_connected, "last_error": error,
                             "last_frame_at": None}}


def test_payload_shape_bounds_and_redaction():
    sup = ws.Supervisor(log=lambda _m: None)
    stop = threading.Event()
    failed = threading.Event()

    def broken(_stop):
        failed.set()
        raise RuntimeError("login refused by http://admin:hunter2@192.168.10.5:80/ISAPI")

    h = sup.supervised("health", broken, args=(stop,), stop=stop, recorder_local_id="rec-a",
                       recorder_id=A)
    h.start()
    assert failed.wait(5) and _wait(lambda: not h.is_alive())
    sup.check()
    cfg_a = SimpleNamespace(recorder_local_id="rec-a", recorder_display_name="Back Office NVR",
                            nvr_url="http://192.168.10.5", nvr_password="hunter2",
                            credential_error=None)
    cfg_b = SimpleNamespace(recorder_local_id="rec-b", recorder_display_name="Gate NVR",
                            nvr_url="http://192.168.10.6", nvr_password="pw",
                            credential_error="CredentialUnavailable")
    sources = [
        runtime_status.RecorderSource(A, _holder(True, True), cfg_a, FakeSpool()),
        runtime_status.RecorderSource(
            B, _holder(False, False, "connect to 192.168.10.6 timed out"), cfg_b, None),
    ]
    proof = {"upload_ok_at": None, "heartbeat_ok_at": None}
    payload = runtime_status.build_payload(sup, sources, cloud_proof=proof)
    text = json.dumps(payload)
    for leak in ("hunter2", "192.168.10.5", "192.168.10.6", "admin", "Back Office NVR",
                 "Gate NVR", "http://"):
        assert leak not in text, leak
    assert payload["schema"] == "agent_runtime_status_v1"
    worker = payload["workers"][0]
    assert worker["worker"] == "health" and worker["recorder_id"] == A
    assert worker["state"] == "restarting" and worker["healthy"] is False
    assert worker["last_error"].startswith("RuntimeError")
    recs = {r["recorder_id"]: r for r in payload["recorders"]}
    assert recs[A]["event_stream"] == "up" and recs[A]["credential_unavailable"] is False
    assert recs[A]["spool_depth"] == 7 and recs[A]["spool_overflow"] is False
    assert recs[B]["event_stream"] == "down" and recs[B]["credential_unavailable"] is True
    assert recs[B]["spool_depth"] is None, "an unreadable queue is unknown, never zero"
    agent = payload["agent"]
    assert agent["spool_depth"] is None and agent["spool_capacity"] == 1000
    assert agent["cloud_upload_last_success_at"] is None
    assert agent["critical_unhealthy"] == 1 and agent["healthy"] is False
    assert len(text.encode("utf-8")) < 65536
    stop.set()


def test_payload_stays_under_the_server_bound_with_many_workers():
    sup = ws.Supervisor(log=lambda _m: None)
    for i in range(60):
        h = sup.supervised("collector", lambda: None, recorder_local_id=f"r{i}",
                           recorder_id=f"{i:08d}-0000-0000-0000-000000000000")
        h._started = True
        h.last_error = "x" * 1000
    payload = runtime_status.build_payload(sup, [], cloud_proof={})
    assert len(payload["workers"]) == 60
    assert all(len(w["last_error"]) <= ws.ERROR_MAX_CHARS for w in payload["workers"])
    assert len(json.dumps(payload).encode("utf-8")) < 65536


def test_event_stream_state_needs_evidence():
    assert runtime_status.event_stream_state({}) == "unknown"
    assert runtime_status.event_stream_state(_holder(True, True)) == "up"
    assert runtime_status.event_stream_state(_holder(None, False)) == "unknown"
    assert runtime_status.event_stream_state(_holder(False, False)) == "down"
    stale = {"recorder_live_at": time.monotonic() - 600}
    assert runtime_status.event_stream_state(stale) == "down"


def test_missing_rpc_turns_reporting_off_for_the_run():
    sup = ws.Supervisor(log=lambda _m: None)
    cloud = FakeCloud(missing={"wl_report_agent_runtime"})
    reporter = runtime_status.RuntimeStatusReporter(
        cloud, {"agent_id": "a", "agent_key": "k"}, sup, lambda: [], cloud_proof={})
    assert reporter.maybe_report(0.0) is False and reporter.disabled
    assert reporter.maybe_report(1000.0) is False
    assert len(cloud.named("wl_report_agent_runtime")) == 1


def test_a_failed_report_does_not_retry_on_every_change():
    clock = Clock(100.0)
    sup = ws.Supervisor(clock=clock, log=lambda _m: None)

    class DownCloud(FakeCloud):
        def call(self, fn, **params):
            super().call(fn, **params)
            raise core.CloudError(fn, 503, None, "unavailable")

    cloud = DownCloud()
    reporter = runtime_status.RuntimeStatusReporter(
        cloud, {"agent_id": "a", "agent_key": "k"}, sup, lambda: [], cloud_proof={})
    assert reporter.maybe_report(clock()) is False and not reporter.disabled
    stop = threading.Event()
    h = sup.supervised("archive", lambda s: s.wait(), args=(stop,), stop=stop)
    h.start()
    assert reporter.maybe_report(clock() + 10) is False
    assert len(cloud.named("wl_report_agent_runtime")) == 1, "no retry storm while unreachable"
    reporter.maybe_report(clock() + runtime_status.STATUS_REPORT_SECONDS)
    assert len(cloud.named("wl_report_agent_runtime")) == 2, "retried when due"
    stop.set()
    assert _wait(lambda: not h.is_alive())


def test_status_is_reported_every_interval_and_at_once_on_change():
    clock = Clock(100.0)
    sup = ws.Supervisor(clock=clock, log=lambda _m: None)
    cloud = FakeCloud()
    reporter = runtime_status.RuntimeStatusReporter(
        cloud, {"agent_id": "a", "agent_key": "k"}, sup, lambda: [], cloud_proof={})
    assert reporter.maybe_report(clock()) is True
    assert reporter.maybe_report(clock() + 10) is False          # nothing changed, not due
    stop = threading.Event()
    h = sup.supervised("archive", lambda s: s.wait(), args=(stop,), stop=stop)
    h.start()
    clock.now += 10
    assert reporter.maybe_report(clock()) is True, "a new worker state is reported at once"
    assert reporter.maybe_report(clock() + 1) is False
    assert reporter.maybe_report(clock() + runtime_status.STATUS_REPORT_SECONDS) is True
    stop.set()
    assert _wait(lambda: not h.is_alive())


# ---------------------------------------------------------------- fan-out
def _prepared(root: Path, name: str, rid: str, primary=False, credential_error=None):
    state = root / name
    cfg = SimpleNamespace(
        recorder_cloud_id=rid, recorder_local_id=f"local-{name}", recorder_display_name=name,
        spool_path=state / "spool.sqlite", health_store_path=state / "health.sqlite",
        last_live_path=state / "last_live.json", spool_max_rows=1000,
        health_batch=4, health_concurrency=1, health_seconds=300,
        recovery_enabled=False, recovery_seconds=300, recovery_threshold_seconds=180,
        upload_seconds=15, heartbeat_seconds=0, credential_error=credential_error)
    return SimpleNamespace(
        context=SimpleNamespace(config=cfg, holder={}, cloud_recorder_id=rid,
                                display_name=name, is_primary=primary),
        device=SimpleNamespace(vendor=name, model="TEST", driver="hikvision"),
        channels=[{"channel": "1"}], capabilities=None,
        camera_mapping={"1": f"{rid[:8]}-cam"}, error=None)


def test_fanout_reports_each_recorder_separately(monkeypatch, tmp_path):
    ws.reset_default()
    cloud = FakeCloud()
    ready = threading.Event()
    seen = set()

    def collector(cfg, spool, stop, holder):
        if cfg.recorder_cloud_id == A:
            holder.update(_holder(True, True))
        else:
            holder.update(_holder(False, False, "stream dropped"))
        seen.add(cfg.recorder_cloud_id)
        ws.tick()
        if len(seen) == 2:
            ready.set()
        stop.wait(10)

    def idle(*_a, **_k):
        return None

    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            ready.wait(5)
        elif sleeps["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(core, "collector", collector)
    for name in ("recovery_worker", "health_worker", "command_worker"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(fanout.periodic_stills, "periodic_still_worker", idle)
    monkeypatch.setattr(core, "upload_once", lambda *a, **k: 0)
    monkeypatch.setattr(core, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(core, "update_runtime_health", lambda **_k: None)
    monkeypatch.setattr(fanout.time, "sleep", sleep)
    monkeypatch.setattr(runtime_status, "STATUS_MIN_GAP_SECONDS", 0.0)
    fanout.run(SimpleNamespace(recovery_enabled=False, upload_seconds=15, heartbeat_seconds=0,
                               analytics_enabled=False),
               {"agent_id": "agent", "agent_key": "key"}, cloud, once=False,
               prepared_recorders=[_prepared(tmp_path, "A", A, primary=True),
                                   _prepared(tmp_path, "B", B)],
               detector=None, analytics_worker=analytics_agent.analytics_worker,
               archive_worker=analytics_agent.archive_worker)

    reports = cloud.named("wl_report_agent_runtime")
    assert reports
    status = reports[-1]["p_status"]
    collectors = {w["recorder_id"]: w for w in status["workers"] if w["worker"] == "collector"}
    assert set(collectors) == {A, B}
    assert collectors[A]["healthy"] is True and collectors[A]["detail"]["event_stream"] == "up"
    assert collectors[B]["healthy"] is False and collectors[B]["detail"]["event_stream"] == "down"
    for name in ("health", "recovery", "periodic_stills"):
        assert {w["recorder_id"] for w in status["workers"] if w["worker"] == name} == {A, B}
    site = [w for w in status["workers"] if w["worker"] == "analytics"]
    assert len(site) == 1 and site[0]["recorder_id"] is None and site[0]["state"] == "disabled"
    streams = {r["recorder_id"]: r["event_stream"] for r in status["recorders"]}
    assert streams == {A: "up", B: "down"}
    assert cloud.named("wl_agent_report_capabilities"), "capabilities reported by the fan-out loop"
    ws.reset_default()


# ---------------------------------------------------------------- SQL contract
def _sql_array(function: str) -> list[str]:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = sql.index(f"create or replace function public.{function}()")
    body = sql[start:sql.index("$$;", start)]
    return re.findall(r"'([a-z0-9_]+)'", body[body.index("array["):])


def test_sql_worker_list_matches_the_agent():
    assert _sql_array("wl_agent_runtime_workers") == list(ws.KNOWN_WORKERS)


def test_sql_states_match_the_agent():
    sql = MIGRATION.read_text(encoding="utf-8")
    check = re.search(r"agent_runtime_status_state_chk check \(state in\s*\(([^)]*)\)\)", sql)
    assert check is not None
    assert re.findall(r"'([a-z]+)'", check.group(1)) == list(ws.STATES)


def test_known_capabilities_is_the_0156_body_plus_one():
    added = _sql_array("wl_known_capabilities")
    base = (Path(MIGRATION).parent / "0156_production_truth_hotfix.sql").read_text(encoding="utf-8")
    start = base.index("create or replace function public.wl_known_capabilities()")
    before = re.findall(r"'([a-z0-9_]+)'", base[start:base.index("$$;", start)])
    assert added == before + ["agent_runtime_status_v1"]
    assert "agent_runtime_status_v1" in analytics_agent.RUNTIME_CAPABILITIES


def test_grants_report_to_anon_read_to_authenticated():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r"grant execute on function public\.wl_report_agent_runtime\(uuid, text, jsonb\) to anon;", sql)
    assert re.search(r"revoke all on function public\.wl_report_agent_runtime\(uuid, text, jsonb\)\s+from public, anon, authenticated;", sql)
    assert re.search(r"grant execute on function public\.wl_site_agent_runtime\(uuid\) to authenticated;", sql)
    assert re.search(r"revoke all on function public\.wl_site_agent_runtime\(uuid\) from public, anon;", sql)
    assert "wl_auth_agent(p_agent_id, p_agent_key)" in sql
    assert "public.wl_is_member(v_site.tenant_id)" in sql
    for table in ("agent_runtime_status", "agent_runtime_recorders", "agent_runtime_summary"):
        assert f"alter table public.{table} enable row level security;" in sql
        assert f"revoke all on table public.{table} from public, anon, authenticated;" in sql
