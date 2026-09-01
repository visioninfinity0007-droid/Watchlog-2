#!/usr/bin/env python3
"""
Fail if an em dash (U+2014) appears in shippable public-site copy.

Public WatchLog site copy must not contain the em dash character. Sentences are
rewritten naturally instead (commas, colons, periods, parentheses). This guard
scans the WordPress theme templates/copy + the content-seeding script.

    python tools/check_no_emdash.py
Exit 0 = clean, 1 = found.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "deploy/wordpress/themes/watchlog",
    ROOT / "deploy/wordpress/site-content.sh",
]
EXTS = {".php", ".css", ".js", ".sh"}
EMDASH = "—"

def files():
    for t in TARGETS:
        if t.is_file():
            yield t
        elif t.is_dir():
            for p in t.rglob("*"):
                if p.suffix.lower() in EXTS:
                    yield p

def main() -> int:
    hits = []
    for f in files():
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if EMDASH in line:
                    hits.append((f, i, line.strip()[:100]))
        except Exception as e:  # noqa: BLE001
            print(f"WARN could not read {f}: {e}")
    if hits:
        print(f"FAIL: em dash (U+2014) found in {len(hits)} line(s) of public-site copy:")
        for f, i, txt in hits:
            print(f"  {f.relative_to(ROOT)}:{i}: {txt}")
        print("Rewrite the sentence naturally (commas, colons, periods, parentheses).")
        return 1
    print("OK: no em dashes in public-site copy.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
