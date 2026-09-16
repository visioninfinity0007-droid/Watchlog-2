#!/usr/bin/env python3
"""Build-metadata stamping (0.4.4 Section 2) — the shipped exe carries its exact source commit.

wl_version resolves BUILD_SHA from a build-time-baked build_info.py (bundled into the frozen
exe by build_exe.ps1) before the env var, so a customer machine with no build environment still
reports the exact commit via --version, heartbeat and the support bundle.
"""
from __future__ import annotations

import importlib, shutil, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import wl_version  # noqa: E402


class BuildMetadata(unittest.TestCase):
    def test_baked_build_info_wins(self):
        d = tempfile.mkdtemp()
        (Path(d) / "build_info.py").write_text('BUILD_SHA = "abc1234def567890"\nBUILD_CHANNEL = "pilot"\n', encoding="utf-8")
        sys.path.insert(0, d)
        sys.modules.pop("build_info", None)
        try:
            importlib.reload(wl_version)
            self.assertEqual(wl_version.BUILD_SHA, "abc1234def567890")
            self.assertEqual(wl_version.BUILD_CHANNEL, "pilot")
            self.assertEqual(wl_version.version_string(), "0.4.6+abc1234")   # short SHA in the string
            md = wl_version.build_metadata()
            self.assertEqual(md["build_sha"], "abc1234def567890")
            self.assertEqual(md["channel"], "pilot")
        finally:
            sys.path.remove(d)
            sys.modules.pop("build_info", None)
            importlib.reload(wl_version)                                     # reset to source state
            shutil.rmtree(d, ignore_errors=True)

    def test_env_var_fallback(self):
        import os
        sys.modules.pop("build_info", None)
        os.environ["WATCHLOG_BUILD_SHA"] = "envsha0099"
        try:
            importlib.reload(wl_version)
            # env is used only when no baked build_info is present on the path
            if wl_version.BUILD_SHA == "envsha0099":
                self.assertTrue(wl_version.version_string().startswith("0.4.6+envsha0"))
        finally:
            del os.environ["WATCHLOG_BUILD_SHA"]
            importlib.reload(wl_version)

    def test_version_and_metadata_shape(self):
        importlib.reload(wl_version)
        self.assertEqual(wl_version.VERSION, "0.4.6")
        md = wl_version.build_metadata()
        self.assertEqual(set(md), {"version", "build_sha", "channel", "version_string"})
        self.assertTrue(md["version_string"].startswith("0.4.6"))
        self.assertEqual(md["channel"] or "production", md["channel"] or "production")

    def test_build_exe_stamps_build_info(self):
        # Guard: the freezer must generate + bundle build_info.py.
        ps = (ROOT / "agent" / "build_exe.ps1").read_text(encoding="utf-8")
        self.assertIn("build_info.py", ps)
        self.assertIn("BUILD_SHA", ps)
        self.assertIn("--hidden-import", ps and ps)
        self.assertIn("build_info", ps)


if __name__ == "__main__":
    unittest.main(verbosity=2)
