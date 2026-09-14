#!/usr/bin/env python3
"""Post-install acceptance self-test orchestration (0.4.4 §10).

A site is only *accepted* when the WHOLE chain works on THIS machine: a local identity exists,
the cloud authenticates it, the recorder is reachable, cameras enumerate, the archive is
retrievable (so the outage-recovery promise is real), the live-event path produces events, and
the local spool is healthy. This module owns the ordered check list and the honest verdict;
``watchlog_agent.cmd_accept`` binds the real probes and the tests bind fakes.

Pure and deterministic given its check callables — no cloud, recorder, or database — so the
gating logic (hard vs soft, exception-safety, verdict) is fully unit-testable. The report is
customer-facing, so a probe that throws becomes a 'blocked' check with a generic detail; raw
exception text is never surfaced.
"""
from __future__ import annotations

from typing import Callable

VALID_STATUS = ("pass", "warn", "blocked", "skipped")


def _normalize(outcome) -> tuple[str, str]:
    """Accept either ``status`` or ``(status, detail)``; coerce unknown statuses to 'blocked'."""
    if isinstance(outcome, (tuple, list)):
        status = str(outcome[0]) if outcome else "blocked"
        detail = str(outcome[1]) if len(outcome) > 1 and outcome[1] is not None else ""
    else:
        status, detail = str(outcome), ""
    if status not in VALID_STATUS:
        status = "blocked"
    return status, detail


def run_checks(checks: list[dict], *, log: Callable[[str], None] | None = None) -> dict:
    """Execute an ordered list of acceptance checks and assemble an honest report.

    Each check is ``{key, label, hard: bool, run: callable() -> status | (status, detail)}``.
    Checks run IN ORDER (later checks may depend on earlier ones, e.g. cameras after the
    recorder), and a check whose ``run()`` raises is recorded as 'blocked' — never crashing the
    harness — with a generic detail. A missing ``run`` is 'skipped'.

    A HARD check that is not 'pass' means the site is NOT accepted. Soft checks may 'warn'
    without blocking acceptance (a genuinely quiet site with no live events yet, or a recorder
    whose archive is empty/unsupported — live monitoring is still accepted, the gap is flagged).
    """
    log = log or (lambda _message: None)
    results: list[dict] = []
    for chk in checks:
        key = chk.get("key")
        label = chk.get("label") or key
        hard = bool(chk.get("hard"))
        run = chk.get("run")
        if run is None:
            status, detail = "skipped", "not applicable"
        else:
            try:
                status, detail = _normalize(run())
            except Exception:  # noqa: BLE001 — a probe failure is a blocked check, not a crash
                status, detail = "blocked", "check could not run on this site"
        results.append({"key": key, "label": label, "hard": hard,
                        "status": status, "detail": detail})
        log(f"  [{status.upper():>7}] {label}" + (f" — {detail}" if detail else ""))

    hard_failures = [r for r in results if r["hard"] and r["status"] != "pass"]
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "pass"),
        "warned": sum(1 for r in results if r["status"] == "warn"),
        "blocked": sum(1 for r in results if r["status"] == "blocked"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "hard_failures": len(hard_failures),
    }
    return {"ready": not hard_failures, "checks": results, "summary": summary}


def map_archive_status(proof_status: str) -> tuple[str, bool]:
    """Map a dahua_archive.prove_recorder_archive status to an acceptance (status, is_pass).

    Archive is a SOFT acceptance dimension: a site with only live monitoring is still accepted,
    but the recovery capability is honestly flagged.
      verified    -> pass  (recovery can backfill)
      empty       -> warn  (reachable, but nothing recorded recently — check recording is on)
      unsupported -> warn  (this recorder has no searchable archive — recovery unavailable)
      unknown/*   -> warn  (could not confirm now)
    """
    return ("pass", True) if proof_status == "verified" else ("warn", False)


__all__ = ["run_checks", "map_archive_status", "VALID_STATUS"]
