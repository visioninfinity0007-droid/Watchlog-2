#!/usr/bin/env python3
"""What Setup actually offers the customer, and what the support log records.

The HASCO Steel Hikvision answered on port 80 only: ONVIF was disabled, 8000 and 554
were closed. Discovery has to offer that host anyway, has to merge it when more than
one technique finds it, and has to leave a diagnosable trail when something fails --
both halves of discover_recorders() previously ended in `except Exception: pass`.

    pytest -q prototype/tests/test_discovery_candidates.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import discover           # noqa: E402
import setup_backend as sb  # noqa: E402

NVR = "192.168.18.184"


class _Item:
    def __init__(self, ip, name="", hardware=""):
        self.ip, self.name, self.hardware = ip, name, hardware


class DiscoveryHarness(unittest.TestCase):
    """Drives the REAL discover_recorders with the network layer replaced."""

    def setUp(self):
        self.logged: list[str] = []
        self._orig = {
            "ws": sb.wsdiscovery.discover,
            "sweep": sb.discover.sweep,
            "neigh": sb.netscan.neighbours,
            "ifaces": sb.netscan.enumerate_interfaces,
            "log": sb._setup_log,
        }
        sb._setup_log = self.logged.append
        sb.netscan.enumerate_interfaces = lambda: [
            sb.netscan.Interface("192.168.18.190", 24, "Ethernet")]
        sb.netscan.neighbours = lambda: [NVR]
        self.addCleanup(self._restore)

    def _restore(self):
        sb.wsdiscovery.discover = self._orig["ws"]
        sb.discover.sweep = self._orig["sweep"]
        sb.netscan.neighbours = self._orig["neigh"]
        sb.netscan.enumerate_interfaces = self._orig["ifaces"]
        sb._setup_log = self._orig["log"]

    def run_discovery(self, *, onvif=None, hits=None, onvif_error=None, sweep_error=None):
        def ws(log=None, **kw):
            if onvif_error:
                raise onvif_error
            return onvif or []

        def sweep(subnet=None, log=None):
            if sweep_error:
                raise sweep_error
            return hits or []

        sb.wsdiscovery.discover = ws
        sb.discover.sweep = sweep
        return sb.discover_recorders()

    @property
    def log_text(self):
        return "\n".join(self.logged)


# ------------------------------------------------------- TEST 2: port 80 is enough
class Port80IsEnough(DiscoveryHarness):
    def test_a_hikvision_on_port_80_only_is_offered(self):
        """THE HASCO CASE. ONVIF off, 8000 closed, 554 closed."""
        rows = self.run_discovery(onvif=[], hits=[(NVR, [80])])
        self.assertEqual(1, len(rows))
        self.assertEqual(NVR, rows[0]["ip"])
        self.assertIn(80, rows[0]["ports"])

    def test_port_8000_is_not_required(self):
        rows = self.run_discovery(onvif=[], hits=[(NVR, [80])])
        self.assertTrue(rows, "requiring 8000 is what hid the HASCO recorder")

    def test_an_https_only_recorder_is_offered(self):
        rows = self.run_discovery(onvif=[], hits=[(NVR, [443])])
        self.assertEqual(1, len(rows))


# ------------------------------------------------- TESTS 3 & 4: family vendor hints
class VendorHints(DiscoveryHarness):
    def test_port_8000_keeps_the_hikvision_family_hint(self):
        rows = self.run_discovery(hits=[(NVR, [80, 8000])])
        self.assertEqual("hikvision", rows[0]["vendor_hint"])
        self.assertIn("Hikvision", rows[0]["label"])

    def test_port_37777_keeps_the_dahua_family_hint(self):
        rows = self.run_discovery(hits=[("192.168.18.50", [80, 37777])])
        self.assertEqual("dahua", rows[0]["vendor_hint"])
        self.assertIn("Dahua", rows[0]["label"])

    def test_xiongmai_is_still_reported_as_unsupported(self):
        rows = self.run_discovery(hits=[("192.168.18.60", [34567])])
        self.assertIn("Xiongmai", rows[0]["label"])


# ----------------------------------------------------------- TEST 5: ONVIF disabled
class OnvifDisabled(DiscoveryHarness):
    def test_tcp_fallback_finds_the_recorder_when_onvif_returns_nothing(self):
        rows = self.run_discovery(onvif=[], hits=[(NVR, [80, 554])])
        self.assertEqual([NVR], [r["ip"] for r in rows])
        self.assertIn("ONVIF returned 0 device(s)", self.log_text)


# ---------------------------------------------------------------- TEST 7: merging
class ResultsAreMerged(DiscoveryHarness):
    def test_one_row_per_ip_with_enriched_metadata(self):
        rows = self.run_discovery(
            onvif=[_Item(NVR, name="DS-7608NI", hardware="Hikvision")],
            hits=[(NVR, [80, 554, 8000])],
        )
        self.assertEqual(1, len(rows), "the same recorder must not appear twice")
        row = rows[0]
        self.assertEqual([80, 554, 8000], row["ports"], "ONVIF hit must gain its ports")
        self.assertEqual("hikvision", row["vendor_hint"])
        self.assertIn("ONVIF", row["source"])
        self.assertIn("Network scan", row["source"])
        self.assertIn("DS-7608NI", row["label"], "the richer ONVIF name must survive")

    def test_a_weak_onvif_label_is_upgraded_by_a_specific_scan_hint(self):
        rows = self.run_discovery(onvif=[_Item(NVR)], hits=[(NVR, [8000])])
        self.assertIn("Hikvision", rows[0]["label"],
                      "'Compatible recorder' must not block a specific family hint")


# ----------------------------------------------------- TEST 6 & 12: failures visible
class FailuresAreVisibleNotSwallowed(DiscoveryHarness):
    def test_an_onvif_failure_still_lets_the_tcp_scan_find_the_recorder(self):
        rows = self.run_discovery(onvif_error=OSError("multicast blocked"),
                                  hits=[(NVR, [80])])
        self.assertEqual([NVR], [r["ip"] for r in rows],
                         "ONVIF is the first attempt, never the only one")
        self.assertIn("ONVIF discovery failed", self.log_text)
        self.assertIn("multicast blocked", self.log_text)

    def test_a_scan_failure_is_recorded_rather_than_hidden(self):
        rows = self.run_discovery(sweep_error=OSError("socket exhausted"))
        self.assertEqual([], rows)
        self.assertIn("network scan failed", self.log_text)
        self.assertIn("socket exhausted", self.log_text)

    def test_the_log_records_what_support_needs(self):
        self.run_discovery(onvif=[], hits=[(NVR, [80])])
        for expected in ("interface Ethernet 192.168.18.190/24",
                         "network=192.168.18.0/24",
                         "ONVIF returned 0 device(s)",
                         "already known to this PC",
                         "1 host(s) answered a recorder port",
                         f"candidate {NVR}",
                         "finished with 1 candidate(s)"):
            self.assertIn(expected, self.log_text, f"missing from support log: {expected}")

    def test_no_secret_reaches_the_log(self):
        self.run_discovery(onvif=[], hits=[(NVR, [80])])
        for secret in ("password", "apikey", "api_key", "sb_secret", "Bearer "):
            self.assertNotIn(secret.lower(), self.log_text.lower())

    def test_elapsed_time_is_recorded(self):
        self.run_discovery(onvif=[], hits=[])
        self.assertRegex(self.log_text, r"finished with 0 candidate\(s\) in \d+\.\d+s")


# ------------------------------------------- the CLI diagnostic must agree with Setup
class DiagnosticAgreesWithSetup(unittest.TestCase):
    def test_sweep_report_offers_a_port_80_only_host_as_a_recorder(self):
        out: list[str] = []
        discover.sweep_report([(NVR, [80])], log=out.append)
        text = "\n".join(out)
        self.assertIn("LIKELY RECORDER", text,
                      "support must not be told 'could be the router' for a real NVR")
        self.assertIn(NVR, text)

    def test_a_dahua_sdk_port_is_still_called_out(self):
        out: list[str] = []
        discover.sweep_report([("192.168.18.50", [37777])], log=out.append)
        self.assertIn("Dahua", "\n".join(out))

    def test_nothing_open_still_reports_nothing(self):
        out: list[str] = []
        discover.sweep_report([], log=out.append)
        self.assertIn("Nothing on this network answered", "\n".join(out))


if __name__ == "__main__":
    unittest.main(verbosity=1)


# ------------------------------------------------------------- TEST 10: manual IP
class ManualIpEntryIsIndependent(unittest.TestCase):
    """A customer who types the recorder's address must never be blocked because
    automatic discovery came back empty. That was HASCO's only working route."""

    def test_a_manually_entered_host_is_probed_directly(self):
        # _probe_web_ports is the live network step; the point is that the login
        # plan is built from the host the customer typed, not from a scan result.
        attempts, error = sb.plan_recorder_probes(NVR, [80], None)
        self.assertIsNone(error)
        self.assertTrue(attempts, "a manually entered recorder must be probed")
        self.assertTrue(any(NVR in url for _driver, url in attempts))

    def test_port_80_alone_is_enough_to_attempt_a_login(self):
        attempts, error = sb.plan_recorder_probes(NVR, [80], "hikvision")
        self.assertIsNone(error)
        self.assertEqual("hikvision-isapi", attempts[0][0],
                         "the Hikvision driver must lead for a Hikvision hint")
        self.assertIn(f"http://{NVR}", attempts[0][1])

    def test_an_unreachable_host_is_reported_honestly(self):
        attempts, error = sb.plan_recorder_probes(NVR, [], None)
        self.assertEqual([], attempts)
        self.assertEqual("network", error)

    def test_a_dahua_sdk_only_host_says_the_web_service_is_off(self):
        attempts, error = sb.plan_recorder_probes(NVR, [37777], None)
        self.assertEqual([], attempts)
        self.assertEqual("web_unreachable", error,
                         "telling the customer to enable HTTP is actionable")
