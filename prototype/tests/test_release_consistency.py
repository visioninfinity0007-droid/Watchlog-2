#!/usr/bin/env python3
"""Phase 11 — release consistency gate: Website = Portal = Manifest = Artifact (SHA + version)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "tools"))
import check_release_consistency as rc  # noqa: E402

A = "a" * 64
B = "b" * 64


class CompareShas(unittest.TestCase):
    def test_all_agree(self):
        r = rc.compare_shas({"artifact": A, "manifest": A, "website": A.upper(), "portal": A})
        self.assertTrue(r["ok"])
        self.assertEqual(r["canonical"], A)

    def test_mismatch_fails(self):
        r = rc.compare_shas({"artifact": A, "manifest": A, "website": B, "portal": A})
        self.assertFalse(r["ok"])
        self.assertIn("website", r["mismatches"])

    def test_missing_fails(self):
        r = rc.compare_shas({"artifact": A, "manifest": A, "website": "", "portal": A})
        self.assertFalse(r["ok"])
        self.assertIn("website", r["missing"])


class Cli(unittest.TestCase):
    def test_all_agree_exit_zero(self):
        code = rc.main(["--artifact-sha", A, "--manifest-sha", A, "--website-sha", A, "--portal-sha", A,
                        "--artifact-version", "0.4.4", "--manifest-version", "0.4.4"])
        self.assertEqual(code, 0)

    def test_mismatch_exit_one(self):
        code = rc.main(["--artifact-sha", A, "--manifest-sha", B, "--website-sha", A, "--portal-sha", A])
        self.assertEqual(code, 1)

    def test_version_mismatch_exit_one(self):
        code = rc.main(["--artifact-sha", A, "--manifest-sha", A, "--website-sha", A, "--portal-sha", A,
                        "--artifact-version", "0.4.4", "--manifest-version", "0.4.5"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
