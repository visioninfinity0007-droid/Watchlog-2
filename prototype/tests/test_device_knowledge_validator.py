#!/usr/bin/env python3
"""Contract: the device-knowledge registry stays normalized/source-linked/evidence-graded, and the
FIELD_VERIFIED guard is not vacuous — datasheet data can never masquerade as earned field truth.

This is the committed half of directive #3's "explicit validation path": it protects both the 47
real model YAMLs AND the validator's guard behaviour, so neither the registry nor the guard can be
weakened without CI noticing.

    python prototype/tests/test_device_knowledge_validator.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vdk", ROOT / "tools" / "validate_device_knowledge.py")
vdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vdk)

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def _errs(yaml_text: str) -> list[str]:
    fd, name = tempfile.mkstemp(suffix=".yaml")
    os.write(fd, yaml_text.encode("utf-8")); os.close(fd)
    try:
        return vdk.validate_model(Path(name))
    finally:
        try: os.unlink(name)
        except OSError: pass


BASE = """manufacturer: Hikvision
model: DS-TEST
recorder_type: NVR
ai_processing_location: recorder
capabilities:
- capability: line_crossing
  support: {{verdict: supported, evidence_class: {evidence}}}
  ai_location: recorder
  read: {{supported: true, method: ISAPI}}
  write: {{supported: false, method: na, safety_class: read}}
  verification: {{required: false}}
  notes: test
source:
- url_or_doc: {url}
  title: {title}
  retrieved_at: '2026-01-01'
"""


def run() -> int:
    # 1. The real 47-model registry validates clean.
    n_models = len(vdk.MODELS)
    problems = sum(len(vdk.validate_model(p)) for p in vdk.MODELS)
    step(n_models >= 47 and problems == 0, "real registry validates clean", f"{n_models} models, {problems} problems")

    # 2. FIELD_VERIFIED backed only by a datasheet is rejected (guard is NOT vacuous).
    e = _errs(BASE.format(evidence="FIELD_VERIFIED", url="https://hik.com/datasheet.pdf", title="Datasheet"))
    step(any("FIELD_VERIFIED without a dated field-probe source" in x for x in e),
         "FIELD_VERIFIED citing a datasheet is rejected")

    # 3. The SAME model graded OFFICIAL_DOCUMENTED is accepted (grade, not source, is the difference).
    e = _errs(BASE.format(evidence="OFFICIAL_DOCUMENTED", url="https://hik.com/datasheet.pdf", title="Datasheet"))
    step(e == [], "OFFICIAL_DOCUMENTED from a datasheet is accepted")

    # 4. FIELD_VERIFIED IS accepted when it cites the real probe/agent artifact.
    e = _errs(BASE.format(evidence="FIELD_VERIFIED",
                          url="tools/dahua_probe.ps1 + prototype/agent/drivers/dahua.py",
                          title="WatchLog field probe on the client unit"))
    step(e == [], "FIELD_VERIFIED citing the probe/agent artifact is accepted")

    # 5. Out-of-range enum values are rejected (schema normalization bites).
    e = _errs(BASE.format(evidence="MADE_UP_GRADE", url="tools/dahua_probe.ps1", title="probe"))
    step(any("evidence_class not in" in x for x in e), "invalid evidence_class is rejected")

    ok = all(STEPS) and len(STEPS) == 5
    print(("OK — " if ok else "FAIL — ") + f"{sum(STEPS)}/{len(STEPS)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
