#!/usr/bin/env python3
"""Recorder connection-hardening tests (workstream 5): URL building for HTTP/HTTPS/custom
ports, Hikvision SDK-vs-HTTP port classification, ordered candidates, explicit self-signed
trust semantics, response classification, and subnet enumeration. Pure logic — no device."""
from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import recorder_probe as rp  # noqa: E402
import discover  # noqa: E402


class ProbeTests(unittest.TestCase):
    def test_build_base_url(self):
        self.assertEqual("http://10.0.0.5", rp.build_base_url("10.0.0.5"))
        self.assertEqual("http://10.0.0.5:8080", rp.build_base_url("10.0.0.5", 8080))
        self.assertEqual("https://10.0.0.5", rp.build_base_url("10.0.0.5", 443, "https"))
        self.assertEqual("https://10.0.0.5:8443", rp.build_base_url("10.0.0.5", 8443, "https"))
        self.assertEqual("http://10.0.0.5", rp.build_base_url("http://10.0.0.5/"))
        self.assertEqual("https://[fe80::1]:8443", rp.build_base_url("fe80::1", 8443, "https"))

    def test_classify_port_sdk_vs_http(self):
        self.assertEqual("hikvision-sdk", rp.classify_port(8000))
        self.assertEqual("dahua-sdk", rp.classify_port(37777))
        self.assertEqual("xiongmai", rp.classify_port(34567))
        self.assertIsNone(rp.classify_port(80))
        self.assertIsNone(rp.classify_port(443))

    def test_candidates_order_and_flags(self):
        cands = rp.candidates("hikvision", "10.0.0.5")
        pairs = [(c["scheme"], c["port"]) for c in cands]
        self.assertIn(("http", 80), pairs)
        self.assertIn(("https", 443), pairs)
        self.assertIn(("https", 8443), pairs)
        cfg = rp.candidates("hikvision", "10.0.0.5", configured_port=8000)
        self.assertEqual(8000, cfg[0]["port"])
        self.assertEqual("hikvision-sdk", cfg[0]["non_http"])
        https = rp.candidates("dahua", "10.0.0.5", configured_port=8443)
        self.assertEqual("https", https[0]["scheme"])
        self.assertEqual("https://10.0.0.5:8443", https[0]["base_url"])

    def test_self_signed_trust_is_explicit(self):
        self.assertTrue(rp.requests_verify(False))
        self.assertFalse(rp.requests_verify(True))
        import ssl
        self.assertEqual(ssl.CERT_NONE, rp.tls_context(True).verify_mode)
        self.assertNotEqual(ssl.CERT_NONE, rp.tls_context(False).verify_mode)

    def test_classify_probe(self):
        self.assertEqual("hikvision-isapi", rp.classify_probe(200, {"Server": "App-webs/"}, ""))
        self.assertEqual("dahua-cgi", rp.classify_probe(200, {}, "magicBox getSystemInfo"))
        self.assertEqual("onvif", rp.classify_probe(200, {}, "<GetDeviceInformationResponse> onvif"))
        self.assertEqual("http-auth-required", rp.classify_probe(401, {"WWW-Authenticate": "Digest realm=x"}, ""))
        self.assertEqual("http-unknown-vendor", rp.classify_probe(200, {"Server": "lighttpd"}, "hello"))
        self.assertEqual("unknown", rp.classify_probe(None, {}, ""))

    def test_local_subnets_returns_list(self):
        nets = rp.local_subnets()
        self.assertIsInstance(nets, list)
        for ip, cidr in nets:
            self.assertIn("/", cidr)

    # 0.4.2 field regression: setup's TCP fallback used only the interface
    # Windows chose for the internet. A CCTV Ethernet /24 could therefore be
    # invisible whenever ONVIF multicast was disabled or filtered.
    def test_setup_adapter_enumeration_includes_active_cctv_nic(self):
        fake = SimpleNamespace(
            net_if_stats=lambda: {
                "Wi-Fi": SimpleNamespace(isup=True),
                "CCTV Ethernet": SimpleNamespace(isup=True),
                "Old VPN": SimpleNamespace(isup=False),
            },
            net_if_addrs=lambda: {
                "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="192.168.10.25")],
                "CCTV Ethernet": [SimpleNamespace(family=socket.AF_INET, address="10.44.7.20")],
                "Old VPN": [SimpleNamespace(family=socket.AF_INET, address="10.200.0.2")],
            },
        )
        with patch.object(discover, "psutil", fake):
            addresses = discover._adapter_ipv4s()
        self.assertEqual(["192.168.10.25", "10.44.7.20"], addresses)

    def test_setup_auto_discovery_never_scans_public_ipv4(self):
        self.assertIsNone(discover._usable_ipv4("8.8.8.8"))
        self.assertEqual("192.168.1.20", discover._usable_ipv4("192.168.1.20"))

    def test_setup_sweep_uses_every_distinct_local_subnet(self):
        with patch.object(discover, "local_ipv4s",
                          return_value=["192.168.10.25", "10.44.7.20", "192.168.10.99"]):
            bases, addresses = discover._sweep_bases(None)
        self.assertEqual(["192.168.10", "10.44.7"], bases)
        self.assertEqual(["192.168.10.25", "10.44.7.20", "192.168.10.99"], addresses)

    def test_setup_explicit_subnet_stays_explicit(self):
        with patch.object(discover, "local_ipv4s", return_value=["10.1.2.3", "192.168.8.4"]):
            bases, addresses = discover._sweep_bases("172.16.40.0")
        self.assertEqual(["172.16.40"], bases)
        self.assertEqual([], addresses)

    def test_setup_invalid_explicit_subnet_fails_closed(self):
        bases, addresses = discover._sweep_bases("not-an-ip")
        self.assertEqual([], bases)
        self.assertEqual([], addresses)


class DiscoveryBlindSpotTests(unittest.TestCase):
    """Field regression: the setup wizard showed an empty recorder list ("no recorder
    found") while typing the recorder's IP manually worked. Two causes: the sweep never
    probed the port the recorder actually listened on, and one dropped SYN was
    indistinguishable from an empty network."""

    def test_sweep_ports_cover_every_port_the_login_step_supports(self):
        import setup_backend as sb
        swept = set(discover.SWEEP_PORTS)
        missing_web = set(sb._WEB_PORTS) - swept
        self.assertFalse(
            missing_web,
            f"login step drives web ports discovery never scans: {sorted(missing_web)}")
        self.assertFalse(set(sb._DAHUA_SDK_PORTS) - swept)

    def test_https_only_and_alt_web_port_recorders_are_scanned(self):
        for port in (443, 8443, 81, 88, 8081):
            self.assertIn(
                port, discover.SWEEP_PORTS,
                f"a recorder reachable only on {port} would stay invisible")

    def test_sweep_gives_a_silent_host_a_second_chance(self):
        def fake_conn(address, timeout=None):
            ip, port = address
            if ip == "10.0.0.7" and port == 443 and timeout == discover.SWEEP_RETRY_TIMEOUT:
                return MagicMock()
            raise OSError("filtered")

        with patch.object(discover, "_sweep_bases", return_value=(["10.0.0"], ["10.0.0.5"])), \
             patch("socket.create_connection", side_effect=fake_conn):
            hits = discover.sweep(log=lambda *_a: None)
        self.assertEqual([("10.0.0.7", [443])], hits)

    def test_command_ipv4s_parses_interfaces_and_rejects_masks(self):
        sample = """
Ethernet adapter CCTV:
   IPv4 Address. . . . . . . . . . . : 192.168.1.50
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.1.1
Wireless LAN adapter Wi-Fi:
   IPv4 Address. . . . . . . . . . . : 10.20.30.40
"""
        with patch.object(discover.subprocess, "run",
                          return_value=SimpleNamespace(stdout=sample)):
            found = discover._command_ipv4s()
        self.assertIn("192.168.1.50", found)
        self.assertIn("10.20.30.40", found)
        self.assertNotIn("255.255.255.0", found)

    def test_cctv_nic_is_still_enumerated_without_psutil(self):
        """psutil is an optional import; a build without it must not silently shrink the
        swept network set back to the default-route /24."""
        with patch.object(discover, "psutil", None), \
             patch.object(discover, "_command_ipv4s", return_value=["192.168.1.50"]), \
             patch("socket.getaddrinfo", side_effect=OSError("no dns")):
            found = discover.local_ipv4s()
        self.assertIn("192.168.1.50", found)


if __name__ == "__main__":
    unittest.main(verbosity=1)
