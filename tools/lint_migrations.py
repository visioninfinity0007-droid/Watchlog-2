#!/usr/bin/env python3
"""
Static sanity checks on the SQL migrations (no database needed).

  * filenames are NNNN_name.sql, numbered with no gaps and no duplicates
    (a number listed in tools/reserved_migrations.json may be absent — it is
    owned by another in-flight branch, e.g. PR #36's 0040/0041 — but must not
    duplicate; every OTHER gap still fails)
  * every migration is non-empty and valid UTF-8 without a BOM
  * a heads-up (not a failure) on unguarded destructive statements

    python tools/lint_migrations.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
MIG = TOOLS.parent / "prototype" / "supabase" / "migrations"
RESERVED_FILE = TOOLS / "reserved_migrations.json"
NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
DESTRUCTIVE = re.compile(r"\b(drop\s+table|truncate|delete\s+from)\b", re.I)


def load_reserved() -> set[int]:
    """Numbers permitted to be absent (owned by another branch). Optional file."""
    if not RESERVED_FILE.exists():
        return set()
    data = json.loads(RESERVED_FILE.read_text(encoding="utf-8"))
    return {int(k) for k in data.get("reserved", {})}


def main() -> int:
    files = sorted(MIG.glob("*.sql"))
    if not files:
        print("no migrations found"); return 1
    errs, warns, nums = [], [], []
    for f in files:
        m = NAME.match(f.name)
        if not m:
            errs.append(f"{f.name}: bad name (want NNNN_snake_case.sql)"); continue
        nums.append(int(m.group(1)))
        raw = f.read_bytes()
        if not raw.strip():
            errs.append(f"{f.name}: empty")
        if raw.startswith(b"\xef\xbb\xbf"):
            errs.append(f"{f.name}: UTF-8 BOM")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            errs.append(f"{f.name}: not valid UTF-8"); continue
        for mm in DESTRUCTIVE.finditer(text):
            warns.append(f"{f.name}: destructive `{mm.group(0)}` — ensure it is intended/guarded")
    reserved = load_reserved()
    present = set(nums)
    if nums:
        missing = [n for n in range(1, max(nums) + 1)
                   if n not in present and n not in reserved]
        if missing:
            errs.append(f"migration numbering gap at {missing[0]:04d} "
                        f"(reserved={sorted(reserved) or 'none'})")
    dup = present & reserved
    if dup:
        # A reserved number present here means the other branch merged — fine,
        # but then it must be removed from the reservation to keep intent clear.
        warns.append(f"reserved migration(s) {sorted(dup)} are present — "
                     f"drop them from reserved_migrations.json")
    if len(set(nums)) != len(nums):
        errs.append("duplicate migration numbers")

    for w in warns:
        print("  WARN " + w)
    if errs:
        print("MIGRATION LINT FAILED:")
        for e in errs:
            print("  " + e)
        return 1
    print(f"migration lint: ok ({len(files)} files, 0001..{max(nums):04d})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
