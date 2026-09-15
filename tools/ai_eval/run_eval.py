#!/usr/bin/env python3
"""WatchLog AI eval/benchmark runner. Grades a local Ollama model against the WatchLog invariants
using the PRODUCTION system prompt (extracted from the edge function), scoring deterministically.
No CI dependency (needs a reachable Ollama); results feed docs/ai/TEST_REPORT.md.

    python tools/ai_eval/run_eval.py --models qwen3:4b,qwen3:8b [--endpoint http://host:11434] [--report path.md]

This decides whether a model is safe for customer-facing Instant — it is NOT auto-promoted; a human
reads the report. Cloud (:cloud) models are external-egress and out of scope for the local path.
"""
from __future__ import annotations

import argparse, json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def load_system_prompt() -> str:
    src = (ROOT / "prototype/supabase/functions/watchlog-ai/index.ts").read_text(encoding="utf-8")
    m = re.search(r"const SYSTEM_PROMPT = `(.*?)`;", src, re.S)
    if not m:
        sys.exit("could not extract SYSTEM_PROMPT from index.ts")
    return m.group(1)

def load_cases():
    try:
        import yaml
    except Exception:
        sys.exit("PyYAML required")
    return yaml.safe_load((Path(__file__).parent / "dataset.yaml").read_text(encoding="utf-8"))["cases"]

def chat(endpoint: str, model: str, system: str, context: dict, prompt: str, timeout: int):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "system", "content": "WATCHLOG_CONTEXT\n" + json.dumps(context)},
            {"role": "user", "content": prompt},
        ],
        "think": False, "format": "json", "stream": False,
        "options": {"temperature": 0.2, "num_predict": 900},
    }).encode()
    req = urllib.request.Request(endpoint.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return data.get("message", {}).get("content", ""), round((time.time() - t0) * 1000)

def score(content: str, checks: dict):
    results = {}
    try:
        obj = json.loads(content)
        answer = str(obj.get("answer", "")) if isinstance(obj, dict) else ""
        results["json_valid"] = isinstance(obj, dict) and "answer" in obj
    except Exception:
        answer, results["json_valid"] = content, False
    blob = (answer + " " + content).lower()
    if "answer_regex_any" in checks:
        results["answer_regex_any"] = any(re.search(p, blob, re.I) for p in checks["answer_regex_any"])
    if "must_not_contain" in checks:
        results["must_not_contain"] = not any(re.search(p, blob, re.I) for p in checks["must_not_contain"])
    if checks.get("json_valid") and "json_valid" not in results:
        results["json_valid"] = False
    passed = all(v for v in results.values())
    return passed, results, answer[:160]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen3:4b")
    ap.add_argument("--endpoint", default="http://185.250.37.61:11434")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--report", default=str(ROOT / "docs/ai/TEST_REPORT.md"))
    args = ap.parse_args()

    system = load_system_prompt()
    cases = load_cases()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    summary, lines = {}, []
    lines.append("# WatchLog AI eval — local model benchmark\n")
    lines.append(f"Endpoint: `{args.endpoint}` · cases: {len(cases)} · system prompt: production (index.ts)\n")

    for model in models:
        print(f"\n=== {model} ===")
        rows, lat, passes = [], [], 0
        for c in cases:
            try:
                content, ms = chat(args.endpoint, model, system, c.get("context", {}), c["prompt"], args.timeout)
                ok, res, ans = score(content, c.get("checks", {}))
            except Exception as e:
                ok, res, ans, ms = False, {"error": str(e)[:80]}, "", 0
            passes += 1 if ok else 0
            lat.append(ms)
            fails = [k for k, v in res.items() if not v]
            print(f"  {'PASS' if ok else 'FAIL':4} {c['id']:32} {ms:>6}ms" + (f"  fail={fails}" if fails else ""))
            rows.append((c["id"], ok, ms, fails, ans))
        avg = round(sum(lat) / len(lat)) if lat else 0
        summary[model] = {"passed": passes, "total": len(cases), "avg_ms": avg, "max_ms": max(lat or [0])}
        lines.append(f"\n## {model} — {passes}/{len(cases)} passed · avg {avg}ms · max {max(lat or [0])}ms\n")
        lines.append("| case | result | ms | failed checks |")
        lines.append("|---|---|---|---|")
        for cid, ok, ms, fails, _ in rows:
            lines.append(f"| {cid} | {'PASS' if ok else 'FAIL'} | {ms} | {', '.join(fails) or '-'} |")

    print("\n=== SUMMARY ===")
    for m, s in summary.items():
        print(f"  {m}: {s['passed']}/{s['total']} passed, avg {s['avg_ms']}ms, max {s['max_ms']}ms")
    rp = Path(args.report); rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nreport -> {rp}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
