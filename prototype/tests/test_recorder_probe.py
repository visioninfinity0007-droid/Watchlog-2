#!/usr/bin/env python3
"""Recorder connection-hardening tests (workstream 5): URL building for HTTP/HTTPS/custom
ports, Hikvision SDK-vs-HTTP port classification, ordered candidates, explicit self-signed
trust semantics, response classification, and subnet enumeration. Pure logic — no device."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import recorder_probe as rp  # noqa: E402


class ProbeTests(unittest.TestCase):
    def test_build_base_url(self):
        self.assertEqual("http://10.0.0.5", rp.build_base_url("10.0.0.5"))
        self.assertEqual("http://10.0.0.5:8080", rp.build_base_url("10.0.0.5", 8080))
        self.assertEqual("https://10.0.0.5", rp.build_base_url("10.0.0.5", 443, "https"))
        self.assertEqual("https://10.0.0.5:8443", rp.build_base_url("10.0.0.5", 8443, "https"))
        self.assertEqual("http://10.0.0.5", rp.build_base_url("http://10.0.0.5/"))     # strips scheme/slash
        self.assertEqual("https://[fe80::1]:8443", rp.build_base_url("fe80::1", 8443, "https"))

    def test_classify_port_sdk_vs_http(self):
        self.assertEqual("hikvision-sdk", rp.classify_port(8000))   # NOT http
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
        # a configured SDK port is tried first AND flagged as non-HTTP
        cfg = rp.candidates("hikvision", "10.0.0.5", configured_port=8000)
        self.assertEqual(8000, cfg[0]["port"])
        self.assertEqual("hikvision-sdk", cfg[0]["non_http"])
        # a configured 8443 implies https only
        https = rp.candidates("dahua", "10.0.0.5", configured_port=8443)
        self.assertEqual("https", https[0]["scheme"])
        self.assertEqual("https://10.0.0.5:8443", https[0]["base_url"])

    def test_self_signed_trust_is_explicit(self):
        self.assertTrue(rp.requests_verify(False))          # verify by default
        self.assertFalse(rp.requests_verify(True))          # only when operator opts in
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
