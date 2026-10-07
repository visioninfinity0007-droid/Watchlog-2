#!/usr/bin/env python3
"""Bump the WatchLog Windows product version in one step.

    python tools/bump_version.py 5.1.1          # rewrite every build-input copy
    python tools/bump_version.py --check        # exit 1 if any copy differs from wl_version

prototype/agent/wl_version.py ``VERSION`` is the single source: the Agent, Setup UI and
analytics entrypoint import it, the PE version resources of both EXEs are generated from it
(build_exe.ps1 / build_setup_gui.ps1), and tools/build_windows_release.ps1 passes it to NSIS as
/DAPPVERSION. The only other copies are the two NSIS ``!ifndef APPVERSION`` fallbacks, which
exist so a bare ``makensis watchlog.nsi`` (the CI installer-contract job) still compiles with the
right version; Repair refuses a candidate whose version differs from its APPVERSION. This tool
rewrites all three together, so a bump is one command, and the parity test
(prototype/tests/test_release_packaging.py) fails if they ever drift.

What it deliberately does NOT write: the release record in
docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md (``Current source line under validation`` and
the version's own section with its promotion status). That is prose the release owner writes;
test_release_version_contract.py requires it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WL_VERSION = ROOT / "prototype" / "agent" / "wl_version.py"
NSIS = [ROOT / "prototype" / "installer" / "nsis" / "watchlog.nsi",
        ROOT / "prototype" / "installer" / "nsis" / "watchlog-repair.nsi"]
_VERSION = re.compile(r'^(VERSION\s*=\s*")([^"]+)(")', re.M)
_FALLBACK = re.compile(r'(!ifndef APPVERSION\s+!define APPVERSION ")([^"]+)(")')


def current() -> str:
    return _VERSION.search(WL_VERSION.read_text(encoding="utf-8")).group(2)


def copies() -> dict[str, str]:
    out = {str(WL_VERSION.relative_to(ROOT)): current()}
    for nsi in NSIS:
        m = _FALLBACK.search(nsi.read_text(encoding="utf-8-sig"))
        out[str(nsi.relative_to(ROOT))] = m.group(2) if m else "<missing>"
    return out


def _rewrite(path: Path, pattern: re.Pattern, version: str) -> None:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    new, n = pattern.subn(lambda m: m.group(1) + version + m.group(3), text, count=1)
    if n != 1:
        raise SystemExit(f"{path}: version line not found")
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    newline = "\r\n" if b"\r\n" in raw else "\n"
    path.write_bytes(bom + new.replace("\r\n", "\n").replace("\n", newline).encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", nargs="?")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check or not args.version:
        found = copies()
        for where, v in found.items():
            print(f"{v:10} {where}")
        return 0 if len(set(found.values())) == 1 else 1
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        raise SystemExit("the version must be a plain release number, e.g. 5.1.1")
    _rewrite(WL_VERSION, _VERSION, args.version)
    for nsi in NSIS:
        _rewrite(nsi, _FALLBACK, args.version)
    for where, v in copies().items():
        print(f"{v:10} {where}")
    print("Now add the release record for this version to "
          "docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md (test_release_version_contract.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
