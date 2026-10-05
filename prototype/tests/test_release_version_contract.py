#!/usr/bin/env python3
"""Release version contract, run in CI.

One version everywhere a build reads it, and no version without its release record:

  * prototype/agent/wl_version.py VERSION is the single source (runtime, heartbeat, setup);
  * the NSIS fallback APPVERSION in watchlog.nsi equals it (the release build passes
    /DAPPVERSION from wl_version, but a direct makensis compile uses the fallback);
  * docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md names that version as the source line
    under validation and has a section for it, which states its promotion status;
  * from 5.1 on (the multi-recorder Agent) that section names the database contract it needs
    first (mr/db-contracts migrations 0146-0155) and carries the multi-recorder field
    acceptance list, and the 5.0.28 live-site section stays in the document.

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


def _section(version: str):
    doc = _read("docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md")
    return re.search(rf"^## [^\n]*\b{re.escape(version)}\b[^\n]*\n(.*?)(?=^## )", doc,
                     re.M | re.S)


def test_source_of_truth_records_the_version():
    doc = _read("docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md")
    version = re.escape(_version())
    assert re.search(rf"Current source line under validation:\s+\*\*{version}\*\*", doc)
    section = _section(_version())
    assert section, "no section for this version in the installer source of truth"
    body = section.group(1).lower()
    # An unpromoted candidate must say so; a promoted one must name its exact artifact.
    assert any(word in body for word in ("not promoted", "unpromoted", "artifact id"))


def _multi_recorder() -> bool:
    major, minor = (int(part) for part in _version().split(".")[:2])
    return (major, minor) >= (5, 1)


def test_a_multi_recorder_release_names_its_database_prerequisite():
    if not _multi_recorder():
        return
    body = _section(_version()).group(1)
    assert "mr/db-contracts" in body and "0146" in body and "0155" in body
    assert "no windows artifact has been built" in body.lower()


def test_a_multi_recorder_release_carries_the_field_acceptance_list():
    if not _multi_recorder():
        return
    body = _section(_version()).group(1)
    # MULTI_RECORDER_CONTRACT.md section 20, first and last of the fifteen proofs.
    assert "1. both recorders discovered/configured;" in body
    assert "15. report coverage remains truthful." in body


def test_a_multi_recorder_release_documents_the_deliberate_downgrade():
    if not _multi_recorder():
        return
    body = _section(_version()).group(1)
    heading = "Deliberate downgrade from 5.1.0 to 5.0.x"
    assert "docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md" in body and heading in body
    runbook = _read("docs/runbooks/WINDOWS_EXISTING_SITE_REPAIR_UPGRADE.md")
    section = runbook[runbook.index(f"## {heading}"):]
    section = section[:section.index("\n## ", 1)]
    assert "exactly one configured recorder" in section
    assert "disable every extra recorder in Manage Recorders" in section
    assert "42501" in section and "Health ledger keys" in section


def test_the_5028_live_site_section_is_kept():
    section = _section("5.0.28")
    assert section, "the 5.0.28 section left the installer source of truth"
    assert "not promoted" in section.group(1).lower()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
