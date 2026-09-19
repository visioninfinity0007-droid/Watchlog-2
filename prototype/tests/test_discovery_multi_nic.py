#!/usr/bin/env python3
"""Recorder discovery across every active LAN — the HASCO Steel field failure.

FIELD EVIDENCE (2026-09-19). A Windows PC at 192.168.18.190 could open its Hikvision
NVR at http://192.168.18.184/ in a browser, and `arp -a` on that same PC listed

    Interface: 192.168.18.190
    192.168.18.184  ac-b9-2f-39-5c-89  dynamic

yet WatchLog Setup reported that no recorder was found. The recorder was reachable the
whole time; discovery was the thing that failed, and it failed silently.

Each test below is one way that happened, or one way it must not happen again.

    pytest -q prototype/tests/test_discovery_multi_nic.py
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import netscan  # noqa: E402

NVR = "192.168.18.184"
PC = "192.168.18.190"


def iface(ip, prefix=24, name=""):
    return netscan.Interface(ip=ip, prefix=prefix, name=name)


# --------------------------------------------------------------- TEST 1: multi-NIC
class MultiNicDiscovery(unittest.TestCase):
    """THE FIELD CASE. The Internet-facing adapter is NOT the CCTV adapter."""

    def test_the_cctv_lan_is_scanned_even_when_another_nic_owns_the_default_route(self):
        interfaces = netscan.enumerate_interfaces(
            sources=[lambda: [iface("10.10.10.5", 24, "Wi-Fi"),
                              iface(PC, 24, "Ethernet")]],
            default_route=lambda: iface("10.10.10.5", 24, "default route"),
        )
        addresses = [i.ip for i in interfaces]
        self.assertIn("10.10.10.5", addresses)
        self.assertIn(PC, addresses, "the CCTV adapter must be enumerated")

        targets, _notes = netscan.scan_targets(interfaces)
        self.assertIn(NVR, targets,
                      "the HASCO recorder must be in the scan set; this is the field bug")

    def test_a_vpn_or_hyperv_adapter_does_not_displace_the_real_lan(self):
        interfaces = netscan.enumerate_interfaces(
            sources=[lambda: [iface("172.16.5.2", 24, "VPN"),
                              iface("172.17.0.1", 16, "vEthernet (Default Switch)"),
                              iface(PC, 24, "Ethernet")]],
            default_route=lambda: iface("172.16.5.2", 24, "default route"),
        )
        self.assertIn(PC, [i.ip for i in interfaces])
        targets, _ = netscan.scan_targets(interfaces)
        self.assertIn(NVR, targets)


# ------------------------------------------------- TEST 8 / 9: prefixes & exclusions
class PrefixHandling(unittest.TestCase):
    def test_a_non_24_prefix_is_honoured_not_assumed(self):
        """A /23 spans two /24s. Assuming /24 would miss half the network."""
        targets, _ = netscan.scan_targets([iface("192.168.18.190", 23)])
        self.assertIn(NVR, targets)
        self.assertIn("192.168.19.10", targets, "the second half of the /23 must be scanned")

    def test_a_25_is_not_widened_to_a_24(self):
        targets, _ = netscan.scan_targets([iface("192.168.18.190", 25)])
        self.assertIn(NVR, targets)
        self.assertNotIn("192.168.18.10", targets, "/25 does not contain .10")

    def test_netmask_to_prefix(self):
        self.assertEqual(24, netscan.prefix_from_netmask("255.255.255.0"))
        self.assertEqual(23, netscan.prefix_from_netmask("255.255.254.0"))
        self.assertIsNone(netscan.prefix_from_netmask("not-a-mask"))


class UnsuitableAdaptersAreExcluded(unittest.TestCase):
    """TEST 9. Scanning these wastes the customer's time and finds nothing."""

    def test_loopback_link_local_public_and_masks_are_rejected(self):
        for bad in ("127.0.0.1", "169.254.10.4", "8.8.8.8", "0.0.0.0",
                    "255.255.255.0", "255.255.254.0"):
            self.assertIsNone(netscan.usable_ipv4(bad), f"{bad} must not be scanned")

    def test_a_private_lan_address_is_accepted(self):
        for good in (PC, "10.10.10.5", "172.16.5.2"):
            self.assertEqual(good, netscan.usable_ipv4(good))

    def test_a_subnet_mask_is_never_enumerated_as_an_adapter(self):
        """Measured on a real Windows box: 255.255.255.0 passed is_private (240/4 is
        reported private by Python) and generated 254 junk targets in 255.255.255.0/24."""
        text = ("   IPv4 Address. . . . . . . . . . . : 192.168.18.190\n"
                "   Subnet Mask . . . . . . . . . . . : 255.255.255.0\n"
                "   Default Gateway . . . . . . . . . : 192.168.18.1\n")
        interfaces = netscan._via_command(reader=lambda cmd, timeout: text)
        self.assertNotIn("255.255.255.0", [i.ip for i in interfaces])
        self.assertIn(PC, [i.ip for i in interfaces])

    def test_the_real_netmask_is_read_from_the_os_dump(self):
        text = ("   IPv4 Address. . . . . . . . . . . : 192.168.18.190\n"
                "   Subnet Mask . . . . . . . . . . . : 255.255.254.0\n")
        interfaces = netscan._via_command(reader=lambda cmd, timeout: text)
        self.assertEqual(23, interfaces[0].prefix, "a /23 site must not be scanned as /24")

    def test_a_disconnected_adapter_is_skipped(self):
        """enumerate_interfaces must survive a source that returns nothing useful."""
        interfaces = netscan.enumerate_interfaces(
            sources=[lambda: []], default_route=lambda: None)
        self.assertEqual([], interfaces)


# ------------------------------------------------------------- TEST 3: ARP pre-pass
class NeighbourPrePass(unittest.TestCase):
    def test_arp_entries_become_priority_candidates(self):
        arp = ("Interface: 192.168.18.190 --- 0x5\n"
               "  Internet Address      Physical Address      Type\n"
               "  192.168.18.1          aa-bb-cc-dd-ee-ff     dynamic\n"
               "  192.168.18.184        ac-b9-2f-39-5c-89     dynamic\n"
               "  192.168.18.255        ff-ff-ff-ff-ff-ff     static\n")
        known = netscan.neighbours(reader=lambda cmd, timeout: arp)
        self.assertIn(NVR, known, "the HASCO NVR was in ARP the whole time")
        self.assertNotIn("192.168.18.255", known, "broadcast is not a device")

    def test_known_hosts_are_probed_before_a_bounded_sweep(self):
        targets, _ = netscan.scan_targets([iface("10.0.0.5", 16)], extra=[NVR])
        self.assertEqual(NVR, targets[0],
                         "a host the OS already knows must not be lost behind a big sweep")


# --------------------------------------------------------- TEST 11: runtime bounded
class DiscoveryIsBounded(unittest.TestCase):
    def test_a_16_does_not_expand_to_65k_targets(self):
        started = time.monotonic()
        targets, notes = netscan.scan_targets([iface("10.0.0.5", 16)])
        self.assertLessEqual(len(targets), netscan.MAX_TOTAL_SWEEP_HOSTS)
        self.assertTrue(notes, "a truncated sweep must be reported, not silently cut")
        self.assertLess(time.monotonic() - started, 5.0)

    def test_many_interfaces_cannot_blow_the_overall_budget(self):
        many = [iface(f"10.{n}.0.5", 16) for n in range(20)]
        targets, notes = netscan.scan_targets(many)
        self.assertLessEqual(len(targets), netscan.MAX_TOTAL_SWEEP_HOSTS)
        self.assertTrue(any("overall" in n for n in notes))

    def test_a_normal_24_is_never_truncated(self):
        targets, notes = netscan.scan_targets([iface(PC, 24)])
        self.assertEqual(254, len(targets))
        self.assertEqual([], notes, "a small LAN must be swept completely")


# ----------------------------------------------- TEST 6: one bad source never blinds
class OneFailingSourceDoesNotStopDiscovery(unittest.TestCase):
    def test_an_enumeration_source_that_raises_is_survived(self):
        def broken():
            raise OSError("WMI unavailable")
        interfaces = netscan.enumerate_interfaces(
            sources=[broken, lambda: [iface(PC, 24, "Ethernet")]],
            default_route=lambda: None,
        )
        self.assertIn(PC, [i.ip for i in interfaces],
                      "a failure on one source must not hide the CCTV adapter")

    def test_a_failing_os_command_yields_nothing_rather_than_crashing(self):
        def boom(cmd, timeout):
            raise OSError("command not found")
        self.assertEqual([], netscan.neighbours(reader=boom))


if __name__ == "__main__":
    unittest.main(verbosity=1)
