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
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main(verbosity=1)
