#!/usr/bin/env python3
"""Recorder storage + recording-configuration truth for Dahua and Hikvision (5.1.2).

Field fact (Al-Khalid DH-XVR1B08-I, 5.1.1): storage_state was 'unknown' everywhere because
DahuaDriver.storage_status() keyword-scanned the getDeviceAllInfo values, and the real reply
(State=Success, IsError=false, TotalBytes/UsedBytes) matched no keyword. The parser now reads
each disk. Hikvision read only /ISAPI/Smart/storageDetection (no capacity, no free space, and
badBlocks dropped) and had no recording_status at all.

Rule under test everywhere: a state is positive only with positive evidence. No disks, an
unrecognised state or an unreported capacity stays UNKNOWN with a reason; absence never reads
as 'ok', and configuration alone never reads as 'recording'.

Fixtures (tests/fixtures/recorder_truth/) are written to the vendors' documented reply shapes;
they are not field captures, so every read stays IMPLEMENTED_UNVERIFIED.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from drivers import recorder_truth as rt  # noqa: E402
from drivers import dahua, hikvision  # noqa: E402
from drivers.base import DriverError  # noqa: E402
from drivers.dahua import DahuaDriver, _parse_kv  # noqa: E402
from drivers.hikvision import HikvisionDriver, _strip_ns  # noqa: E402
import recording_health  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "recorder_truth"
TB = 1000068870144


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def xml(text: str) -> ET.Element:
    return _strip_ns(ET.fromstring(text.encode("utf-8")))


def dahua_disk(idx=0, *, state="Success", is_error="false", total=TB, used=600_000_000_000,
               name="/dev/sda", path="/dev/sda1", kind="ReadWrite") -> str:
    p = f"list.info[{idx}]"
    lines = [f"{p}.Detail[0].IsError={is_error}", f"{p}.Detail[0].Path={path}",
             f"{p}.Detail[0].TotalBytes={total:.6f}", f"{p}.Detail[0].Type={kind}",
             f"{p}.Detail[0].UsedBytes={used:.6f}", f"{p}.Name={name}"]
    if state is not None:
        lines.append(f"{p}.State={state}")
    return "\r\n".join(lines) + "\r\n"


# ---- Dahua getDeviceAllInfo -------------------------------------------------------------

def test_dahua_xvr1b08i_reply_is_ok_with_disk_inventory():
    out = dahua.parse_storage_all_info(_parse_kv(fixture("dahua_xvr1b08i_getdeviceallinfo.txt")))
    assert out["supported"] is True
    assert (out["state"], out["reason"]) == ("ok", "ok")
    assert out["disk_count"] == 1
    disk = out["disks"][0]
    assert disk == {"id": "/dev/sda", "path": "/dev/sda1", "type": "ReadWrite", "state": "ok",
                    "reason": "ok", "total_bytes": 1000068870144,
                    "free_bytes": 1000068870144 - 612408090624}
    assert out["total_bytes"] == 1000068870144
    assert out["free_bytes"] == 1000068870144 - 612408090624


def test_dahua_crlf_reply_parses_the_same():
    text = fixture("dahua_xvr1b08i_getdeviceallinfo.txt").replace("\n", "\r\n")
    assert dahua.parse_storage_all_info(_parse_kv(text))["state"] == "ok"


@pytest.mark.parametrize("state", ["Error", "Abnormal", "Fault", "error"])
def test_dahua_faulted_disk_state_is_fault(state):
    out = dahua.parse_storage_all_info(_parse_kv(dahua_disk(state=state)))
    assert (out["state"], out["reason"]) == ("fault", "disk_error")
    assert out["disks"][0]["state"] == "fault"


def test_dahua_partition_iserror_true_faults_the_disk():
    out = dahua.parse_storage_all_info(_parse_kv(dahua_disk(is_error="true")))
    assert (out["state"], out["reason"]) == ("fault", "disk_error")


def test_dahua_normal_state_is_ok():
    assert dahua.parse_storage_all_info(_parse_kv(dahua_disk(state="Normal")))["state"] == "ok"


@pytest.mark.parametrize("state", ["Sleeping", "Formatting", "", None])
def test_dahua_unrecognised_or_missing_state_is_unknown(state):
    out = dahua.parse_storage_all_info(_parse_kv(dahua_disk(state=state)))
    assert out["state"] is None and out["reason"] == "disk_state_unknown"


def test_dahua_no_disks_is_unknown_never_ok():
    for text in ("", "list.info=\r\n", "result=OK\r\n", "status=Normal\r\n"):
        out = dahua.parse_storage_all_info(_parse_kv(text))
        assert out["state"] is None, text
        assert out["reason"] == "no_disks_reported"


def test_dahua_keyword_fallback_reports_problems_only():
    assert dahua.parse_storage_all_info({"status": "DiskError"})["state"] == "fault"
    assert dahua.parse_storage_all_info({"status": "LowSpace"})["state"] == "degraded"
    out = dahua.parse_storage_all_info({"status": "Running ok normal good"})
    assert out["state"] is None, "a 'normal'-looking word without disks is never ok"


def test_a_nearly_full_overwriting_disk_is_healthy_and_flagged_near_full():
    # Owner decision 2026-10-07: overwrite recorders sit near 0 % free by design; only the
    # recorder's own low-space signal is 'disk_full'.
    used = int(TB * 0.99)                      # 1 % free: an overwriting disk
    out = dahua.parse_storage_all_info(_parse_kv(dahua_disk(used=used)))
    assert (out["state"], out["reason"], out["near_full"]) == ("ok", "ok", True)
    above = dahua_disk(total=10**12, used=97 * 10**10)  # 3 % free
    assert dahua.parse_storage_all_info(_parse_kv(above))["near_full"] is False
    assert dahua.parse_storage_all_info({"status": "LowSpace"})["reason"] == "disk_full"


def test_dahua_missing_sizes_are_capacity_unknown():
    text = "list.info[0].Name=/dev/sda\r\nlist.info[0].State=Success\r\n"
    out = dahua.parse_storage_all_info(_parse_kv(text))
    assert (out["state"], out["reason"]) == (None, "capacity_unknown")
    assert out["disks"][0]["total_bytes"] is None and out["disks"][0]["free_bytes"] is None


def test_dahua_two_disks_one_failed_is_degraded_not_a_recorder_fault():
    text = dahua_disk(0) + dahua_disk(1, state="Error", name="/dev/sdb", path="/dev/sdb1")
    out = dahua.parse_storage_all_info(_parse_kv(text))
    assert (out["state"], out["reason"]) == ("degraded", "disk_error")
    assert [d["state"] for d in out["disks"]] == ["ok", "fault"]


def test_dahua_driver_storage_status_reads_the_cgi():
    d = DahuaDriver("http://recorder", "u", "p")
    try:
        d._get = lambda path, **kw: fixture("dahua_xvr1b08i_getdeviceallinfo.txt")
        assert d.storage_status()["state"] == "ok"

        def boom(path, **kw):
            raise DriverError("HTTP 404")
        d._get = boom
        assert d.storage_status() == {"supported": False, "state": None}
    finally:
        d.close()


# ---- Hikvision ISAPI storage --------------------------------------------------------------

def hik_hdd(ident="1", status="ok", capacity=953869, free=400000, prop="RW") -> str:
    return (f"<hdd><id>{ident}</id><hddName>hdd{ident}</hddName><hddType>SATA</hddType>"
            f"<status>{status}</status><capacity>{capacity}</capacity>"
            f"<freeSpace>{free}</freeSpace><property>{prop}</property></hdd>")


def hik_storage(*hdds: str, nas: str = "") -> ET.Element:
    return xml("<storage xmlns='http://www.hikvision.com/ver20/XMLSchema'><hddList>"
               + "".join(hdds) + "</hddList><nasList>" + nas + "</nasList></storage>")


def test_hikvision_storage_fixture_is_ok_with_capacity_in_bytes():
    out = hikvision.parse_storage(xml(fixture("hikvision_storage.xml")), None)
    assert (out["state"], out["reason"]) == ("ok", "ok")
    disk = out["disks"][0]
    assert disk["id"] == "hdd1" and disk["state"] == "ok" and disk["type"] == "RW"
    assert disk["total_bytes"] == 3815447 * 1024 * 1024
    assert disk["free_bytes"] == 1310720 * 1024 * 1024


@pytest.mark.parametrize("status", ["error", "abnormal", "unformatted", "uninitialized",
                                    "smartFailed", "notexist"])
def test_hikvision_fault_statuses(status):
    out = hikvision.parse_storage(hik_storage(hik_hdd(status=status)), None)
    assert (out["state"], out["reason"]) == ("fault", "disk_error")


@pytest.mark.parametrize("status", ["ok", "normal", "sleeping"])
def test_hikvision_healthy_statuses(status):
    assert hikvision.parse_storage(hik_storage(hik_hdd(status=status)), None)["state"] == "ok"


@pytest.mark.parametrize("status", ["formatting", "idle"])
def test_hikvision_unknown_status_stays_unknown(status):
    out = hikvision.parse_storage(hik_storage(hik_hdd(status=status)), None)
    assert (out["state"], out["reason"]) == (None, "disk_state_unknown")


def test_hikvision_low_free_space_is_near_full_not_a_fault():
    out = hikvision.parse_storage(hik_storage(hik_hdd(free=0)), None)
    assert (out["state"], out["near_full"]) == ("ok", True)


def test_hikvision_empty_disk_list_is_unknown():
    out = hikvision.parse_storage(hik_storage(), None)
    assert (out["state"], out["reason"]) == (None, "no_disks_reported")


def test_hikvision_bad_blocks_degrade_an_ok_disk():
    det = xml("<storageDetection><healthState>good</healthState><badBlocks>12</badBlocks>"
              "</storageDetection>")
    out = hikvision.parse_storage(hik_storage(hik_hdd()), det)
    assert (out["state"], out["reason"]) == ("degraded", "disk_error")
    assert out["detail"] == {"health_state": "good", "bad_blocks": 12}


def test_hikvision_detection_bad_health_faults_and_good_never_upgrades():
    bad = xml("<storageDetection><healthState>bad</healthState></storageDetection>")
    assert hikvision.parse_storage(hik_storage(hik_hdd()), bad)["state"] == "fault"
    good = xml("<storageDetection><healthState>good</healthState><badBlocks>0</badBlocks>"
               "</storageDetection>")
    unknown_disk = hikvision.parse_storage(hik_storage(hik_hdd(status="formatting")), good)
    assert unknown_disk["state"] is None, "a good SMART read never turns an unknown disk ok"
    alone = hikvision.parse_storage(None, good)
    assert (alone["state"], alone["reason"]) == (None, "capacity_unknown")


def test_hikvision_nothing_readable_is_unsupported():
    assert hikvision.parse_storage(None, None) == {"supported": False, "state": None}


def test_hikvision_nas_rows_never_carry_an_address():
    nas = ("<nas><id>9</id><addressingFormatType>ipaddress</addressingFormatType>"
           "<ipAddress>192.168.1.50</ipAddress><path>/share</path><status>ok</status>"
           "<capacity>1000</capacity><freeSpace>500</freeSpace></nas>")
    out = hikvision.parse_storage(hik_storage(hik_hdd(), nas=nas), None)
    assert [d["id"] for d in out["disks"]] == ["hdd1", "nas9"]
    assert "192.168.1.50" not in repr(out) and out["disks"][1]["path"] is None


def test_hikvision_driver_falls_back_to_the_hdd_endpoint():
    class Fake(HikvisionDriver):
        def __init__(self, docs):
            super().__init__("http://127.0.0.1", "u", "p")
            self.docs, self.asked = docs, []

        def _xml(self, path):
            self.asked.append(path)
            if path not in self.docs:
                raise DriverError("HTTP 404")
            return xml(self.docs[path])

    d = Fake({"/ISAPI/ContentMgmt/Storage/hdd": "<hddList>" + hik_hdd() + "</hddList>"})
    try:
        assert d.storage_status()["state"] == "ok"
        assert d.asked == ["/ISAPI/ContentMgmt/Storage", "/ISAPI/ContentMgmt/Storage/hdd",
                           "/ISAPI/Smart/storageDetection"]
    finally:
        d.close()


# ---- recording configuration --------------------------------------------------------------

def test_dahua_record_mode_and_schedule():
    kv = _parse_kv(fixture("dahua_record_config.txt"))
    assert dahua.parse_record_mode(kv) == {"1": "scheduled", "2": "continuous", "3": "disabled",
                                           "4": "scheduled", "5": "scheduled"}
    sched = dahua.parse_record_schedule(kv)
    assert sched["1"] == "continuous", "a 12:00/23:59:59 split still covers the day"
    assert sched["4"] == "scheduled"       # motion/alarm all day + regular office hours only
    assert sched["5"] == "disabled"        # sections present, none records


@pytest.mark.parametrize("mode", ["off", "Off", "closed", "2"])
def test_dahua_mode_off_variants_are_disabled(mode):
    assert dahua.parse_record_mode({"table.RecordMode[0].Mode": mode}) == {"1": "disabled"}


def test_dahua_recording_status_reports_disabled_and_config():
    d = DahuaDriver("http://recorder", "u", "p")
    text = fixture("dahua_record_config.txt")
    try:
        d._get = lambda path, **kw: text
        out = d.recording_status(None)
        assert out["supported"] is True
        assert out["channels"] == {"1": None, "2": None, "3": "not_recording", "4": None,
                                   "5": "not_recording"}
        assert out["reasons"] == {"3": "recording_disabled", "5": "recording_disabled"}
        assert out["config"] == {"1": "continuous", "2": "continuous", "3": "disabled",
                                 "4": "scheduled", "5": "disabled"}
    finally:
        d.close()


def test_dahua_unreadable_schedule_leaves_scheduled_channels_unknown():
    d = DahuaDriver("http://recorder", "u", "p")

    def get(path, **kw):
        if "name=RecordMode" in path:
            return "table.RecordMode[0].Mode=0\r\ntable.RecordMode[1].Mode=2\r\n"
        raise DriverError("HTTP 404")
    try:
        d._get = get
        out = d.recording_status(None)
        assert out["config"] == {"1": "unknown", "2": "disabled"}
        assert out["channels"] == {"1": None, "2": "not_recording"}
    finally:
        d.close()


def test_hikvision_record_tracks():
    config = hikvision.parse_record_tracks(xml(fixture("hikvision_record_tracks.xml")))
    assert config == {"1": "continuous", "2": "disabled", "3": "scheduled", "4": "disabled",
                      "5": "unknown"}, "sub-stream track 102 is ignored"


def test_hikvision_recording_status_and_liveness():
    class Fake(HikvisionDriver):
        def __init__(self, docs):
            super().__init__("http://127.0.0.1", "u", "p")
            self.docs = docs

        def _xml(self, path):
            if path not in self.docs:
                raise DriverError("HTTP 404")
            return xml(self.docs[path])

    d = Fake({
        "/ISAPI/ContentMgmt/record/tracks": fixture("hikvision_record_tracks.xml"),
        "/ISAPI/ContentMgmt/InputProxy/channels/status":
            "<InputProxyChannelStatusList><InputProxyChannelStatus><id>1</id><online>true</online>"
            "</InputProxyChannelStatus><InputProxyChannelStatus><id>2</id><online>false</online>"
            "</InputProxyChannelStatus></InputProxyChannelStatusList>",
    })
    try:
        rec = d.recording_status(None)
        assert rec["channels"]["2"] == "not_recording" and rec["channels"]["1"] is None
        assert rec["reasons"] == {"2": "recording_disabled", "4": "recording_disabled"}
        assert d.channel_liveness() == {"supported": True, "online": ["1"], "offline": ["2"]}
        d.docs = {}
        assert d.recording_status(None) == {"supported": False, "channels": {}}
        assert d.channel_liveness()["supported"] is False
    finally:
        d.close()


def test_week_coverage_edges():
    day = rt.MINUTES_PER_DAY
    full = [(d * day, d * day + day) for d in range(7)]
    assert rt.covers_week(full)
    assert rt.covers_week([(d * day, d * day + day - 1) for d in range(7)])   # 23:59 ends
    assert not rt.covers_week(full[:6])                                       # a day missing
    assert not rt.covers_week([(d * day, d * day + 600) for d in range(7)])   # part days
    assert not rt.covers_week([])
    assert rt.hms_minutes("24:00:00") == day and rt.hms_minutes("24:01:00") is None


# ---- the assessment carries disks and reasons to the cloud ---------------------------------

class _Driver:
    def __init__(self, storage, recording):
        self._s, self._r = storage, recording

    def storage_status(self):
        return self._s

    def recording_status(self, channels=None):
        return self._r


def test_assessment_carries_disk_inventory_reason_and_config():
    storage = dahua.parse_storage_all_info(_parse_kv(dahua_disk(used=int(TB * 0.995))))
    rec = {"supported": True, "channels": {"1": None, "2": "not_recording"},
           "reasons": {"2": "recording_disabled"}, "config": {"1": "continuous", "2": "disabled"}}
    out = recording_health.assess_recording_storage(_Driver(storage, rec), ["1", "2"], "ok")
    assert out["storage"]["state"] == "ok" and out["storage"]["near_full"] is True
    assert out["storage"]["total_bytes"] == TB and len(out["storage"]["disks"]) == 1
    rows = {r["channel"]: r for r in out["recording"]["channels"]}
    assert rows["2"] == {"channel": "2", "state": "not_recording", "reason": "recording_disabled"}
    assert rows["1"]["state"] == "unknown", "configuration is never proof of recording"
    assert out["recording_config"] == {"1": "continuous", "2": "disabled"}


def test_assessment_unknown_storage_keeps_its_reason():
    storage = dahua.parse_storage_all_info({})
    out = recording_health.assess_recording_storage(
        _Driver(storage, {"supported": False, "channels": {}}), ["1"], "ok")
    assert out["storage"]["state"] == "unknown"
    assert out["storage"]["reason"] == "no_disks_reported"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
