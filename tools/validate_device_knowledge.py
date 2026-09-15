#!/usr/bin/env python3
"""Validate the WatchLog device-knowledge registry (ai-harness/device-knowledge/**/models/*.yaml).

This is the "explicit validation path" that keeps the 47-model Dahua/Hikvision registry
normalized, source-linked and evidence-graded, and — critically — guards FIELD_VERIFIED facts:
a capability may only be graded FIELD_VERIFIED if the model carries a dated field-probe source.
That is what stops generated/datasheet data from silently overwriting hard-won field truth.

Fail-closed: any deviation is a non-zero exit (wired into CI). No network, no DB — pure files.

    python tools/validate_device_knowledge.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except Exception as e:  # pragma: no cover
    print("PyYAML is required:", e)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]
MODELS = sorted((ROOT / "ai-harness" / "device-knowledge").glob("*/models/*.yaml"))

VERDICTS = {"supported", "unsupported", "by_camera", "unknown"}
EVIDENCE = {"FIELD_VERIFIED", "OFFICIAL_DOCUMENTED", "IMPLEMENTED_UNVERIFIED", "UNSUPPORTED", "UNKNOWN"}
SAFETY = {"read", "safe_write", "high_risk", "prohibited", "na"}
# 'unknown' is a first-class honest grade: some datasheets confirm a capability exists but do not
# say whether the AI runs on the camera or the recorder. Honest-unknown beats a confident guess.
AI_LOC = {"recorder", "camera", "both", "na", "hybrid", "none", "unknown"}
REQUIRED_TOP = ("manufacturer", "model", "recorder_type", "ai_processing_location", "capabilities", "source")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A source counts as FIELD evidence only when it is dated AND its url_or_doc cites the actual
# WatchLog probe/agent artifact that produced the observation (e.g. tools/dahua_probe.ps1,
# prototype/agent/drivers/*.py). Field truth is site-specific and must be earned from a real run —
# never inferred from a datasheet. Title text is deliberately NOT trusted (too easy to fake).
FIELD_SRC_URL = re.compile(r"(prototype/agent|/drivers/|_probe\b|probe\.(ps1|py)|\.ps1\b|\.py\b)", re.I)


def is_field_source(src: dict) -> bool:
    if not isinstance(src, dict) or not DATE_RE.match(str(src.get("retrieved_at", ""))):
        return False
    return bool(FIELD_SRC_URL.search(str(src.get("url_or_doc", ""))))


def validate_model(path: Path) -> list[str]:
    errs: list[str] = []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"YAML parse error: {e}"]
    if not isinstance(doc, dict):
        return ["top-level YAML is not a mapping"]

    for k in REQUIRED_TOP:
        if k not in doc or doc[k] in (None, "", []):
            errs.append(f"missing/empty required key: {k!r}")

    if str(doc.get("ai_processing_location")) not in AI_LOC:
        errs.append(f"ai_processing_location not in {sorted(AI_LOC)}: {doc.get('ai_processing_location')!r}")

    # source[] must be dated + linked
    sources = doc.get("source") or []
    if not isinstance(sources, list) or not sources:
        errs.append("source: must be a non-empty list")
        sources = []
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            errs.append(f"source[{i}] is not a mapping"); continue
        for k in ("url_or_doc", "title", "retrieved_at"):
            if not s.get(k):
                errs.append(f"source[{i}] missing {k}")
        if s.get("retrieved_at") and not DATE_RE.match(str(s["retrieved_at"])):
            errs.append(f"source[{i}].retrieved_at not ISO YYYY-MM-DD: {s['retrieved_at']!r}")

    has_field_source = any(is_field_source(s) for s in sources if isinstance(s, dict))

    caps = doc.get("capabilities") or []
    if not isinstance(caps, list) or not caps:
        errs.append("capabilities: must be a non-empty list")
        caps = []
    seen = set()
    for c in caps:
        if not isinstance(c, dict):
            errs.append(f"capability entry is not a mapping: {c!r}"); continue
        name = c.get("capability")
        if not name:
            errs.append("capability entry missing 'capability' name"); continue
        if name in seen:
            errs.append(f"duplicate capability: {name}")
        seen.add(name)
        sup = c.get("support") or {}
        verdict, evidence = sup.get("verdict"), sup.get("evidence_class")
        if verdict not in VERDICTS:
            errs.append(f"{name}: verdict not in {sorted(VERDICTS)}: {verdict!r}")
        if evidence not in EVIDENCE:
            errs.append(f"{name}: evidence_class not in {sorted(EVIDENCE)}: {evidence!r}")
        if str(c.get("ai_location")) not in AI_LOC:
            errs.append(f"{name}: ai_location not in {sorted(AI_LOC)}: {c.get('ai_location')!r}")
        wr = c.get("write") or {}
        if "safety_class" in wr and wr.get("safety_class") not in SAFETY:
            errs.append(f"{name}: write.safety_class not in {sorted(SAFETY)}: {wr.get('safety_class')!r}")
        # honest-verdict: a 'supported' write must name a method, never 'na'
        if verdict == "supported" and wr.get("supported") is True and str(wr.get("method", "na")).strip().lower() == "na":
            errs.append(f"{name}: write.supported=true but method is 'na' (dishonest capability)")
        # THE core guard: FIELD_VERIFIED must be backed by a dated field-probe source.
        if evidence == "FIELD_VERIFIED" and not has_field_source:
            errs.append(f"{name}: FIELD_VERIFIED without a dated field-probe source (unearned field claim)")
    return errs


def main() -> int:
    if not MODELS:
        print("no model YAMLs found under ai-harness/device-knowledge/*/models/")
        return 1
    total_err = 0
    for path in MODELS:
        errs = validate_model(path)
        rel = path.relative_to(ROOT).as_posix()
        if errs:
            total_err += len(errs)
            print(f"FAIL  {rel}")
            for e in errs:
                print(f"        - {e}")
    n = len(MODELS)
    print(f"\n{'OK' if total_err == 0 else 'FAILED'} — {n} models validated, {total_err} problem(s)")
    if total_err == 0:
        print("  normalized schema OK · evidence grades in-range · sources dated+linked · "
              "FIELD_VERIFIED facts backed by field-probe sources")
    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
