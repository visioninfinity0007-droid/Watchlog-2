#!/usr/bin/env python3
"""0.4.4 §13/§14 — in-app update decision + integrity engine (no network / no processes).

Two layers:
  * stdlib-only decision + integrity: version compare, manifest parse, channel selection, SHA-256 +
    size payload gating, and plan_update's signature-first / version / channel / min-version policy;
  * Ed25519 signature round-trip (skipped only where the cryptography backend is absent): a valid
    signature verifies, a tampered manifest or wrong key does not, and plan_update refuses an
    unverifiable manifest — the "trust nothing" gate, proven.
"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import updater  # noqa: E402

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    HAVE_CRYPTO = True
except Exception:  # noqa: BLE001
    HAVE_CRYPTO = False


def base_manifest(version="0.4.5", channel="production", min_agent=None):
    rel = {"version": version, "url": f"https://dl.watchlog.app/{version}/watchlog-agent.exe",
           "sha256": "a" * 64, "size": 1234, "notes": "test"}
    if min_agent:
        rel["min_agent_version"] = min_agent
    return {"schema": updater.MANIFEST_SCHEMA, "generated_at": "2026-09-14T00:00:00Z",
            "channels": {channel: rel}}


class Versions(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(updater.parse_version("v0.4.5+abc1234"), (0, 4, 5))
        self.assertEqual(updater.parse_version("0.4.5-rc1"), (0, 4, 5))
        self.assertEqual(updater.parse_version("garbage"), (0, 0, 0))
        self.assertTrue(updater.is_newer("0.4.5", "0.4.4"))
        self.assertFalse(updater.is_newer("0.4.4", "0.4.4"))
        self.assertFalse(updater.is_newer("0.4.3", "0.4.4"))


class Manifest(unittest.TestCase):
    def test_parse_valid_forms(self):
        m = base_manifest()
        import json
        self.assertEqual(updater.parse_manifest(json.dumps(m)), m)
        self.assertEqual(updater.parse_manifest(json.dumps(m).encode()), m)
        self.assertEqual(updater.parse_manifest(m), m)

    def test_rejects_bad_schema_and_empty_channels(self):
        with self.assertRaises(ValueError):
            updater.parse_manifest({"schema": "nope", "channels": {"pilot": {}}})
        with self.assertRaises(ValueError):
            updater.parse_manifest({"schema": updater.MANIFEST_SCHEMA, "channels": {}})

    def test_canonical_bytes_exclude_signature_and_are_stable(self):
        m = base_manifest()
        m_signed = dict(m, signature="ZZZ")
        self.assertEqual(updater.canonical_manifest_bytes(m),
                         updater.canonical_manifest_bytes(m_signed))
        # deterministic regardless of key order
        self.assertEqual(updater.canonical_manifest_bytes({"channels": m["channels"], "schema": m["schema"],
                                                           "generated_at": m["generated_at"]}),
                         updater.canonical_manifest_bytes(m))


class Payload(unittest.TestCase):
    def _tmp(self, data: bytes):
        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(data)
        f.close()
        return Path(f.name)

    def test_verify_ok(self):
        p = self._tmp(b"binary-bytes")
        digest = updater.sha256_file(p)
        ok, detail = updater.verify_payload(p, digest, len(b"binary-bytes"))
        self.assertTrue(ok, detail)

    def test_sha_mismatch_refused(self):
        p = self._tmp(b"binary-bytes")
        ok, detail = updater.verify_payload(p, "b" * 64)
        self.assertFalse(ok)
        self.assertIn("sha256 mismatch", detail)

    def test_size_mismatch_refused(self):
        p = self._tmp(b"binary-bytes")
        ok, detail = updater.verify_payload(p, updater.sha256_file(p), 999999)
        self.assertFalse(ok)
        self.assertIn("size mismatch", detail)

    def test_missing_refused(self):
        ok, detail = updater.verify_payload(Path("/nonexistent/x.exe"), "a" * 64)
        self.assertFalse(ok)


class PlanUpdate(unittest.TestCase):
    def test_unknown_channel_blocked(self):
        r = updater.plan_update(base_manifest(), "0.4.4", "banana", signature_state=True)
        self.assertEqual(r["action"], "blocked")
        self.assertEqual(r["reason"], "unknown_channel")

    def test_unsigned_blocked_even_if_up_to_date(self):
        # trust nothing from an unverifiable manifest
        r = updater.plan_update(base_manifest(version="0.4.4"), "0.4.4", "production",
                                signature_state=None, require_signature=True)
        self.assertEqual(r["action"], "blocked")
        self.assertEqual(r["reason"], "manifest_unsigned")

    def test_bad_signature_blocked(self):
        r = updater.plan_update(base_manifest(), "0.4.4", "production", signature_state=False)
        self.assertEqual(r["reason"], "bad_signature")

    def test_update_available_when_signed_and_newer(self):
        r = updater.plan_update(base_manifest(version="0.4.5"), "0.4.4", "production",
                                signature_state=True)
        self.assertEqual(r["action"], "update")
        self.assertEqual(r["target"], "0.4.5")
        self.assertTrue(r["url"].endswith("watchlog-agent.exe"))

    def test_up_to_date_when_not_newer(self):
        r = updater.plan_update(base_manifest(version="0.4.4"), "0.4.4", "production",
                                signature_state=True)
        self.assertEqual(r["action"], "up-to-date")

    def test_agent_too_old_blocked(self):
        r = updater.plan_update(base_manifest(version="0.5.0", min_agent="0.4.9"), "0.4.4",
                                "production", signature_state=True)
        self.assertEqual(r["action"], "blocked")
        self.assertEqual(r["reason"], "agent_too_old")

    def test_require_signature_false_allows_unsigned(self):
        r = updater.plan_update(base_manifest(version="0.4.5"), "0.4.4", "production",
                                signature_state=None, require_signature=False)
        self.assertEqual(r["action"], "update")


@unittest.skipUnless(HAVE_CRYPTO, "cryptography backend not available")
class Ed25519Signature(unittest.TestCase):
    def _keypair(self):
        priv = Ed25519PrivateKey.generate()
        pub_raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                                 serialization.PublicFormat.Raw)
        return priv, base64.b64encode(pub_raw).decode()

    def _sign(self, priv, manifest):
        sig = priv.sign(updater.canonical_manifest_bytes(manifest))
        return base64.b64encode(sig).decode()

    def test_valid_signature_verifies_and_plans_update(self):
        priv, pub_b64 = self._keypair()
        m = base_manifest(version="0.4.5")
        m["signature"] = self._sign(priv, m)
        state = updater.verify_manifest_signature(m, pub_b64)
        self.assertIs(state, True)
        r = updater.plan_update(m, "0.4.4", "production", signature_state=state)
        self.assertEqual(r["action"], "update")

    def test_tampered_manifest_fails(self):
        priv, pub_b64 = self._keypair()
        m = base_manifest(version="0.4.5")
        m["signature"] = self._sign(priv, m)
        m["channels"]["production"]["url"] = "https://evil.example/watchlog-agent.exe"  # tamper
        self.assertIs(updater.verify_manifest_signature(m, pub_b64), False)

    def test_wrong_key_fails(self):
        priv, _pub = self._keypair()
        _priv2, pub2_b64 = self._keypair()
        m = base_manifest()
        m["signature"] = self._sign(priv, m)
        self.assertIs(updater.verify_manifest_signature(m, pub2_b64), False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
