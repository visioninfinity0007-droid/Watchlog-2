#!/usr/bin/env python3
"""Multi-recorder owner surfaces: behaviour of System Health, Site Control and the Watch AI card.

The recorder rules live in React-free modules so they can be checked in plain node:
  portal/app/site-health/recorder-impact.js    MNVR-068 recorder root cause, distinct issue count
  portal/app/site-control/recorder-control.js  MNVR-048 recorder grouping, MNVR-049 evidence labels
  portal/app/ai/recorder-card.js               MNVR-051 "Checked" only on capability_known === true
  portal/app/control-room/camera-events.js     latest camera event per camera, by camera identity
The static checks below make sure each surface really renders through those modules.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "portal" / "app"
MODULES = (
    APP / "site-health" / "recorder-impact.js",
    APP / "site-control" / "recorder-control.js",
    APP / "ai" / "recorder-card.js",
    APP / "control-room" / "camera-events.js",
)


def read(rel):
    return (APP / rel).read_text(encoding="utf-8")


def test_recorder_surface_unit_checks_pass_in_node():
    node = shutil.which("node")
    assert node, "node is required for the portal recorder-surface unit checks"
    missing = [str(p.relative_to(ROOT)) for p in MODULES if not p.exists()]
    assert not missing, f"portal recorder modules missing: {missing}"
    with tempfile.TemporaryDirectory() as tmp:
        urls = []
        for src in MODULES:
            dst = Path(tmp) / (src.parent.name + "-" + src.stem + ".mjs")
            shutil.copy(src, dst)
            urls.append(dst.as_uri())
        r = subprocess.run([node, str(ROOT / "prototype/tests/portal_recorder_surfaces_unit.mjs"), *urls],
                           capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checks passed" in r.stdout


def test_system_health_renders_through_the_recorder_rules():
    health = read("site-health/health-workspace.js")
    assert 'from "./recorder-impact"' in health
    assert "recorderImpact(" in health
    # A camera behind a failed recorder takes that recorder's advice, not "check the camera's cable".
    assert "recorderView(failed).action" in health
    # The rail shows recording confirmation again (it was lost in the multi-recorder port).
    assert '<Stat label="Recording confirmed"' in health
    assert 'stats.recording+" of "+cams.length' in health
    # The lead uses the distinct issue count, not a sum of overlapping fault and camera-state totals.
    assert "cameraFaults.length+stats.offline" not in health
    assert "impact.issueCount" in health
    # Recorder-root faults are not dropped just because the site has recorder rows.
    assert "recorderRootReasons" not in health


def test_page_leads_follow_the_recorder_impact_rules():
    # A storage issue never outranks camera faults in a lead, and "affected" is the cameras the root
    # cause explains (recorderImpact), never every camera_id behind a failing recorder.
    cameras = read("control-room/customer-workspace.js")
    health = read("site-health/health-workspace.js")
    assert re.search(r'import \{[^}]*\brecorderImpact\b[^}]*\} from "\.\./site-health/recorder-impact";', cameras)
    assert "recorderImpact({ cams: cameras, faults, recorderRows })" in cameras
    assert "} else if (impact.blockingIssues.length) {" in cameras
    assert "impact.blockingIssues.length||" in health
    for page in (cameras, health):
        assert "recorderIssueCameraIds.size" in page
        assert "(row.camera_ids || []).map(String))).size" not in page
        assert "(r.camera_ids||[]).map(String))).size" not in page
    assert '(r.camera_count===1?"":"s")+" affected"' not in health
    assert "impact.camerasAffectedBy(r)" in health


def test_recorder_state_is_not_current_while_the_site_is_disconnected():
    cameras = read("control-room/customer-workspace.js")
    health = read("site-health/health-workspace.js")
    assert "currentRecorderRows(recorderSummary?.recorders||[],online)" in health
    assert "currentRecorderRows(recorderSummary?.recorders || [], Boolean(ctx?.connectivity?.agent_online))" in cameras
    for page in (cameras, health):
        assert "recorderSummary?.recorders" in page and page.count("recorderSummary?.recorders") == 1,             "recorder rows are read once, through currentRecorderRows"


def test_a_failed_recorder_call_falls_back_to_the_context():
    cameras = read("control-room/customer-workspace.js")
    health = read("site-health/health-workspace.js")
    assert "setRecorderSummary(prev=>recorderSummaryFrom(recorders,r.data,prev))" in health
    assert "setRecorderSummary(recorderSummaryFrom(recorderResult, contextResult.data))" in cameras
    assert "recorders.error?null" not in health
    assert "!recorderResult.error ? recorderResult.data || null : null" not in cameras


def test_site_control_renders_through_the_recorder_rules():
    page = read("site-control/customer-workspace.js")
    assert 'from "./recorder-control"' in page
    assert "recorderGroups(" in page
    assert "key={cam.key}" in page
    assert '(cam.channel??i)+"-"+(cam.name||"")' not in page
    # Label logic lives in one place; the page must not keep a second copy.
    assert "function capView(" not in page and "function evidenceLabel(" not in page
    control = read("site-control/recorder-control.js")
    assert 'evidence_scope==="recorder"' in control
    # A multi-recorder site never renders one site-wide capability list.
    assert "multiRecorder" in page


def test_watch_ai_card_renders_through_the_recorder_rules():
    card = read("ai/customer-card.js")
    assert 'from "./recorder-card"' in card
    assert "recorderCardRows(" in card
    assert '"Checked"' not in card, "only recorder-card.js decides when support reads Checked"


def main():
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures.append(name)
                print(f"  FAIL  {name}: {exc}")
    if failures:
        return 1
    print("Portal recorder surfaces: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
