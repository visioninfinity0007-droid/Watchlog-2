"""5.1.9: Setup reaches a recorder this PC has never talked to (no browser login first).

Field (Al-Khalid DH-XVR1B08-I and HASCO DS-7608NI-Q1, 5.1.7/5.1.8, 2026-11-03): scanning the
recorder's IP and the login test both stopped at the 30-second limit until someone had opened
the recorder's page in a browser on the same PC; after that Setup worked. Windows must learn a
LAN device's hardware address (ARP) before its first packet, and a missed ARP request is
repeated only after one second, beyond Setup's 0.75-1.5 s connects. 5.1.8 also spent most of the
login limit stopping the old Agent. No real network is used here.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "agent"))
sys.path.insert(0, str(TESTS))
import programdata_sandbox  # noqa: E402,F401  (test_recorder writes setup.log under PROGRAMDATA)

import discover  # noqa: E402
import setup_backend as backend  # noqa: E402

RECORDER = "192.168.1.108"


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _cold_lan(learned: set, answers: dict):
    """A LAN where a host answers only once its address was learned (ARP) or when the connect
    waits long enough for Windows' second ARP request (>= 1.5 s). Absent hosts fail at once."""
    def connect(address, timeout=None):
        ip, port = address
        if port in answers.get(ip, ()) and (ip in learned or (timeout or 0) >= 1.5):
            return _Conn()
        raise socket.timeout("timed out")
    return connect


# --- discover.warm_host ------------------------------------------------------------------

def test_warm_host_learns_the_address_then_finds_the_ports():
    learned = set()
    out = discover.warm_host(RECORDER, _resolve=lambda ip: learned.add(ip) or "aa:bb:cc:dd:ee:ff",
                             _connect=_cold_lan(learned, {RECORDER: {80, 37777}}))
    assert out["mac"] == "aa:bb:cc:dd:ee:ff"
    assert out["ports"] == [80, 37777]


def test_warm_host_retries_once_when_nothing_answered():
    calls = {"n": 0}

    def connect(address, timeout=None):
        calls["n"] += 1
        if calls["n"] > len(discover.WARM_PORTS) and address[1] == 80:
            return _Conn()
        raise socket.timeout("timed out")
    out = discover.warm_host(RECORDER, _resolve=lambda ip: None, _connect=connect)
    assert out["ports"] == [80]
    assert calls["n"] == 2 * len(discover.WARM_PORTS)


def test_warm_host_is_bounded_when_nothing_is_there():
    seen = []

    def connect(address, timeout=None):
        seen.append(timeout)
        raise socket.timeout("timed out")
    out = discover.warm_host(RECORDER, rounds=2, _resolve=lambda ip: None, _connect=connect)
    assert out == {"mac": None, "ports": [], "elapsed": out["elapsed"]}
    assert len(seen) == 2 * len(discover.WARM_PORTS)
    assert set(seen) == {discover.WARM_CONNECT_TIMEOUT}


def test_resolve_neighbor_never_raises():
    assert discover.resolve_neighbor("not-an-ip") is None


# --- the subnet sweep ----------------------------------------------------------------------

def _sweep(resolve):
    learned = set()

    def tracking_resolve(ip):
        mac = resolve(ip)
        if mac:
            learned.add(ip)
        return mac
    return discover.sweep(log=lambda *_a: None, progress=lambda *_a: None,
                          _connect=_cold_lan(learned, {RECORDER: {80, 37777}}),
                          _bases=(["192.168.1"], ["192.168.1.20"]),
                          _resolve=tracking_resolve if resolve else None)


def test_sweep_without_arp_priming_misses_a_cold_recorder():
    # The 5.1.8 behaviour: 0.75 s connects never outlast a missed first ARP request.
    assert _sweep(None) == []


def test_sweep_finds_a_cold_recorder_after_learning_the_network():
    hits = dict(_sweep(lambda ip: "aa:bb:cc:dd:ee:ff" if ip == RECORDER else None))
    assert hits[RECORDER] == [80, 37777]


def test_arp_priming_covers_only_the_first_networks():
    seen = []
    discover.prime_neighbors([f"10.0.{n}" for n in range(1, 9)], _resolve=lambda ip: seen.append(ip))
    assert len(seen) == discover.ARP_PRIME_SUBNETS * 254
    assert {ip.rsplit(".", 1)[0] for ip in seen} == {"10.0.1", "10.0.2"}


# --- the login test --------------------------------------------------------------------------

def _driver_builder(outcomes: list, built: list):
    """Each build pops the next outcome: an exception text, or a channel count."""
    def build(name, url, user, password, timeout):
        outcome = outcomes.pop(0) if outcomes else "no driver recognised the device"
        built.append((name, url, timeout))

        class _Driver:
            verified_against_hardware = False

            def probe(self):
                if isinstance(outcome, str):
                    raise Exception(outcome)
                return SimpleNamespace(vendor="Dahua", model="DH-XVR1B08-I", firmware="",
                                       serial="", channel_count=outcome)

            def close(self):
                pass
        _Driver.name = name
        return _Driver()
    return build


def _login(hint=None, scan=None, warm_ports=(), outcomes=None):
    built, progress = [], []
    result = backend.test_recorder(
        RECORDER, "admin", "pw", progress=progress.append, hint=hint,
        _scan=scan or (lambda _h: []), _probe=lambda _h, **_k: [],
        _build=_driver_builder(list(outcomes or []), built),
        _warm=lambda _h: {"mac": "aa:bb:cc:dd:ee:ff", "ports": list(warm_ports), "elapsed": 0.1})
    return result, built, progress


def test_login_reaches_the_recorder_before_signing_in():
    _result, _built, progress = _login(hint={"ports": [80, 37777], "vendor_hint": "dahua"},
                                       outcomes=[8])
    assert progress.index("Reaching the recorder on the network…") < progress.index(
        "Signing in to the recorder…")


def test_manual_ip_uses_the_ports_the_reach_step_proved_when_the_scan_missed_them():
    result, built, _ = _login(scan=lambda _h: [], warm_ports=[80, 37777], outcomes=[8])
    assert result["driver"] == "dahua-cgi" and len(result["channels"]) == 8
    assert built[0][:2] == ("dahua-cgi", f"http://{RECORDER}")


def test_a_timed_out_login_is_retried_once_on_the_same_address():
    result, built, progress = _login(hint={"ports": [80, 37777], "vendor_hint": "dahua"},
                                     outcomes=["Read timed out. (read timeout=8)", 8])
    assert result["driver"] == "dahua-cgi"
    assert [b[:2] for b in built] == [("dahua-cgi", f"http://{RECORDER}")] * 2
    assert "The recorder is slow to answer. Trying again…" in progress
    assert built[0][2] == backend.RECORDER_PROBE_TIMEOUT


def test_a_second_timeout_moves_on_instead_of_looping():
    with pytest.raises(ValueError):
        _login(hint={"ports": [80, 37777], "vendor_hint": "dahua"},
               outcomes=["timed out"] * 10)


def test_a_wrong_password_is_never_retried():
    with pytest.raises(ValueError, match="rejected that username or password"):
        _login(hint={"ports": [80, 37777], "vendor_hint": "dahua"},
               outcomes=["HTTP 401 unauthorized", 8])


# --- stopping the old Agent and the GUI limit ------------------------------------------------

def test_pause_finds_the_agent_without_waiting_for_wmi_first():
    script = backend._PAUSE_SCRIPT
    assert script.index("Get-Process -Name 'watchlog-agent'") < script.index("Get-CimInstance Win32_Process")
    assert "-ieq $want" in script and "Stop-ScheduledTask" in script


def test_login_watchdog_outlasts_the_pause_and_the_backend_deadline():
    gui = (TESTS.parent / "agent" / "setup_gui.py").read_text(encoding="utf-8")
    block = gui[gui.index("def test_connection(self):"):gui.index("def connection_ok(self, result):")]
    assert "timeout_ms=90000" in block
    assert (30 + backend.RECORDER_DEADLINE + backend.RECORDER_PROBE_TIMEOUT
            + backend.CHANNEL_READ_BUDGET <= 90)


def test_the_paused_login_says_it_is_stopping_the_old_agent(monkeypatch):
    import qt_fake
    gui = qt_fake.load_setup_gui_with_fake_qt()
    order = []
    monkeypatch.setattr(gui.backend, "pause_background_agent", lambda *a, **k: order.append("pause"))
    monkeypatch.setattr(gui.backend, "test_recorder", lambda *a, **k: order.append("test") or {})
    gui._test_with_agent_paused(RECORDER, "admin", "pw", hint={}, progress=order.append)
    assert order == ["Stopping the previous WatchLog on this PC…", "pause", "test"]


# --- an IP-only Hikvision NVR's camera list (HASCO DS-7608NI-Q1, 5.1.9) -------------------------

def test_hikvision_model_names_state_the_channel_count():
    assert backend.channels_from_model("DS-7608NI-Q1") == 8
    assert backend.channels_from_model("DS-7616NI-K2") == 16
    assert backend.channels_from_model("DS-7732NI-K4") == 32
    assert backend.channels_from_model("DS-96128NI-I16") == 128
    assert backend.channels_from_model("DH-XVR1B08-I") is None
    assert backend.channels_from_model(None) is None


def _ip_nvr(model: str, list_channels):
    def build(name, url, user, password, timeout):
        class _Driver:
            verified_against_hardware = False

            def probe(self):
                return SimpleNamespace(vendor="Hikvision", model=model, firmware="", serial="",
                                       channel_count=0)   # no analog inputs on an IP-only NVR

            def close(self):
                pass
        _Driver.name = name
        _Driver.list_channels = lambda self: list_channels()
        return _Driver()
    return build


def _hik_login(model, list_channels, monkeypatch):
    monkeypatch.setattr(backend, "CHANNEL_READ_BUDGET", 0.2)
    progress = []
    result = backend.test_recorder(
        RECORDER, "admin", "pw", progress=progress.append,
        hint={"ports": [80, 8000], "vendor_hint": "hikvision"},
        _scan=lambda _h: [], _probe=lambda _h, **_k: [], _build=_ip_nvr(model, list_channels),
        _warm=lambda _h: {"mac": "aa:bb:cc:dd:ee:ff", "ports": [80, 8000], "elapsed": 0.1})
    return result, progress


def test_a_stalled_camera_list_uses_the_model_count(monkeypatch):
    import threading
    release = threading.Event()
    try:
        result, progress = _hik_login("DS-7608NI-Q1", lambda: release.wait(5) and [], monkeypatch)
    finally:
        release.set()
    assert result["driver"] == "hikvision-isapi"
    assert [c["channel"] for c in result["channels"]] == [str(i) for i in range(1, 9)]
    assert "Reading camera channels…" in progress


def test_a_camera_list_that_answers_keeps_the_real_names(monkeypatch):
    rows = [SimpleNamespace(channel="1", name="Gate"), SimpleNamespace(channel="2", name="Store")]
    result, _ = _hik_login("DS-7608NI-Q1", lambda: rows, monkeypatch)
    assert [c["name"] for c in result["channels"]] == ["Gate", "Store"]


def test_a_stalled_list_on_an_unknown_model_fails_as_a_timeout(monkeypatch):
    import threading
    release = threading.Event()
    try:
        with pytest.raises(ValueError) as caught:
            _hik_login("NVR-UNKNOWN", lambda: release.wait(5) and [], monkeypatch)
    finally:
        release.set()
    assert str(caught.value) == backend._CUSTOMER_ERROR["timeout"]
