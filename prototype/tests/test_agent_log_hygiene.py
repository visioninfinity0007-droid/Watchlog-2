#!/usr/bin/env python3
"""Guard: the agent must never write the enrollment code or agent-key material to
the persistent agent.log. These are on the "never log" list (enrollment codes let a
third party enrol; the agent key authenticates the agent). Targets the exact leak
patterns that regressed, so a re-introduction fails fast in the offline job."""
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
SRC = (AGENT / "watchlog_agent.py").read_text(encoding="utf-8")

# Exact leak patterns that must never come back (each was previously present).
FORBIDDEN = [
    "enrolling with code {code}",            # full enrollment code -> log
    "mask(state['agent_key'])",              # partial agent key -> log (enroll path)
    "mask(state.get('agent_key'))",          # partial agent key -> log (--status path)
]

# The safe replacements that must be present (prove the fix, not just deletion).
REQUIRED = [
    "enrolling with the provided setup code",
    "agent identity stored (encrypted)",
]


def main():
    problems = []
    for bad in FORBIDDEN:
        if bad in SRC:
            problems.append(f"agent logs a secret (never-log list): {bad!r}")
    for good in REQUIRED:
        if good not in SRC:
            problems.append(f"expected safe log phrasing missing: {good!r}")
    if problems:
        raise SystemExit("agent log-hygiene contract FAILED:\n- " + "\n- ".join(problems))
    print("agent log-hygiene contract: PASS (no enrollment code or agent key written to log)")


if __name__ == "__main__":
    main()
