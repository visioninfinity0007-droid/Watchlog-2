#!/usr/bin/env python3
"""Production acceptance matrix rules (owner-mandated, 2026-10-07).

No capability is green because code exists: a capability is SUPPORTED_FIELD_VERIFIED for a
vendor only when every gate (implemented, unit, integration, that vendor's field test, remote
observability, failure/recovery) is PASS or N_A with evidence; hardware that cannot do it is
UNSUPPORTED_BY_HARDWARE with evidence; UNKNOWN never becomes PASS; a Dahua PASS never
certifies Hikvision.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import acceptance_matrix as am  # noqa: E402

DOC = am.load()


def _row(**gates):
    base = {g: {"status": "PASS", "evidence": "x"} for g in am.GATES}
    base.update(gates)
    return {"id": "cap", "family": "f", "scope": "5.1.2", "gates": base,
            "acceptance_checks": ["Recording"]}


def test_the_committed_matrix_is_valid_and_complete():
    assert am.problems(DOC) == []
    ids = {r["id"] for r in DOC["capabilities"]}
    for required in ("tenant_isolation", "per_camera_recording_check", "storage_health",
                     "tamper_event", "video_restore_event", "agent_runtime_status",
                     "evidence_attribution", "run_acceptance_test", "remote_update_rollback"):
        assert required in ids, required


def test_pass_needs_evidence_and_na_needs_a_note():
    doc = {"capabilities": [_row(unit={"status": "PASS"})]}
    assert any("without evidence" in p for p in am.problems(doc))
    doc = {"capabilities": [_row(integration={"status": "N_A"})]}
    assert any("N_A without a note" in p for p in am.problems(doc))


def test_unknown_is_not_a_gate_value():
    doc = {"capabilities": [_row(dahua_field={"status": "UNKNOWN", "evidence": "x"})]}
    assert any("invalid status" in p for p in am.problems(doc))


def test_nothing_passes_downstream_of_unimplemented_code():
    doc = {"capabilities": [_row(implemented={"status": "FAIL", "note": "missing"})]}
    assert any("PASS while not implemented" in p for p in am.problems(doc))


def test_a_dahua_pass_never_certifies_hikvision():
    row = _row(hikvision_field={"status": "NOT_RUN"})
    assert am.verdict(row, "dahua")[0] == "SUPPORTED_FIELD_VERIFIED"
    assert am.verdict(row, "hikvision") == ("NOT_READY", ["hikvision_field"])


def test_unsupported_hardware_is_explicit():
    row = _row(hikvision_field={"status": "UNSUPPORTED_BY_HARDWARE", "evidence": "404 on endpoint"})
    assert am.verdict(row, "hikvision")[0] == "UNSUPPORTED_BY_HARDWARE"
    doc = {"capabilities": [_row(unit={"status": "UNSUPPORTED_BY_HARDWARE", "evidence": "x"})]}
    assert any("only for field gates" in p for p in am.problems(doc))


def test_recording_an_acceptance_run_never_turns_unknown_into_pass():
    doc = {"capabilities": [_row(dahua_field={"status": "NOT_RUN"})]}
    run = {"summary": {"hardware": {"vendor": "Dahua", "model": "XVR"}, "agent": {"version": "5.1.2"}},
           "checks": [{"name": "Recording", "status": "PASS"}, {"name": "Recording", "status": "UNKNOWN"}]}
    assert am.record(doc, "dahua", run, "lab") == []
    assert doc["capabilities"][0]["gates"]["dahua_field"]["status"] == "NOT_RUN"
    run["checks"] = [{"name": "Recording", "status": "PASS"}] * 8
    assert am.record(doc, "dahua", run, "lab") == ["cap: dahua_field -> PASS"]
    run["checks"].append({"name": "Recording", "status": "FAIL"})
    am.record(doc, "dahua", run, "lab")
    assert doc["capabilities"][0]["gates"]["dahua_field"]["status"] == "FAIL"
    assert doc["capabilities"][0]["gates"]["hikvision_field"]["status"] == "PASS"   # untouched


def test_the_release_gate_blocks_until_both_vendors_are_proven():
    doc = {"capabilities": [_row(hikvision_field={"status": "NOT_RUN"})]}
    assert am.release_gate(doc, "5.1.2") == ["cap [hikvision]: blocked by hikvision_field"]
    doc["capabilities"][0]["gates"]["hikvision_field"] = {"status": "PASS", "evidence": "hik site"}
    assert am.release_gate(doc, "5.1.2") == []


def test_today_nothing_is_production_ready():
    # Honest starting point (2026-10-07): no capability is field-verified on Hikvision.
    assert all(am.verdict(r, "hikvision")[0] != "SUPPORTED_FIELD_VERIFIED"
               for r in DOC["capabilities"]
               if r["gates"]["hikvision_field"]["status"] != "PASS")
    assert am.release_gate(copy.deepcopy(DOC), "5.1.2")


def test_rendered_markdown_matches_the_json():
    md = (ROOT / "docs" / "acceptance" / "PRODUCTION_ACCEPTANCE_MATRIX.md").read_text(encoding="utf-8")
    assert md.strip() == am.render(DOC).strip(), "re-run: python tools/acceptance_matrix.py render"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
