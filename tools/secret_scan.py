#!/usr/bin/env python3
"""
Fail if a real secret looks committed to the tracked tree.

Matches secret *shapes* with enough length that documentation mentioning a
prefix (e.g. the audit's "github_pat_...") does not trip it. Scans only
git-tracked files. Used by CI and by tools/release_check.py.

    python tools/secret_scan.py         # exit 1 if anything found
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (name, compiled regex). Lengths chosen so real tokens match but prose does not.
PATTERNS = [
    ("GitHub PAT (fine-grained)", re.compile(r"github_pat_[A-Za-z0-9_]{40,}")),
    ("GitHub PAT (classic)", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}")),
    ("SendGrid key", re.compile(r"SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")),
    ("private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    ("Postgres URL w/ password", re.compile(r"postgres(?:ql)?://[^:@/\s]+:[^@/\s]{6,}@")),
    ("Coolify/Sanctum token in URL", re.compile(r"x-access-token:[^@\s]{20,}@")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
]

# Suffixes that are allowed to contain placeholders / examples.
SKIP_SUFFIX = (".example",)


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return [ROOT / p for p in out.stdout.splitlines() if p.strip()]


def main() -> int:
    hits = []
    for f in tracked_files():
        if f.name.endswith(SKIP_SUFFIX) or not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for name, rx in PATTERNS:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{f.relative_to(ROOT).as_posix()}:{line}: {name}")
    if hits:
        print("SECRET SCAN FAILED — possible secrets in tracked files:")
        for h in hits:
            print("  " + h)
        return 1
    print(f"secret scan: clean ({len(tracked_files())} tracked files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
