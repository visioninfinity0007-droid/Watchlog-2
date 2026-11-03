#!/usr/bin/env python3
"""5.1.3 installer field fixes: vendor discovery, known recorder first, honest start-up check.

Field (2026-10-08):
* Al-Khalid (Dahua): Setup exited 2 ("did not finish") while the Agent it had started kept
  reporting. The 50 s finalize watchdog and the 100 s start-up bound were both shorter than the
  start-up script's own worst case, and a killed script was read as "not started".
* HASCO (Hikvision): "no recorder found" on an upgrade, although the recorder was configured in
  watchlog.ini, and ONVIF/sweep cannot see a recorder in another subnet of the LAN.
"""
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import setup_backend as backend  # noqa: E402
import vendor_discovery as vd  # noqa: E402

SADP_REPLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<ProbeMatch><Uuid>ABC</Uuid><Types>inquiry</Types><DeviceType>139711</DeviceType>
<DeviceDescription>DS-7608NI-Q1</DeviceDescription><DeviceSN>DS-7608NI-Q10820230101CCRRK12345678WCVU</DeviceSN>
<CommandPort>8000</CommandPort><HttpPort>80</HttpPort><MAC>c0-56-e3-00-00-01</MAC>
<IPv4Address>192.168.1.64</IPv4Address><IPv4SubnetMask>255.255.255.0</IPv4SubnetMask>
<IPv4Gateway>192.168.1.1</IPv4Gateway><Activated>true</Activated></ProbeMatch>"""


def test_sadp_probe_is_the_inquiry_xml():
    probe = vd.sadp_probe_bytes("PID").decode()
    assert "<Probe>" in probe and "<Uuid>PID</Uuid>" in probe and "<Types>inquiry</Types>" in probe


def test_sadp_reply_names_the_recorder_even_in_another_subnet():
    row = vd.parse_sadp(SADP_REPLY)
    assert row["ip"] == "192.168.1.64" and row["vendor_hint"] == "hikvision"
    assert row["label"] == "Hikvision DS-7608NI-Q1" and row["preferred_web_port"] == 80
    assert row["source"] == "SADP" and "inactive" not in row


def test_sadp_ignores_probes_and_junk():
    assert vd.parse_sadp(vd.sadp_probe_bytes()) is None
    assert vd.parse_sadp(b"<ProbeMatch><IPv4Address>999.1.1.1</IPv4Address></ProbeMatch>") is None
    assert vd.parse_sadp(b"\xff\x00garbage") is None


def test_dahua_probe_is_dhip_framed_json():
    probe = vd.dahua_probe_bytes(7)
    head = struct.unpack("<I4sIIIIII", probe[:32])
    body = probe[32:].strip()
    assert head[0] == 0x20 and head[1] == b"DHIP" and head[3] == 7
    assert head[4] == head[6] == len(body)
    assert json.loads(body)["method"] == "DHDiscover.search"


def test_dahua_reply_is_parsed():
    reply = {"method": "client.notifyDevInfo", "params": {"deviceInfo": {
        "DeviceType": "DH-XVR1B08-I", "HttpPort": 80,
        "IPv4Address": {"IPAddress": "192.168.0.108", "SubnetMask": "255.255.255.0"}}}}
    data = struct.pack("<I4sIIIIII", 0x20, b"DHIP", 0, 1, 10, 0, 10, 0) + json.dumps(reply).encode()
    row = vd.parse_dahua(data)
    assert row == {"ip": "192.168.0.108", "label": "Dahua DH-XVR1B08-I", "source": "DHDiscover",
                   "vendor_hint": "dahua", "preferred_web_port": 80, "subnet_mask": "255.255.255.0"}
    assert vd.parse_dahua(b"no json here") is None


def test_known_recorder_comes_first_and_keeps_its_label(monkeypatch):
    monkeypatch.setattr(backend, "_known_recorder_rows",
                        lambda known: [{"ip": "192.168.1.64", "label": "Previously connected recorder",
                                        "source": "known", "vendor_hint": "hikvision"}])
    monkeypatch.setattr(vd, "discover", lambda timeout=3.0: [
        {"ip": "192.168.1.64", "label": "Hikvision DS-7608NI-Q1", "source": "SADP", "vendor_hint": "hikvision"},
        {"ip": "192.168.1.65", "label": "Hikvision DS-7608NI-Q1", "source": "SADP", "vendor_hint": "hikvision"}])
    monkeypatch.setattr(backend.wsdiscovery, "discover", lambda log=None: [])
    monkeypatch.setattr(backend.discover, "sweep", lambda *a, **k: iter([("192.168.1.65", {80, 8000})]))
    monkeypatch.setattr(backend.discover, "fingerprint", lambda ip, ports: {"vendor_guess": None, "web": []})
    rows = backend.discover_recorders(known=["http://192.168.1.64"])
    by_ip = {r["ip"]: r for r in rows}
    assert rows[0]["ip"] == "192.168.1.64" and rows[0]["source"] == "known"
    assert by_ip["192.168.1.65"]["source"] == "SADP"
    assert by_ip["192.168.1.65"]["vendor_hint"] == "hikvision"


def test_known_addresses_come_from_ini_and_registry(monkeypatch):
    import recorder_registry
    monkeypatch.setattr(recorder_registry, "recorders",
                        lambda: [{"url": "http://10.0.0.5"}, {"url": "http://192.168.1.64"}])
    assert backend.known_recorder_addresses({"nvr_url": "http://192.168.1.64"}) == [
        "http://192.168.1.64", "http://10.0.0.5"]


@pytest.fixture
def windows(monkeypatch, tmp_path):
    # Only setup_backend sees Windows: patching os.name itself makes pathlib build WindowsPath
    # objects, which cannot exist on the Linux CI runner.
    import os as _os

    class _NtOs:
        name = "nt"

        def __getattr__(self, attr):
            return getattr(_os, attr)
    monkeypatch.setattr(backend, "os", _NtOs())
    (tmp_path / "register-service.ps1").write_text("# stub", encoding="utf-8")
    return tmp_path


def _runner(register_result, running_out):
    calls = []

    def run(cmd, timeout):
        calls.append(cmd)
        if any(str(c).endswith("register-service.ps1") for c in cmd):
            return register_result
        return 0, running_out
    return run, calls


def test_a_killed_startup_script_with_the_agent_running_is_started(windows):
    run, calls = _runner((-1, "timed out"), "WL_RUNNING agents=1 task=Running\n")
    out = backend.ensure_background_agent(windows, timeout=5, _run=run, require_readiness=True)
    assert out["started"] is True and out["proven"] is False
    assert len(calls) == 2


def test_no_agent_process_means_not_started(windows):
    run, _ = _runner((1, "boom"), "WL_RUNNING agents=0 task=Ready\n")
    out = backend.ensure_background_agent(windows, timeout=5, _run=run, require_readiness=True)
    assert out["started"] is False


def test_an_agent_without_its_task_is_not_started(windows):
    run, _ = _runner((1, "boom"), "WL_RUNNING agents=1 task=none\n")
    assert backend.ensure_background_agent(windows, timeout=5, _run=run)["started"] is False


def test_startup_bound_outlasts_the_scripts_own_worst_case():
    script = (Path(backend.__file__).resolve().parent.parent / "installer" / "register-service.ps1")
    text = script.read_text(encoding="utf-8")
    readiness = int(__import__("re").search(r"\$ReadinessTimeoutSec = (\d+)", text).group(1))
    assert backend.BACKGROUND_READY_TIMEOUT_SECONDS >= readiness + 10 + 60


def test_finalize_watchdog_outlasts_the_startup_bound():
    src = (Path(backend.__file__).resolve().parent / "setup_gui.py").read_text(encoding="utf-8")
    assert "seconds = 60 + 20 * extra + backend.BACKGROUND_READY_TIMEOUT_SECONDS" in src
    assert "if backend.background_agent_running():" in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
