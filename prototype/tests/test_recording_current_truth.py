#!/usr/bin/env python3
"""Per-camera recording truth and disk inventory reporting (5.1.2, Agent side).

Field fact (Al-Khalid, 5.1.1): the current-proof path could only say 'recording' (recent
archive found) or 'unknown'. A Dahua channel whose RecordMode was off was reported unknown (the
mode was read and discarded), and a camera that stopped recording while live stayed unknown
forever. Now:

  * archive footage found                  -> recording, with latest_recording_at;
  * recording disabled on the recorder     -> not_recording (recording_disabled);
  * archive search failed                  -> unknown (archive_search_failed);
  * search succeeded, nothing found:
      video loss                           -> unknown (video_loss)
      continuous schedule + camera online  -> not_recording (no_recent_recording)
      anything less certain                -> unknown (no_recent_archive)

and the per-disk inventory goes to the recorder-scoped disk RPC (0161), skipped silently on an
older database.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import recording_current as rc  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DriverError  # noqa: E402

A = "aaaaaaaa-0000-4000-8000-000000000001"
STATE = {"agent_id": "agent", "agent_key": "key"}
OK_STORAGE = {"supported": True, "state": "ok", "reason": "ok",
              "disks": [{"id": "/dev/sda", "path": "/dev/sda1", "type": "ReadWrite",
                         "state": "ok", "reason": "ok", "total_bytes": 1000,
                         "free_bytes": 400}],
              "total_bytes": 1000, "free_bytes": 400}


class FakeDahua:
    name = "dahua-cgi"

    def __init__(self, *, config, video_loss=(), faults_supported=True, storage=None):
        self.config = config
        self.video_loss = list(video_loss)
        self.faults_supported = faults_supported
        self.storage = storage or OK_STORAGE

    def storage_status(self):
        return self.storage

    def recording_status(self, channels=None):
        disabled = [c for c, m in self.config.items() if m == "disabled"]
        return {"supported": True,
                "channels": {c: ("not_recording" if c in disabled else None) for c in self.config},
                "reasons": {c: "recording_disabled" for c in disabled},
                "config": dict(self.config)}

    def current_faults(self):
        if not self.faults_supported:
            return {"supported": False, "video_loss": [], "video_blind": []}
        return {"supported": True, "video_loss": self.video_loss, "video_blind": [],
                "video_loss_supported": True}


class FakeHik(FakeDahua):
    name = "hikvision-isapi"

    def __init__(self, *, online=(), offline=(), **kw):
        super().__init__(**kw)
        self.online, self.offline = list(online), list(offline)

    def channel_liveness(self):
        if not (self.online or self.offline):
            return {"supported": False, "online": [], "offline": []}
        return {"supported": True, "online": self.online, "offline": self.offline}


def dahua_archive(monkeypatch, per_channel):
    """per_channel: channel -> 'found' | 'empty' | 'unknown' | 'raise'"""
    def enumerate_events(driver, channel, start, end, cursor=None, limit=500):
        kind = per_channel.get(str(channel), "empty")
        if kind == "raise":
            raise DriverError("recorder busy")
        if kind == "found":
            return {"status": "supported", "next_cursor": None, "events": [
                {"segment": {"start": "2026-10-07T09:00:00Z", "end": "2026-10-07T09:58:00Z"}},
                {"segment": {"start": "2026-10-07T09:58:00Z", "end": "2026-10-07T10:04:00Z"}}]}
        if kind == "empty":
            return {"status": "supported", "events": [], "next_cursor": None}
        return {"status": "unknown", "events": [], "next_cursor": None}
    monkeypatch.setattr(rc.dahua_archive, "enumerate_historical_events", enumerate_events)


def hik_archive(monkeypatch, per_channel):
    def search(driver, channel, start, end, *, offset=0, limit=50):
        kind = per_channel.get(str(channel), "empty")
        if kind == "raise":
            raise DriverError("archive refused")
        if kind == "found":
            return {"status": "supported", "incomplete": 0, "next_offset": None, "matches": [
                {"start": "2026-10-07T10:00:00+05:00", "end": "2026-10-07T15:03:00+05:00"}]}
        if kind == "incomplete":
            return {"status": "supported", "incomplete": 1, "next_offset": None, "matches": []}
        return {"status": "supported", "incomplete": 0, "next_offset": None, "matches": []}
    monkeypatch.setattr(rc.hikvision_archive, "search_recordings", search)


def rows(report):
    return {r["channel"]: r for r in report["recording"]["channels"]}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(rc, "_RECORDER_RPC_ABSENT", False)
    monkeypatch.setattr(rc, "_DISKS_RPC_ABSENT", False)
    monkeypatch.setattr(core, "log", lambda _m: None)


# ---- Dahua --------------------------------------------------------------------------------

def test_dahua_decision_table(monkeypatch):
    config = {"1": "continuous", "2": "continuous", "3": "disabled", "4": "continuous",
              "5": "scheduled", "6": "continuous", "7": "unknown"}
    dahua_archive(monkeypatch, {"1": "found", "2": "empty", "3": "empty", "4": "unknown",
                                "5": "empty", "6": "empty", "7": "empty"})
    driver = FakeDahua(config=config, video_loss=["6"])
    out = rows(rc._archive_assess(driver, list(config), "ok"))
    assert out["1"] == {"channel": "1", "state": "recording", "reason": "ok",
                        "latest_recording_at": "2026-10-07T10:04:00Z"}
    assert out["2"] == {"channel": "2", "state": "not_recording", "reason": "no_recent_recording"}
    assert out["3"] == {"channel": "3", "state": "not_recording", "reason": "recording_disabled"}
    assert out["4"] == {"channel": "4", "state": "unknown", "reason": "archive_search_failed"}
    assert out["5"] == {"channel": "5", "state": "unknown", "reason": "no_recent_archive"}, \
        "an event/part-week schedule can legitimately leave no recent footage"
    assert out["6"] == {"channel": "6", "state": "unknown", "reason": "video_loss"}
    assert out["7"]["state"] == "unknown", "an unreadable schedule never yields not_recording"


def test_dahua_failed_search_is_never_not_recording(monkeypatch):
    dahua_archive(monkeypatch, {"1": "raise"})
    out = rows(rc._archive_assess(FakeDahua(config={"1": "continuous"}), ["1"], "ok"))
    assert out["1"] == {"channel": "1", "state": "unknown", "reason": "archive_search_failed"}


def test_dahua_unknown_liveness_never_yields_not_recording(monkeypatch):
    dahua_archive(monkeypatch, {"1": "empty"})
    driver = FakeDahua(config={"1": "continuous"}, faults_supported=False)
    out = rows(rc._archive_assess(driver, ["1"], "ok"))
    assert out["1"] == {"channel": "1", "state": "unknown", "reason": "no_recent_archive"}


def test_dahua_footage_beats_a_disabled_mode(monkeypatch):
    dahua_archive(monkeypatch, {"1": "found"})
    out = rows(rc._archive_assess(FakeDahua(config={"1": "disabled"}), ["1"], "ok"))
    assert out["1"]["state"] == "recording"


def test_disabled_mode_holds_even_when_the_search_fails(monkeypatch):
    dahua_archive(monkeypatch, {"1": "raise"})
    out = rows(rc._archive_assess(FakeDahua(config={"1": "disabled"}), ["1"], "ok"))
    assert out["1"] == {"channel": "1", "state": "not_recording", "reason": "recording_disabled"}


def test_storage_fault_and_inventory_still_dominate(monkeypatch):
    dahua_archive(monkeypatch, {})
    fault = dict(OK_STORAGE, state="fault", reason="disk_error", disks=[])
    out = rows(rc._archive_assess(FakeDahua(config={"1": "continuous"}, storage=fault),
                                  ["1"], "ok"))
    assert out["1"]["state"] == "storage_fault"
    out = rows(rc._archive_assess(FakeDahua(config={"1": "continuous", "2": "continuous"}),
                                  ["1", "2"], "ok", inventory={"1": "missing", "2": "disabled"}))
    assert out["1"]["reason"] == "channel_missing" and out["2"]["reason"] == "channel_disabled"


def test_unreachable_recorder_is_not_searched(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a down recorder must not be searched")
    monkeypatch.setattr(rc.dahua_archive, "enumerate_historical_events", boom)
    out = rc._archive_assess(FakeDahua(config={"1": "continuous"}), ["1"], "unreachable")
    assert out["recording_evidence"] == "vendor_status"
    assert rows(out)["1"]["state"] == "unknown"


# ---- Hikvision ----------------------------------------------------------------------------

def test_hikvision_decision_table(monkeypatch):
    config = {"1": "continuous", "2": "continuous", "3": "continuous", "4": "continuous",
              "5": "disabled"}
    hik_archive(monkeypatch, {"1": "found", "2": "empty", "3": "empty", "4": "incomplete",
                              "5": "empty"})
    driver = FakeHik(config=config, online=["1", "2", "4"], offline=["3"])
    out = rows(rc._archive_assess(driver, list(config), "ok"))
    assert out["1"]["state"] == "recording"
    assert out["1"]["latest_recording_at"] == "2026-10-07T10:03:00Z"
    assert out["2"] == {"channel": "2", "state": "not_recording", "reason": "no_recent_recording"}
    assert out["3"] == {"channel": "3", "state": "unknown", "reason": "video_loss"}
    assert out["4"]["state"] == "recording" and "latest_recording_at" not in out["4"]
    assert out["5"]["reason"] == "recording_disabled"


def test_hikvision_channel_not_named_by_status_stays_unknown(monkeypatch):
    hik_archive(monkeypatch, {"1": "empty"})
    driver = FakeHik(config={"1": "continuous"}, online=["2"])
    out = rows(rc._archive_assess(driver, ["1"], "ok"))
    assert out["1"] == {"channel": "1", "state": "unknown", "reason": "no_recent_archive"}


# ---- disk inventory report -----------------------------------------------------------------

class Cloud:
    def __init__(self, missing=(), code="PGRST202", status=404):
        self.calls, self.missing, self.code, self.status = [], set(missing), code, status

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name in self.missing:
            raise core.CloudError(name, self.status, self.code, f"no function public.{name}")
        return {"ok": True}

    def names(self):
        return [n for n, _ in self.calls]


def assessed(monkeypatch):
    dahua_archive(monkeypatch, {"1": "found"})
    return rc._archive_assess(FakeDahua(config={"1": "continuous"}), ["1"], "ok")


def test_disks_go_to_the_recorder_scoped_rpc(monkeypatch):
    report = assessed(monkeypatch)
    cloud = Cloud()
    rc.report_disks(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=A), report)
    assert cloud.names() == ["wl_report_recorder_storage_disks"]
    kw = cloud.calls[0][1]
    assert kw["p_recorder_id"] == A and kw["p_agent_id"] == "agent"
    assert kw["p_report"]["disks"][0]["id"] == "/dev/sda"
    assert kw["p_report"]["total_bytes"] == 1000 and kw["p_report"]["free_bytes"] == 400


@pytest.mark.parametrize("code,status", [("PGRST202", 404), ("42883", 400)])
def test_an_older_database_without_the_disk_rpc_is_skipped_silently(monkeypatch, code, status):
    report = assessed(monkeypatch)
    cloud = Cloud(missing={"wl_report_recorder_storage_disks"}, code=code, status=status)
    cfg = SimpleNamespace(recorder_cloud_id=A)
    rc.report_disks(core, cloud, STATE, cfg, report)          # no raise
    rc.report_disks(core, cloud, STATE, cfg, report)
    assert cloud.names() == ["wl_report_recorder_storage_disks"], "not asked again every cycle"


def test_other_disk_rpc_errors_are_raised_for_the_cycle_to_log(monkeypatch):
    report = assessed(monkeypatch)
    cloud = Cloud(missing={"wl_report_recorder_storage_disks"}, code="42501", status=403)
    with pytest.raises(core.CloudError):
        rc.report_disks(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=A), report)


def test_no_disk_report_without_a_recorder_or_a_storage_read(monkeypatch):
    report = assessed(monkeypatch)
    cloud = Cloud()
    rc.report_disks(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=None), report)
    unread = {"storage": {"state": "unknown", "reason": "nvr_unreachable"}}
    rc.report_disks(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=A), unread)
    assert cloud.calls == []


def test_installed_cycle_reports_proof_then_disks(monkeypatch):
    monkeypatch.setattr(rc, "_INSTALLED", False)
    monkeypatch.setattr(rc.recording_health, "assess_recording_storage",
                        rc.recording_health.assess_recording_storage)
    dahua_archive(monkeypatch, {"1": "found"})
    fake_core = SimpleNamespace(log=lambda _m: None)
    driver = FakeDahua(config={"1": "continuous"})

    def health_cycle(cloud, state, cfg, holder):
        rc.recording_health.assess_recording_storage(driver, ["1"], "ok")

    fake_core.health_cycle = health_cycle
    rc.install(fake_core)
    cloud = Cloud(missing={"wl_report_recorder_storage_disks"})
    fake_core.health_cycle(cloud, STATE, SimpleNamespace(recorder_cloud_id=A), {})
    assert cloud.names() == ["wl_report_recorder_recording_storage_current",
                             "wl_report_recorder_storage_disks"]
    proof = cloud.calls[0][1]["p_report"]
    assert proof["recording"]["channels"][0]["latest_recording_at"] == "2026-10-07T10:04:00Z"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))


def test_dahua_newest_recording_is_taken_over_every_row_not_the_oldest_page(monkeypatch):
    """Al-Khalid 2026-10-08: rows come back oldest-first; the first 8 were 00:00-02:00 PKT files,
    so latest_recording_at stuck at 02:00 all afternoon. The newest end must win."""
    seen = {}

    def enumerate_events(driver, channel, start, end, cursor=None, limit=500):
        seen["limit"] = limit
        events = [{"segment": {"start": f"2026-10-08T{h:02d}:00:00Z", "end": f"2026-10-08T{h:02d}:59:59Z"}}
                  for h in range(0, 11)]
        return {"status": "supported", "next_cursor": None, "events": events[:limit]}
    monkeypatch.setattr(rc.dahua_archive, "enumerate_historical_events", enumerate_events)

    class D:
        name = "dahua-cgi"
    outcome, latest = rc._search(D(), "1", None, None)
    assert outcome == "found"
    assert latest == "2026-10-08T10:59:59Z"
    assert seen["limit"] >= 1000
