#!/usr/bin/env python3
"""Release version contract, run in CI.

One version everywhere a build reads it, and no version without its release record:

  * prototype/agent/wl_version.py VERSION is the single source (runtime, heartbeat, setup);
  * the NSIS fallback APPVERSION in watchlog.nsi equals it (the release build passes
    /DAPPVERSION from wl_version, but a direct makensis compile uses the fallback);
  * docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md names that version as the source line
    under validation and has a section for it, which states its promotion status.

test_release_hardening.py checks the first two as well but is not run by CI.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _version() -> str:
    return re.search(r'^VERSION\s*=\s*"([^"]+)"', _read("prototype/agent/wl_version.py"),
                     re.M).group(1)


def test_version_is_a_plain_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _version())


def test_nsis_fallback_matches_wl_version():
    nsis = _read("prototype/installer/nsis/watchlog.nsi")
    fallback = re.search(r'!ifndef APPVERSION\s+!define APPVERSION "([^"]+)"', nsis).group(1)
    assert fallback == _version()


def test_source_of_truth_records_the_version():
    doc = _read("docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md")
    version = re.escape(_version())
    assert re.search(rf"Current source line under validation:\s+\*\*{version}\*\*", doc)
    section = re.search(rf"^## [^\n]*\b{version}\b[^\n]*\n(.*?)(?=^## )", doc, re.M | re.S)
    assert section, "no section for this version in the installer source of truth"
    body = section.group(1).lower()
    # An unpromoted candidate must say so; a promoted one must name its exact artifact.
    assert any(word in body for word in ("not promoted", "unpromoted", "artifact id"))


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
