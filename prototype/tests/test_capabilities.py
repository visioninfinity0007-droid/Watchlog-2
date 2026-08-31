#!/usr/bin/env python3
"""
Capability-probe tests.

The probe reads what analytics a recorder supports and which are already
on — read-only, it must never change a device. These tests pin the Dahua
parser against the simulator (which serves realistic config), and check
the normalised shape every driver must return, including the fail-safe:
an endpoint that errors leaves that analytic 'unknown', it never crashes.

    python prototype/tests/test_capabilities.py
    pytest -q prototype/tests/test_capabilities.py
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "sim"))

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class DahuaSim:
    """Run the real Dahua simulator on a background thread."""

    def __enter__(self):
        import dahua_sim
        self.port = _free_port()
        srv = dahua_sim.ThreadingHTTPServer(
            ("127.0.0.1", self.port), dahua_sim.make_handler()
            if hasattr(dahua_sim, "make_handler") else dahua_sim.Handler)
        self.srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        return self

    def __exit__(self, *a):
        self.srv.shutdown()


@case("Dahua probe reports the sim's configured analytics correctly")
def t_dahua():
    from drivers.dahua import DahuaDriver
    with DahuaSim() as sim:
        d = DahuaDriver(f"http://127.0.0.1:{sim.port}", "admin", "admin")
        caps = d.capabilities()
        d.close()

    chans = {c["channel"]: c for c in caps["channels"]}
    assert len(chans) == 4, f"expected 4 channels, got {len(chans)}"

    def a(ch, key):
        return next(x for x in chans[ch]["analytics"] if x["key"] == key)

    # motion on everywhere
    assert all(a(ch, "motion")["active"] for ch in chans), "motion not active on all"
    # SMD supported but off
    assert a("1", "human_vehicle")["supported"]
    assert not a("1", "human_vehicle")["active"]
    # the sim configured a line on ch1 and a zone on ch3
    assert a("1", "line_crossing")["active"], "gate line not detected active"
    assert not a("2", "line_crossing")["active"], "ch2 line should be off"
    assert a("3", "intrusion")["active"], "yard zone not detected active"
    # tamper on ch4 only
    assert a("4", "tamper")["active"] and not a("1", "tamper")["active"]
    # geometry flags
    assert a("1", "line_crossing")["geometry"] is True
    assert a("1", "motion")["geometry"] is False
    return "line on ch1, zone on ch3, tamper on ch4, motion everywhere"


@case("every analytic has the required, correctly-typed fields")
def t_shape():
    from drivers.mock import MockDriver
    caps = MockDriver("http://mock").capabilities()
    assert caps["channels"], "mock returned no channels"
    for ch in caps["channels"]:
        assert isinstance(ch["channel"], str)
        for x in ch["analytics"]:
            for f in ("key", "label", "supported", "active", "geometry"):
                assert f in x, f"missing '{f}' in {x}"
            assert isinstance(x["supported"], bool)
            assert isinstance(x["active"], bool)
            assert isinstance(x["geometry"], bool)
            # you cannot be active without being supported
            assert not (x["active"] and not x["supported"]), \
                f"{x['key']} active but unsupported"
    return "shape valid; no active-without-supported"


@case("the probe fails safe when the device errors")
def t_fail_safe():
    from drivers.dahua import DahuaDriver
    # points at a closed port: every query errors
    d = DahuaDriver("http://127.0.0.1:1", "x", "x", timeout=1)
    try:
        caps = d.capabilities()
    except Exception as e:                         # noqa: BLE001
        raise AssertionError(f"probe crashed instead of failing safe: {e}")
    finally:
        d.close()
    # no channels learnable -> empty, not an exception
    assert caps == {"channels": []} or caps["channels"] == [], caps
    return "returned empty, did not crash"


def run():
    print("Capability probe")
    print("=" * 62)
    p = f = 0
    for name, fn in CASES:
        try:
            print(f"  PASS  {name}\n          {fn()}"); p += 1
        except AssertionError as e:
            print(f"  FAIL  {name}\n          {e}"); f += 1
        except Exception as e:                     # noqa: BLE001
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}"); f += 1
    print("=" * 62)
    print(f"  {p} passed, {f} failed")
    return 1 if f else 0


def test_capabilities():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
