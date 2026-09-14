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
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import updater  # noqa: E402
import watchlog_agent as wa  # noqa: E402

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


class ApplyUpdate(unittest.TestCase):
    def _harness(self, *, stage_exits=None, download_ok=True, verify_ok=True,
                 stage_binary_ok=True, register_ok=True):
        calls = []
        stage_exits = stage_exits or {}

        def download(url):
            if not download_ok:
                raise RuntimeError("net down")
            calls.append(("download", url))
            return "/tmp/pkg.exe"

        def verify(path, sha, size):
            calls.append(("verify", path))
            return (verify_ok, "ok" if verify_ok else "sha256 mismatch — refusing to install")

        def run_stage(stage, expected_version=None):
            calls.append(("stage", stage, expected_version))
            return stage_exits.get(stage, 0)

        def stage_binary(pkg, d):
            calls.append(("stage_binary", pkg, d))
            return stage_binary_ok

        def register():
            calls.append(("register",))
            return register_ok

        return calls, dict(download=download, verify=verify, run_stage=run_stage,
                           stage_binary=stage_binary, register=register)

    def _apply(self, **h):
        calls, steps = self._harness(**h)
        res = updater.apply_update("0.4.5", "https://x/pkg.exe", "a" * 64,
                                   install_dir="C:/wl", size=10, **steps)
        stages = [c[1] for c in calls if c[0] == "stage"]
        return res, calls, stages

    def test_happy_path_commits(self):
        res, calls, stages = self._apply()
        self.assertTrue(res["ok"])
        self.assertEqual(res["stage"], "commit")
        self.assertEqual(stages, ["preflight", "verify-version", "commit"])
        self.assertIn(("stage_binary", "/tmp/pkg.exe", "C:/wl"), calls)
        self.assertIn(("register",), calls)

    def test_download_failure_makes_no_changes(self):
        res, calls, stages = self._apply(download_ok=False)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "download")
        self.assertFalse(res["rolled_back"])
        self.assertEqual(stages, [])                     # never touched the agent

    def test_bad_hash_refused_before_preflight(self):
        res, _calls, stages = self._apply(verify_ok=False)
        self.assertEqual(res["stage"], "verify")
        self.assertIn("sha256", res["detail"])
        self.assertEqual(stages, [])                     # corrupt package never installed

    def test_preflight_refusal_no_rollback(self):
        res, _calls, stages = self._apply(stage_exits={"preflight": 10})
        self.assertEqual(res["stage"], "preflight")
        self.assertFalse(res["rolled_back"])
        self.assertNotIn("rollback", stages)             # nothing changed -> nothing to roll back

    def test_stage_binary_failure_rolls_back(self):
        res, _calls, stages = self._apply(stage_binary_ok=False)
        self.assertEqual(res["stage"], "stage")
        self.assertTrue(res["rolled_back"])
        self.assertIn("rollback", stages)

    def test_version_mismatch_rolls_back(self):
        res, _calls, stages = self._apply(stage_exits={"verify-version": 11})
        self.assertEqual(res["stage"], "verify-version")
        self.assertTrue(res["rolled_back"])
        self.assertIn("rollback", stages)

    def test_register_failure_rolls_back(self):
        res, _calls, stages = self._apply(register_ok=False)
        self.assertEqual(res["stage"], "register")
        self.assertTrue(res["rolled_back"])

    def test_commit_failure_rolls_back(self):
        res, _calls, stages = self._apply(stage_exits={"commit": 12})
        self.assertEqual(res["stage"], "commit")
        self.assertTrue(res["rolled_back"])
        self.assertIn("rollback", stages)

    def test_rollback_itself_failing_is_reported(self):
        res, _calls, _stages = self._apply(stage_exits={"commit": 12, "rollback": 1})
        self.assertFalse(res["ok"])
        self.assertFalse(res["rolled_back"])             # rollback also failed -> reported honestly


def _update_cfg(**over):
    cfg = dict(update_url="https://dl.watchlog.app/manifest.json", update_channel="production",
               update_public_key="", update_require_signature=True)
    cfg.update(over)
    return SimpleNamespace(**cfg)


def _run_check(cfg, fetch):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = wa.cmd_check_update(cfg, _fetch=fetch)
    out = buf.getvalue()
    plan = json.loads(out.split("UPDATE_JSON ", 1)[1].splitlines()[0]) if "UPDATE_JSON " in out else None
    return code, out, plan


class CmdCheckUpdate(unittest.TestCase):
    def test_not_configured(self):
        code, out, _ = _run_check(_update_cfg(update_url=""), lambda url: "")
        self.assertEqual(code, 1)
        self.assertIn("not configured", out)

    def test_refuses_non_https(self):
        code, out, _ = _run_check(_update_cfg(update_url="http://dl.watchlog.app/m.json"),
                                  lambda url: "")
        self.assertEqual(code, 1)
        self.assertIn("non-HTTPS", out)

    def test_network_error_is_reported_not_crash(self):
        def boom(url):
            raise RuntimeError("dns failure")
        code, out, _ = _run_check(_update_cfg(), boom)
        self.assertEqual(code, 1)
        self.assertIn("could not fetch", out)
        self.assertNotIn("dns failure", out)   # raw error not echoed

    def test_unsigned_blocked_by_default(self):
        code, _out, plan = _run_check(_update_cfg(), lambda url: json.dumps(base_manifest(version="0.9.9")))
        self.assertEqual(code, 2)
        self.assertEqual(plan["reason"], "manifest_unsigned")

    def test_update_available_when_signature_not_required(self):
        cfg = _update_cfg(update_require_signature=False)
        code, out, plan = _run_check(cfg, lambda url: json.dumps(base_manifest(version="99.0.0")))
        self.assertEqual(code, 0)
        self.assertEqual(plan["action"], "update")
        self.assertIn("update available", out)

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography backend not available")
    def test_signed_manifest_reports_update(self):
        priv = Ed25519PrivateKey.generate()
        pub_b64 = base64.b64encode(priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        m = base_manifest(version="99.0.0")
        m["signature"] = base64.b64encode(priv.sign(updater.canonical_manifest_bytes(m))).decode()
        code, _out, plan = _run_check(_update_cfg(update_public_key=pub_b64),
                                      lambda url: json.dumps(m))
        self.assertEqual(code, 0)
        self.assertEqual(plan["action"], "update")


class CmdUpdate(unittest.TestCase):
    def _run(self, cfg, fetch, apply_fn):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = wa.cmd_update(cfg, _fetch=fetch, _apply=apply_fn)
        return code, buf.getvalue()

    def test_applies_when_update_available(self):
        captured = {}

        def fake_apply(target, url, sha, **kw):
            captured.update(target=target, url=url, sha=sha, kw=kw)
            return {"ok": True, "stage": "commit", "rolled_back": False, "detail": "updated"}
        code, out = self._run(_update_cfg(update_require_signature=False),
                              lambda u: json.dumps(base_manifest(version="99.0.0")), fake_apply)
        self.assertEqual(code, 0)
        self.assertEqual(captured["target"], "99.0.0")
        self.assertTrue(captured["url"].endswith("watchlog-agent.exe"))
        self.assertIn("install_dir", captured["kw"])
        self.assertIn("applying transactionally", out)

    def test_up_to_date_does_not_apply(self):
        called = {"n": 0}

        def fake_apply(*a, **k):
            called["n"] += 1
            return {"ok": True}
        code, out = self._run(_update_cfg(update_require_signature=False),
                              lambda u: json.dumps(base_manifest(version="0.0.1")), fake_apply)
        self.assertEqual(code, 0)
        self.assertEqual(called["n"], 0)
        self.assertIn("already up to date", out)

    def test_blocked_unsigned_does_not_apply(self):
        called = {"n": 0}

        def fake_apply(*a, **k):
            called["n"] += 1
            return {}
        code, _out = self._run(_update_cfg(),        # require_signature True + no key -> unsigned
                               lambda u: json.dumps(base_manifest(version="99.0.0")), fake_apply)
        self.assertEqual(code, 2)
        self.assertEqual(called["n"], 0)

    def test_rolled_back_returns_2(self):
        def fake_apply(*a, **k):
            return {"ok": False, "stage": "commit", "rolled_back": True, "detail": "verify failed"}
        code, _out = self._run(_update_cfg(update_require_signature=False),
                               lambda u: json.dumps(base_manifest(version="99.0.0")), fake_apply)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
