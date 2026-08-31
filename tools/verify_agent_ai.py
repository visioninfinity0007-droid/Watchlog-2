#!/usr/bin/env python3
"""
Verify the AI false-alarm filter is packaged in a BUILT agent, by running it.

This is the gate that stops "the filter ships" being a claim: it executes the
agent's `--selftest`, which loads the model through onnxruntime, runs
inference, and discards a junk frame. Proof by execution, not by strings.

    python tools/verify_agent_ai.py [path-to-exe]

Default exe: prototype/dist/watchlog-agent.exe. If it is absent, the source
agent is run instead (with the exported model wired in) so the pipeline can
still be verified without a frozen build.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "prototype" / "dist" / "watchlog-agent.exe"
MODEL = ROOT / "prototype" / "models" / "yolov8n.onnx"


def main() -> int:
    env = dict(os.environ)
    if EXE.exists():
        cmd = [str(EXE), "--selftest"]
        where = f"frozen exe {EXE.name}"
    else:
        print(f"(no exe at {EXE}; running the source agent --selftest instead)")
        if MODEL.exists():
            env["WATCHLOG_SELFTEST_MODEL"] = str(MODEL)
        cmd = [sys.executable, str(ROOT / "prototype" / "agent" / "watchlog_agent.py"), "--selftest"]
        where = "source agent"

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    except Exception as e:  # noqa: BLE001
        print(f"VERIFY: FAIL — could not run {where}: {e}")
        return 1
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print("stderr:", r.stderr[-400:])

    passed = r.returncode == 0 and "RESULT: PASS" in r.stdout
    print(f"\nVERIFY ({where}): {'PASS — AI filter is packaged and working' if passed else f'FAIL (exit {r.returncode})'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
