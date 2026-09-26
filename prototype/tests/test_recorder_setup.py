"""Step 04 recorder-login regression tests.

Driven by the first real Dahua field finding: discovery saw ports
[80, 443, 554, 37777] but the login test blind-probed every driver against
every candidate URL, so it could hang for minutes and gave no actionable
error. These tests lock the fixed behaviour: port/vendor-aware ordering,
bounded attempts, fast credential failure, deferred capabilities, and
customer-safe classified errors. No real hardware or network is used.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import setup_backend as backend  # noqa: E402


# --- fakes -----------------------------------------------------------------

class _Info(SimpleNamespace):
    pass


class FakeDriver:
    """Stand-in recorder driver whose behaviour a test supplies."""
    def __init__(self, name, url, user, password, timeout, behavior):
        self.name = name
        self.url = url
        self.timeout = timeout
        self.verified_against_hardware = False
        self._b = behavior
        behavior.setdefault("built", []).append((name, url))

    def probe(self):
        if self._b.get("probe_error"):
            raise Exception(self._b["probe_error"])
        return _Info(vendor=self._b.get("vendor", "Dahua"),
                     model=self._b.get("model", "NVR-Test"),
                     firmware="1.0", serial=None, channel_count=self._b.get("channels", 4))

    def list_channels(self):
        if self._b.get("channels_error"):
            raise Exception(self._b["channels_error"])
        return [SimpleNamespace(channel=str(i + 1), name=f"Camera {i + 1}")
                for i in range(self._b.get("channels", 4))]

    def capabilities(self):   # must never be called during Step 04
        raise AssertionError("capabilities() must not run during the login test")

    def close(self):
        pass


def make_build(rules, shared):
    """rules: {driver_name: behavior}. A missing driver 'not recognises' the box."""
    def _build(name, url, user, password, timeout):
        beh = dict(rules.get(name, {"probe_error": "no driver recognised the device"}))
        beh["built"] = shared["built"]
        return FakeDriver(name, url, user, password, timeout, beh)
    return _build


def run(address, hint=None, scan_ports=None, rules=None, vendor_guess=None, hik_probe=None):
    shared = {"built": []}
    progress = []
    scan = None
    if scan_ports is not None:
        results = [SimpleNamespace(port=p, open=True, kind="http",
                                   vendor_guess=vendor_guess) for p in scan_ports]
        scan = lambda _host: results
    build = make_build(rules or {}, shared)
    try:
        result = backend.test_recorder(
            address, "admin", "pass1234",
            progress=progress.append, hint=hint,
            _scan=scan, _build=build,
            _hik_probe=(hik_probe or (lambda _host, _ports: {
                "vendor_hint": None, "state": "unknown", "port": None
            })),
        )
        return {"ok": True, "result": result, "built": shared["built"], "progress": progress}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "built": shared["built"], "progress": progress}


# --- 1. Dahua candidate: Dahua-first, standard port, bounded ---------------

def test_dahua_ports_plan_prefers_dahua_http_first():
    attempts, hard = backend.plan_recorder_probes("192.168.1.10", [80, 443, 554, 37777], None)
    assert hard is None
    assert attempts[0] == ("dahua-cgi", "http://192.168.1.10")   # Dahua first, not Hikvision
    assert all(":37777" not in url for _d, url in attempts)      # never probe the SDK port as HTTP
    assert len(attempts) <= 5                                    # bounded


def test_dahua_same_endpoint_fallback_precedes_second_native_port():
    """Build 62 stacked native HTTP+HTTPS timeouts before ONVIF; Build 69 then
    lived near the 30s watchdog. Fall back on the proven endpoint first."""
    attempts, hard = backend.plan_recorder_probes(
        "192.168.1.10", [80, 443, 554, 37777], "dahua")
    assert hard is None
    assert attempts[:4] == [
        ("dahua-cgi", "http://192.168.1.10"),
        ("onvif", "http://192.168.1.10"),
        ("dahua-cgi", "https://192.168.1.10"),
        ("onvif", "https://192.168.1.10"),
    ]


def test_discovery_preferred_web_port_is_tried_first():
    attempts, hard = backend.plan_recorder_probes(
        "192.168.1.10", [80, 443, 37777], "dahua", preferred_port=443)
    assert hard is None
    assert attempts[0] == ("dahua-cgi", "https://192.168.1.10")
    assert attempts[1] == ("onvif", "https://192.168.1.10")


# --- 2. Dahua HTTP 80 success ----------------------------------------------

def test_dahua_http80_success_returns_channels():
    out = run("192.168.1.10", hint={"ports": [80, 554, 37777], "vendor_hint": "dahua"},
              rules={"dahua-cgi": {"vendor": "Dahua", "channels": 8}})
    assert out["ok"]
    assert out["result"]["driver"] == "dahua-cgi"
    assert out["result"]["url"] == "http://192.168.1.10"
    assert len(out["result"]["channels"]) == 8
    assert out["built"][0][0] == "dahua-cgi"                     # dahua tried first


# --- 3. Dahua wrong credentials: fast, customer-safe -----------------------

def test_dahua_wrong_credentials_fast_fail():
    out = run("192.168.1.10", hint={"ports": [80, 37777], "vendor_hint": "dahua"},
              rules={"dahua-cgi": {"probe_error": "http://192.168.1.10: HTTP 401 unauthorized"},
                     "onvif": {"probe_error": "should not reach onvif"}})
    assert not out["ok"]
    assert out["error"] == "The recorder rejected that username or password."
    assert len(out["built"]) == 1                               # stopped immediately, no long loop


# --- 4. Dahua 37777 present but HTTP closed --------------------------------

def test_dahua_sdk_only_no_http_is_web_unreachable():
    out = run("192.168.1.10", hint={"ports": [554, 37777], "vendor_hint": "dahua"})
    assert not out["ok"]
    assert "web service is not reachable" in out["error"]
    assert out["built"] == []                                   # never attempts 37777 as HTTP


# --- 5. HTTPS-only Dahua ----------------------------------------------------

def test_https_only_dahua_uses_443():
    attempts, hard = backend.plan_recorder_probes("192.168.1.10", [443, 554, 37777], "dahua")
    assert hard is None
    assert attempts[0] == ("dahua-cgi", "https://192.168.1.10")
    out = run("192.168.1.10", hint={"ports": [443, 554, 37777], "vendor_hint": "dahua"},
              rules={"dahua-cgi": {"vendor": "Dahua", "channels": 4}})
    assert out["ok"] and out["result"]["url"] == "https://192.168.1.10"


# --- 6. Hikvision candidate with 8000 --------------------------------------

def test_hikvision_8000_preferred():
    assert backend._vendor_hint_from_ports([80, 8000, 554]) == "hikvision"
    attempts, hard = backend.plan_recorder_probes("192.168.1.10", [80, 8000, 554], None)
    assert hard is None
    assert attempts[0][0] == "hikvision-isapi"
    out = run("192.168.1.10", hint={"ports": [80, 8000], "vendor_hint": "hikvision"},
              rules={"hikvision-isapi": {"vendor": "Hikvision", "channels": 16}})
    assert out["ok"] and out["result"]["driver"] == "hikvision-isapi"


# --- 7. Xiongmai 34567: immediate unsupported ------------------------------

def test_xiongmai_34567_unsupported_immediately():
    out = run("192.168.1.10", hint={"ports": [34567, 554], "vendor_hint": "xiongmai"})
    assert not out["ok"]
    assert "not yet supported" in out["error"]
    assert out["built"] == []                                   # no long generic autodetect loop


# --- 8. Unknown recorder: bounded fallback ---------------------------------

def test_unknown_recorder_bounded_fallback():
    attempts, hard = backend.plan_recorder_probes("192.168.1.10", [80], None)
    assert hard is None
    assert [d for d, _ in attempts][:3] == ["hikvision-isapi", "dahua-cgi", "onvif"]
    out = run("192.168.1.10", hint={"ports": [80]},
              rules={"hikvision-isapi": {"probe_error": "http://x: HTTP 404 not found"},
                     "dahua-cgi": {"vendor": "Dahua", "channels": 4}})
    assert out["ok"] and out["result"]["driver"] == "dahua-cgi"
    assert len(out["built"]) <= 5


# --- 9. Capabilities failure must not fail Step 04 -------------------------

def test_capabilities_deferred_not_called():
    out = run("192.168.1.10", hint={"ports": [80], "vendor_hint": "dahua"},
              rules={"dahua-cgi": {"vendor": "Dahua", "channels": 4}})
    assert out["ok"]
    assert out["result"]["capabilities"] is None               # deferred to background agent
    # FakeDriver.capabilities() asserts if called; success proves it was not.


# --- 10. Progress + error copy contract ------------------------------------

def test_progress_sequence_and_error_copy():
    out = run("192.168.1.10", hint={"ports": [80], "vendor_hint": "dahua"},
              rules={"dahua-cgi": {"vendor": "Dahua", "channels": 2}})
    assert out["ok"]
    joined = " | ".join(out["progress"])
    assert "Checking recorder at 192.168.1.10" in joined
    assert "Detected Dahua-compatible recorder." in joined
    assert "Signing in to the recorder" in joined
    assert ("Recorder login verified" in joined or "Reading camera channels" in joined)
    # every customer error key maps to a non-technical, non-empty sentence
    for key, msg in backend._CUSTOMER_ERROR.items():
        assert msg and "cgi" not in msg.lower() and "http://" not in msg
    # bounded timeouts are sane
    assert backend.RECORDER_PROBE_TIMEOUT <= 8
    assert backend.RECORDER_DEADLINE <= 20


# --- 11. Hikvision integration auth: do not mislabel browser password -------

def test_hikvision_isapi_401_falls_back_to_onvif_before_failing():
    out = run(
        "192.168.1.20",
        hint={"ports": [80, 554, 8000], "vendor_hint": "hikvision",
              "integration_state": "auth_required"},
        rules={
            "hikvision-isapi": {"probe_error": "http://192.168.1.20/ISAPI/System/deviceInfo: HTTP 401 unauthorized"},
            "onvif": {"vendor": "Hikvision", "channels": 8},
        },
    )
    assert out["ok"]
    assert out["result"]["driver"] == "onvif"
    assert [name for name, _url in out["built"]][:2] == ["hikvision-isapi", "onvif"]


def test_hikvision_browser_password_rejection_reports_integration_setting():
    out = run(
        "192.168.1.20",
        hint={"ports": [80, 554, 8000], "vendor_hint": "hikvision",
              "integration_state": "auth_required"},
        rules={
            "hikvision-isapi": {"probe_error": "HTTP 401 unauthorized"},
            "onvif": {"probe_error": "HTTP 401 unauthorized"},
        },
    )
    assert not out["ok"]
    assert "integration API" in out["error"]
    assert "ISAPI" in out["error"]
    assert "browser" in out["error"]


def test_hikvision_known_disabled_service_reports_service_not_password():
    out = run(
        "192.168.1.20",
        hint={"ports": [80, 554], "vendor_hint": "hikvision",
              "integration_state": "unavailable"},
        rules={
            "hikvision-isapi": {"probe_error": "HTTP 404 not found"},
            "onvif": {"probe_error": "HTTP 404 not found"},
        },
    )
    assert not out["ok"]
    assert "integration service" in out["error"]
    assert "Enable ISAPI" in out["error"]


def test_rtsp_only_candidate_reidentified_as_hikvision_after_web_rescue():
    shared = {"built": []}
    progress = []
    build = make_build({
        "hikvision-isapi": {"probe_error": "HTTP 401 unauthorized"},
        "onvif": {"probe_error": "HTTP 401 unauthorized"},
        "dahua-cgi": {"vendor": "Dahua", "channels": 4},
    }, shared)
    try:
        backend.test_recorder(
            "192.168.15.108", "admin", "browser-password",
            progress=progress.append,
            hint={"ports": [554], "vendor_hint": None, "source": "Network scan"},
            _build=build,
            _probe=lambda _host: [80],
            _hik_probe=lambda _host, _ports: {
                "vendor_hint": "hikvision", "state": "auth_required", "port": 80
            },
        )
        assert False, "expected integration-auth error"
    except ValueError as exc:
        msg = str(exc)
    assert "integration API" in msg
    assert "ISAPI" in msg
    # The generic Dahua driver must never get a chance to overwrite the Hikvision diagnosis.
    assert all(name != "dahua-cgi" for name, _url in shared["built"])
