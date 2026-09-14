#!/usr/bin/env python3
"""Release consistency gate (0.4.4 §Phase 11): Website SHA = Portal SHA = Manifest SHA = Artifact SHA.

One approved immutable build must be the single source of truth. This tool fails the release if the
website download, the portal download, the update manifest and the built artifact do not all agree
on the SAME SHA-256 (and version). It is the gate a future release must pass before promotion.

Pure comparison (unit-tested); an optional fetch layer (injected) can pull the live values.
"""
from __future__ import annotations

import argparse
import sys


def compare_shas(shas: dict) -> dict:
    """shas: {source_name: sha256_hex_or_None}. Returns {ok, canonical, mismatches, missing}.
    The most-common value is the reference; any source that differs is a mismatch."""
    from collections import Counter
    norm = {k: (v or "").strip().lower() for k, v in shas.items()}
    present = {k: v for k, v in norm.items() if v}
    missing = [k for k, v in norm.items() if not v]
    counts = Counter(present.values())
    canonical = counts.most_common(1)[0][0] if counts else None
    mismatches = sorted(k for k, v in present.items() if v != canonical)
    ok = (not missing) and len(set(present.values())) == 1
    return {"ok": ok, "canonical": canonical, "mismatches": mismatches, "missing": missing,
            "sources": norm}


def compare_versions(versions: dict) -> dict:
    """Same idea for version strings (manifest version must match the built/installer version)."""
    norm = {k: (v or "").strip() for k, v in versions.items()}
    present = {k: v for k, v in norm.items() if v}
    missing = [k for k, v in norm.items() if not v]
    values = set(present.values())
    return {"ok": (not missing) and len(values) == 1,
            "canonical": next(iter(values)) if len(values) == 1 else None,
            "missing": missing, "sources": norm}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact-sha", default="")
    ap.add_argument("--manifest-sha", default="")
    ap.add_argument("--website-sha", default="")
    ap.add_argument("--portal-sha", default="")
    ap.add_argument("--artifact-version", default="")
    ap.add_argument("--manifest-version", default="")
    args = ap.parse_args(argv)

    sha = compare_shas({"artifact": args.artifact_sha, "manifest": args.manifest_sha,
                        "website": args.website_sha, "portal": args.portal_sha})
    ver = compare_versions({"artifact": args.artifact_version, "manifest": args.manifest_version})

    for name, v in sha["sources"].items():
        print(f"  sha {name:8} = {v or '(missing)'}")
    if sha["ok"]:
        print(f"OK: all download SHAs agree ({sha['canonical']})")
    else:
        if sha["missing"]:
            print(f"FAIL: missing SHA from: {', '.join(sha['missing'])}")
        if sha["mismatches"]:
            print(f"FAIL: SHA mismatch in: {', '.join(sha['mismatches'])} (canonical {sha['canonical']})")
    if args.artifact_version or args.manifest_version:
        print(f"  version = {ver['sources']}  -> {'OK' if ver['ok'] else 'FAIL'}")

    return 0 if sha["ok"] and (ver["ok"] or not (args.artifact_version or args.manifest_version)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
