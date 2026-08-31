#!/usr/bin/env python3
"""
Static sanity checks on the SQL migrations (no database needed).

  * filenames are NNNN_name.sql, numbered with no gaps and no duplicates
  * every migration is non-empty and valid UTF-8 without a BOM
  * a heads-up (not a failure) on unguarded destructive statements

    python tools/lint_migrations.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MIG = Path(__file__).resolve().parents[1] / "prototype" / "supabase" / "migrations"
NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
DESTRUCTIVE = re.compile(r"\b(drop\s+table|truncate|delete\s+from)\b", re.I)


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
    for a, b in zip(sorted(nums), range(1, len(nums) + 1)):
        if a != b:
            errs.append(f"migration numbering gap/dup near {a:04d} (expected {b:04d})"); break
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
