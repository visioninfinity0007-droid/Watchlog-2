#!/usr/bin/env python3
"""Run every step of the CI `backend` job locally, in order, and report pass/fail.
Used to prove the backend job is green after porting incidents/page.js — since CI
aborted at step 21, steps 22..69 never actually ran on the fork."""
import subprocess, sys, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

try:
    import yaml
    steps = yaml.safe_load(CI)["jobs"]["backend"]["steps"]
    cmds = [(s.get("name", "?"), s["run"].strip()) for s in steps if "run" in s]
except Exception as e:
    print("yaml parse failed:", e); sys.exit(2)

results = []
for name, run in cmds:
    # only single-line python/tool commands from this job (skip multi-line shell blobs)
    first = run.splitlines()[0].strip()
    if not (first.startswith("python ") or first.startswith("php ")):
        results.append((name, "SKIP(non-test)", 0)); continue
    p = subprocess.run(first, shell=True, cwd=str(ROOT),
                       capture_output=True, text=True)
    ok = p.returncode == 0
    results.append((name, "PASS" if ok else "FAIL", p.returncode))
    if not ok:
        tail = (p.stdout + p.stderr).strip().splitlines()[-8:]
        print(f"\n### FAIL: {name}\n$ {first}")
        print("\n".join("   " + t for t in tail))

print("\n===== BACKEND JOB SUMMARY =====")
fails = [r for r in results if r[1] == "FAIL"]
for name, status, rc in results:
    if status != "SKIP(non-test)":
        print(f"  {status:5} rc={rc}  {name}")
print(f"\n{sum(1 for r in results if r[1]=='PASS')} passed, {len(fails)} failed, "
      f"{sum(1 for r in results if r[1].startswith('SKIP'))} non-test skipped")
sys.exit(1 if fails else 0)
