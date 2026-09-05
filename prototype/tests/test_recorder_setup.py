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


def run(address, hint=None, scan_ports=None, rules=None, vendor_guess=None):
    shared = {"built": []}
    progress = []
    scan = None
    if scan_ports is not None:
        results = [SimpleNamespace(port=p, open=True, kind="http",
                                   vendor_guess=vendor_guess) for p in scan_ports]
        scan = lambda _host: results
    build = make_build(rules or {}, shared)
    try:
        result = backend.test_recorder(address, "admin", "pass1234",
                                       progress=progress.append, hint=hint,
                                       _scan=scan, _build=build)
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
    assert "Reading camera channels" in joined
    # every customer error key maps to a non-technical, non-empty sentence
    for key, msg in backend._CUSTOMER_ERROR.items():
        assert msg and "cgi" not in msg.lower() and "http://" not in msg
    # bounded timeouts are sane
    assert backend.RECORDER_PROBE_TIMEOUT <= 8
    assert backend.RECORDER_DEADLINE <= 20
