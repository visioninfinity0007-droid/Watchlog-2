#!/usr/bin/env python3
"""WatchLog production acceptance matrix: validate, record field evidence, render.

docs/acceptance/production_acceptance_matrix.json holds one row per capability with seven gates:

  implemented -> unit -> integration -> dahua_field -> hikvision_field
              -> remote_observability -> failure_recovery

Gate values: PASS (needs `evidence`), FAIL (needs `evidence` or `note`), NOT_RUN, N_A (needs
`note`: why the gate does not apply). The two field gates may also be UNSUPPORTED_BY_HARDWARE
(needs `evidence`: what the recorder answered). UNKNOWN is never a gate value: an inconclusive
result is NOT_RUN or FAIL, never PASS.

A capability's verdict per vendor is
  SUPPORTED_FIELD_VERIFIED  every gate is PASS or N_A, and that vendor's field gate is PASS;
  UNSUPPORTED_BY_HARDWARE   that vendor's field gate says so (with evidence);
  NOT_READY                 anything else (the blocking gates are listed).
The release gate passes only when every row in the release scope is SUPPORTED_FIELD_VERIFIED or
UNSUPPORTED_BY_HARDWARE for BOTH vendors. A Dahua PASS never certifies Hikvision.

  python tools/acceptance_matrix.py validate
  python tools/acceptance_matrix.py render  > docs/acceptance/PRODUCTION_ACCEPTANCE_MATRIX.md
  python tools/acceptance_matrix.py gate --scope 5.1.2           # exit 1 while not releasable
  python tools/acceptance_matrix.py record --vendor dahua --run acceptance.json --site "Al-Khalid"
      # applies a RUN_FULL_ACCEPTANCE_TEST result: PASS checks become field PASS with evidence,
      # FAIL becomes FAIL, UNSUPPORTED becomes UNSUPPORTED_BY_HARDWARE, UNKNOWN changes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "acceptance" / "production_acceptance_matrix.json"

GATES = ("implemented", "unit", "integration", "dahua_field", "hikvision_field",
         "remote_observability", "failure_recovery")
FIELD_GATES = {"dahua": "dahua_field", "hikvision": "hikvision_field"}
VALUES = {"PASS", "FAIL", "NOT_RUN", "N_A", "UNSUPPORTED_BY_HARDWARE"}


def load(path: Path = MATRIX) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(doc: dict, path: Path = MATRIX) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def problems(doc: dict) -> list[str]:
    out = []
    seen = set()
    for row in doc["capabilities"]:
        cid = row.get("id")
        if not cid or cid in seen:
            out.append(f"duplicate or missing id: {cid!r}")
        seen.add(cid)
        for gate in GATES:
            g = row.get("gates", {}).get(gate)
            if not isinstance(g, dict) or g.get("status") not in VALUES:
                out.append(f"{cid}.{gate}: missing or invalid status")
                continue
            status = g["status"]
            if status == "UNSUPPORTED_BY_HARDWARE" and gate not in FIELD_GATES.values():
                out.append(f"{cid}.{gate}: UNSUPPORTED_BY_HARDWARE is only for field gates")
            if status in ("PASS", "UNSUPPORTED_BY_HARDWARE") and not str(g.get("evidence", "")).strip():
                out.append(f"{cid}.{gate}: {status} without evidence")
            if status == "N_A" and not str(g.get("note", "")).strip():
                out.append(f"{cid}.{gate}: N_A without a note")
            if status == "FAIL" and not (g.get("evidence") or g.get("note")):
                out.append(f"{cid}.{gate}: FAIL without evidence or note")
        # Gates are ordered: nothing downstream may PASS while the code is not implemented.
        if row["gates"]["implemented"]["status"] != "PASS":
            for gate in GATES[1:]:
                if row["gates"][gate]["status"] == "PASS":
                    out.append(f"{cid}.{gate}: PASS while not implemented")
    return out


def verdict(row: dict, vendor: str) -> tuple[str, list[str]]:
    gates = row["gates"]
    field = gates[FIELD_GATES[vendor]]["status"]
    if field == "UNSUPPORTED_BY_HARDWARE":
        return "UNSUPPORTED_BY_HARDWARE", []
    other_field = FIELD_GATES["hikvision" if vendor == "dahua" else "dahua"]
    blocking = [g for g in GATES if g != other_field
                and gates[g]["status"] not in ("PASS", "N_A")]
    return ("SUPPORTED_FIELD_VERIFIED", []) if not blocking else ("NOT_READY", blocking)


def release_gate(doc: dict, scope: str) -> list[str]:
    out = []
    for row in doc["capabilities"]:
        if row.get("scope") != scope:
            continue
        for vendor in FIELD_GATES:
            v, blocking = verdict(row, vendor)
            if v == "NOT_READY":
                out.append(f"{row['id']} [{vendor}]: blocked by {', '.join(blocking)}")
    return out


def record(doc: dict, vendor: str, run: dict, site: str) -> list[str]:
    """Apply a RUN_FULL_ACCEPTANCE_TEST result to the field gate of the mapped capabilities."""
    gate = FIELD_GATES[vendor]
    hw = run.get("summary", {}).get("hardware", {}) or run.get("hardware", {})
    agent = run.get("summary", {}).get("agent", {}) or run.get("agent", {})
    tested = run.get("summary", {}).get("tested_at") or run.get("tested_at", "")
    by_check: dict[str, list[dict]] = {}
    for check in run.get("checks", []):
        by_check.setdefault(check.get("name", ""), []).append(check)
    changed = []
    for row in doc["capabilities"]:
        names = row.get("acceptance_checks") or []
        results = [c for n in names for c in by_check.get(n, [])]
        if not results:
            continue
        statuses = {c.get("status") for c in results}
        where = (f"{site}; {hw.get('vendor', vendor)} {hw.get('model', '')} fw {hw.get('firmware', '?')}; "
                 f"agent {agent.get('version', '?')} {str(agent.get('build_sha', ''))[:8]}; {tested}")
        if "FAIL" in statuses:
            new = {"status": "FAIL", "evidence": f"acceptance FAIL: {where}"}
        elif statuses == {"UNSUPPORTED"}:
            new = {"status": "UNSUPPORTED_BY_HARDWARE", "evidence": f"acceptance UNSUPPORTED: {where}"}
        elif statuses <= {"PASS", "UNSUPPORTED"} and "PASS" in statuses:
            new = {"status": "PASS", "evidence": f"acceptance PASS ({len(results)} checks): {where}"}
        else:
            continue  # any UNKNOWN: inconclusive, the gate is left as it was
        if row["gates"][gate] != new:
            row["gates"][gate] = new
            changed.append(f"{row['id']}: {gate} -> {new['status']}")
    return changed


def render(doc: dict) -> str:
    lines = ["# WatchLog production acceptance matrix", "",
             f"Generated from `docs/acceptance/production_acceptance_matrix.json` by "
             f"`tools/acceptance_matrix.py render`. Rules: see the tool docstring.", ""]
    for scope in ("5.1.2", "later", "gated"):
        rows = [r for r in doc["capabilities"] if r.get("scope") == scope]
        if not rows:
            continue
        lines += [f"## Scope {scope}", "",
                  "| family | capability | impl | unit | integ | Dahua field | Hik field | remote obs | fail/recover | Dahua verdict | Hik verdict |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            g = r["gates"]
            cells = [g[x]["status"].replace("UNSUPPORTED_BY_HARDWARE", "UNSUPPORTED") for x in GATES]
            lines.append(f"| {r['family']} | `{r['id']}` | " + " | ".join(cells) + " | "
                         + verdict(r, "dahua")[0] + " | " + verdict(r, "hikvision")[0] + " |")
        lines.append("")
    total = len(doc["capabilities"])
    ready = {v: sum(1 for r in doc["capabilities"] if verdict(r, v)[0] != "NOT_READY") for v in FIELD_GATES}
    lines += [f"**{ready['dahua']}/{total} ready on Dahua, {ready['hikvision']}/{total} ready on Hikvision.**", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("render")
    g = sub.add_parser("gate")
    g.add_argument("--scope", default="5.1.2")
    r = sub.add_parser("record")
    r.add_argument("--vendor", choices=sorted(FIELD_GATES), required=True)
    r.add_argument("--run", type=Path, required=True)
    r.add_argument("--site", required=True)
    args = ap.parse_args(argv)
    doc = load()
    issues = problems(doc)
    if args.cmd == "validate":
        print("\n".join(issues) or f"matrix ok ({len(doc['capabilities'])} capabilities)")
        return 1 if issues else 0
    if issues:
        print("matrix invalid:\n" + "\n".join(issues), file=sys.stderr)
        return 2
    if args.cmd == "render":
        print(render(doc))
        return 0
    if args.cmd == "gate":
        blocked = release_gate(doc, args.scope)
        print("\n".join(blocked) or f"release gate {args.scope}: PASS")
        print(f"{len(blocked)} blocking item(s)")
        return 1 if blocked else 0
    changed = record(doc, args.vendor, json.loads(args.run.read_text(encoding="utf-8")), args.site)
    issues = problems(doc)
    if issues:
        print("refusing to save an invalid matrix:\n" + "\n".join(issues), file=sys.stderr)
        return 2
    save(doc)
    print("\n".join(changed) or "no gate changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
