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

    def test_router_answer_does_not_hide_native_recorder_signature(self):
        """A generic router response must not hide a Dahua recorder on the same LAN."""
        def fake_conn(address, timeout=None):
            ip, port = address
            if ip == "10.0.0.1" and port == 80:
                return MagicMock()
            if ip == "10.0.0.119" and port == 37777:
                return MagicMock()
            raise OSError("filtered")

        progress = []
        with patch.object(discover, "_sweep_bases",
                          return_value=(["10.0.0"], ["10.0.0.5"])), \
             patch("socket.create_connection", side_effect=fake_conn):
            hits = discover.sweep(log=lambda *_a: None, progress=progress.append)

        by_ip = dict(hits)
        self.assertIn(80, by_ip["10.0.0.1"])
        self.assertIn(37777, by_ip["10.0.0.119"])
        self.assertTrue(any("confirm" in msg.lower() for msg in progress))

    def test_multiple_ranked_lans_preserve_multiple_recorder_candidates(self):
        """Bounding discovery must not hide a second real recorder LAN."""
        def fake_conn(address, timeout=None):
            ip, port = address
            if ip == "10.44.7.119" and port == 8000:
                return MagicMock()
            if ip == "192.168.10.108" and port == 37777:
                return MagicMock()
            raise OSError("filtered")

        with patch.object(discover, "_sweep_bases",
                          return_value=(["10.44.7", "192.168.10"], ["10.44.7.20", "192.168.10.25"])), \
             patch("socket.create_connection", side_effect=fake_conn):
            hits = discover.sweep(log=lambda *_a: None, progress=lambda *_a: None)

        by_ip = dict(hits)
        self.assertIn(8000, by_ip["10.44.7.119"])
        self.assertIn(37777, by_ip["192.168.10.108"])

    def test_build69_eighth_subnet_recorder_is_still_discovered(self):
        """Field-proven Build 69 searched eight /24s; keep that reach permanently."""
        bases = [
            "10.0.1", "10.0.2", "10.0.3", "10.0.4",
            "10.0.5", "10.0.6", "10.0.7", "10.0.8",
        ]
        def fake_conn(address, timeout=None):
            ip, port = address
            if ip == "10.0.8.108" and port == 8000:
                return MagicMock()
            raise OSError("filtered")

        with patch.object(discover, "_sweep_bases",
                          return_value=(bases, [f"{b}.20" for b in bases])), \
             patch("socket.create_connection", side_effect=fake_conn):
            hits = discover.sweep(log=lambda *_a: None, progress=lambda *_a: None)

        self.assertIn("10.0.8.108", dict(hits))
        self.assertIn(8000, dict(hits)["10.0.8.108"])

    def test_physical_adapters_rank_before_virtual_adapters(self):
        fake = SimpleNamespace(
            net_if_stats=lambda: {
                "vEthernet (Default Switch)": SimpleNamespace(isup=True),
                "CCTV Ethernet": SimpleNamespace(isup=True),
                "Wi-Fi": SimpleNamespace(isup=True),
            },
            net_if_addrs=lambda: {
                "vEthernet (Default Switch)": [SimpleNamespace(family=socket.AF_INET, address="172.22.64.1")],
                "CCTV Ethernet": [SimpleNamespace(family=socket.AF_INET, address="10.44.7.20")],
                "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="192.168.10.25")],
            },
        )
        with patch.object(discover, "psutil", fake):
            addresses = discover._adapter_ipv4s()
        self.assertEqual(["10.44.7.20", "192.168.10.25", "172.22.64.1"], addresses)

    def test_setup_sweep_preserves_build69_coverage_with_hard_product_budget(self):
        # Build 69 is the field-proven discovery baseline. Future releases may
        # improve ordering/timing, but may not silently shrink its eight-/24 reach.
        self.assertEqual(discover.MAX_AUTO_SUBNETS, 8)
        # Within Build 69's field-proven envelope (768) and enough to reach all eight
        # networks well inside the deadline (test_discovery_field_timing).
        self.assertTrue(512 <= discover.SWEEP_WORKERS <= 768)
        self.assertLessEqual(discover.DISCOVERY_DEADLINE_SECONDS, 32)
        self.assertTrue({80, 443, 8000, 37777}.issubset(set(discover.SWEEP_FAST_PORTS)))
        self.assertTrue({
            80, 443, 8000, 8080, 8443, 81, 82, 88, 8081, 8888,
            554, 37777, 37778, 34567,
        }.issubset(set(discover.SWEEP_PORTS)))

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
