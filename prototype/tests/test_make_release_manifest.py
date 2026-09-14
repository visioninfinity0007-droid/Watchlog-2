#!/usr/bin/env python3
"""Phase 7 — release-manifest generator: single source of truth, sign -> verify -> plan.

Proves tools/make_release_manifest builds a manifest the Agent accepts, that an Ed25519-signed
manifest verifies and yields an 'update' decision, and that HTTPS-only + unsigned refusal hold.
"""
from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT.parent / "tools"))

import updater  # noqa: E402
import make_release_manifest as mk  # noqa: E402

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    HAVE_CRYPTO = True
except Exception:  # noqa: BLE001
    HAVE_CRYPTO = False


class BuildManifest(unittest.TestCase):
    def test_structure_is_agent_parseable(self):
        m = mk.build_manifest(channel="pilot", version="0.4.5", build_sha="abc1234",
                              sha256="A" * 64, size=1234,
                              url="https://dl.watchlog.app/x/WatchLog-Setup-0.4.5.exe", notes="rc")
        parsed = updater.parse_manifest(m)               # must not raise
        rel = updater.select_release(parsed, "pilot")
        self.assertEqual(rel["version"], "0.4.5")
        self.assertEqual(rel["sha256"], "a" * 64)        # lowercased
        self.assertEqual(rel["build_sha"], "abc1234")

    def test_bad_channel_rejected(self):
        with self.assertRaises(ValueError):
            mk.build_manifest(channel="nope", version="1", build_sha="x", sha256="a", size=1, url="https://y")


@unittest.skipUnless(HAVE_CRYPTO, "cryptography backend not available")
class SignVerify(unittest.TestCase):
    def _key(self):
        priv = Ed25519PrivateKey.generate()
        priv_b64 = base64.b64encode(priv.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())).decode()
        pub_b64 = base64.b64encode(priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        return priv_b64, pub_b64

    def test_signed_manifest_verifies_and_plans_update(self):
        priv_b64, pub_b64 = self._key()
        m = mk.build_manifest(channel="production", version="0.4.5", build_sha="deadbee",
                              sha256="b" * 64, size=999,
                              url="https://dl.watchlog.app/r/WatchLog-Setup-0.4.5.exe")
        signed = mk.sign_manifest(m, priv_b64)
        self.assertIs(updater.verify_manifest_signature(signed, pub_b64), True)
        plan = updater.plan_update(signed, "0.4.4", "production",
                                   signature_state=updater.verify_manifest_signature(signed, pub_b64))
        self.assertEqual(plan["action"], "update")
        self.assertEqual(plan["target"], "0.4.5")

    def test_tamper_after_signing_fails(self):
        priv_b64, pub_b64 = self._key()
        m = mk.build_manifest(channel="pilot", version="0.4.5", build_sha="x", sha256="c" * 64,
                              size=1, url="https://dl.watchlog.app/a.exe")
        signed = mk.sign_manifest(m, priv_b64)
        signed["channels"]["pilot"]["url"] = "https://evil.example/a.exe"
        self.assertIs(updater.verify_manifest_signature(signed, pub_b64), False)


class Cli(unittest.TestCase):
    def test_non_https_url_refused(self):
        rc = mk.main(["--channel", "pilot", "--version", "0.4.5", "--build-sha", "x",
                      "--sha256", "a" * 64, "--size", "1", "--url", "http://insecure/a.exe"])
        self.assertEqual(rc, 2)

    def test_unsigned_manifest_is_blocked_by_agent(self):
        m = mk.build_manifest(channel="pilot", version="0.4.5", build_sha="x", sha256="a" * 64,
                              size=1, url="https://dl.watchlog.app/a.exe")
        # no signature -> the Agent refuses under the default require_signature policy
        state = updater.verify_manifest_signature(m, "")     # no key -> None
        plan = updater.plan_update(m, "0.4.4", "pilot", signature_state=state, require_signature=True)
        self.assertEqual(plan["action"], "blocked")
        self.assertEqual(plan["reason"], "manifest_unsigned")


if __name__ == "__main__":
    unittest.main(verbosity=2)
