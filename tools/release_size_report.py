#!/usr/bin/env python3
"""Per-component size report of the frozen WatchLog EXEs, plus the Setup UI content check.

    # after a release build (windows-release.yml runs this):
    python tools/release_size_report.py --agent prototype/dist/watchlog-agent.exe \\
        --setup-ui prototype/dist/watchlog-setup-ui.exe \\
        --setup dist-installer/WatchLog-Setup.exe --repair dist-installer/WatchLog-Repair-Upgrade.exe \\
        --json dist-installer/WatchLog-Size-Report.json

    # content check of a frozen Setup UI (build_setup_gui.ps1 runs this after every build):
    python tools/release_size_report.py --check-setup-ui prototype/dist/watchlog-setup-ui.exe

    # refresh the committed baseline after a reviewed release:
    python tools/release_size_report.py --agent ... --setup-ui ... --write-baseline

SIZE IS NEVER INTEGRITY PROOF. Identity is proven by hashes (tools/verify_installer_payload.py
and the release manifest). The size gate here only rejects gross failures: a stub, a missing
runtime, or a packaging regression such as the 206 MB of Qt Addons `--collect-all PySide6`
pulled in on runner image 20260925.250.1. Bounds are deliberately wide; the largest component
deltas against the committed baseline are printed for a human to read.

The Setup UI content check is a correctness gate, not a size gate: it reads the frozen archive's
table of contents and fails if the Agent runtime or its AI/FFmpeg stack is inside, if a Qt
Addon family (WebEngine, QML/Quick-only modules, 3D, Multimedia, Designer...) is inside, if the
Qt plugin set differs from prototype/packaging/setup-ui-qt-inventory.json, or if no BUILD_SHA
is baked in.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pyi_archive  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "prototype" / "packaging" / "size-baseline.json"
QT_INVENTORY = ROOT / "prototype" / "packaging" / "setup-ui-qt-inventory.json"
MB = 1024 * 1024

# Gross sanity bounds in bytes (inclusive). See docs/release/WINDOWS_PACKAGING.md for why.
BOUNDS = {
    "agent": (70 * MB, 140 * MB),          # 5.1.0 AI build: 88.6 MiB (92,905,793 B)
    "agent-lean": (8 * MB, 70 * MB),       # lean diagnostic build (no AI stack)
    "setup-ui": (30 * MB, 180 * MB),       # selective Qt, no Agent runtime; Build 69 was 51 MiB
    "setup": (100 * MB, 330 * MB),         # NSIS: Agent + Setup UI + scripts
    "repair": (100 * MB, 330 * MB),        # NSIS: Agent + Setup UI (Manage Recorders) + scripts
}

# --- Setup UI content rules -------------------------------------------------------------------
FORBIDDEN_PYZ_TOP = {
    "watchlog_agent", "analytics_agent", "analytics", "analytics_setup", "release_agent",
    "vision", "recovery", "recovery_ai", "backfill", "archive_runtime", "updater",
    "remote_update", "multi_recorder_fanout", "native_event_collector", "periodic_stills",
    "incident_evidence", "numpy", "onnxruntime", "PIL", "imageio_ffmpeg", "cv2", "cryptography",
    "ultralytics", "torch",
}
FORBIDDEN_ENTRY = re.compile(
    r"(?i)(\.onnx$|^onnxruntime[\\/]|^numpy[\\/]|^numpy\.libs[\\/]|^pil[\\/]|^imageio_ffmpeg[\\/]"
    r"|ffmpeg[^\\/]*\.exe$|^cryptography[\\/]|^cv2[\\/])")
# Qt Addon families the Setup UI never imports (WebEngine, QML/Quick-only modules, 3D,
# Multimedia, Designer and the other Addons). Qt6Qml/Qt6Quick/Qt6QmlModels/... themselves are
# allowed by the inventory below only because the virtual-keyboard input plugin links them, as
# in every field-proven Setup UI (Build 69, 5.0.26).
FORBIDDEN_QT = re.compile(
    r"(?i)(^pyside6[\\/]qml[\\/]|qt6webengine|qtwebengine|qt6webview|qt6webchannel|qt63d|qt3d"
    r"|qt6quick3d|qt6quickcontrols2|qt6quicktemplates2|qt6quickdialogs|qt6quicklayouts"
    r"|qt6quickshapes|qt6quickparticles|qt6quickwidgets|qt6multimedia|qtmultimedia"
    r"|qt6spatialaudio|qt6designer|qtdesigner|qt6help|qt6uitools|qt6charts|qt6datavisualization"
    r"|qt6graphs|qt6location|qt6positioning|qt6bluetooth|qt6nfc|qt6sensors|qt6serialport"
    r"|qt6texttospeech|qt6lottie|qt6labs|qt6httpserver|qt6remoteobjects|qt6scxml|qt6sql"
    r"|qt6shadertools|qt6canvaspainter|[\\/]plugins[\\/](multimedia|sqldrivers|designer"
    r"|qmltooling|qmllint|renderers|sceneparsers|assetimporters|geoservices|position"
    r"|texttospeech|webview|sensors|canbus|scenegraph)[\\/]|assistant\.exe|designer\.exe"
    r"|linguist\.exe)")
REQUIRED_PLUGINS = {
    "platforms": {"qwindows.dll", "qoffscreen.dll"},   # the desktop and the CI offscreen run
    "styles": {"qmodernwindowsstyle.dll"},
    "imageformats": {"qico.dll"},                      # setup.ico window icon
    "iconengines": {"qsvgicon.dll"},
    "tls": {"qschannelbackend.dll"},
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_setup_ui(exe_path: Path, inventory_path: Path = QT_INVENTORY) -> list[str]:
    """Problems with a frozen Setup UI's content (empty list = clean)."""
    exe = pyi_archive.PyInstallerExe(exe_path)
    problems: list[str] = []
    tops = {m.split(".")[0] for m in exe.pyz_modules()}
    for bad in sorted(tops & FORBIDDEN_PYZ_TOP):
        problems.append(f"Agent runtime / heavy module bundled: {bad}")
    for name in exe.names():
        if FORBIDDEN_ENTRY.search(name):
            problems.append(f"Agent-only file bundled: {name}")
        if FORBIDDEN_QT.search(name):
            problems.append(f"Qt Addon file bundled: {name}")
    plugins = pyi_archive.qt_plugins(exe)
    for family, files in REQUIRED_PLUGINS.items():
        missing = sorted(files - set(plugins.get(family, [])))
        if missing:
            problems.append(f"required Qt plugin missing: {family}/{', '.join(missing)}")
    if inventory_path.exists():
        expected = _load_json(inventory_path)
        exp_plugins = {k: sorted(v) for k, v in expected["plugins"].items()}
        if plugins != exp_plugins:
            got = {f"{k}/{f}" for k, v in plugins.items() for f in v}
            want = {f"{k}/{f}" for k, v in exp_plugins.items() for f in v}
            if got - want:
                problems.append(f"Qt plugins not in the committed inventory: {sorted(got - want)}")
            if want - got:
                problems.append(f"Qt plugins of the committed inventory missing: {sorted(want - got)}")
        libs = set(pyi_archive.qt_libraries(exe))
        allowed = set(expected["libraries"])
        if libs - allowed:
            problems.append(f"Qt libraries not in the committed inventory: {sorted(libs - allowed)}")
        for need in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd"):
            if need not in libs:
                problems.append(f"Qt library missing: {need}")
    if not exe.baked_build_sha():
        problems.append("no BUILD_SHA baked into the Setup UI (build_info module absent or empty)")
    return problems


# --- size report ------------------------------------------------------------------------------
def describe(exe_path: Path) -> dict:
    exe = pyi_archive.PyInstallerExe(exe_path)
    return {"bytes": exe.bytes, "sha256": exe.sha256(),
            "components": {k: v["compressed"] for k, v in pyi_archive.components(exe).items()},
            "baked_build_sha": exe.baked_build_sha()}


def check_bounds(kind: str, size: int) -> str | None:
    lo, hi = BOUNDS[kind]
    if not lo <= size <= hi:
        return (f"{kind}: {size:,} bytes is outside the sanity bounds "
                f"{lo // MB}-{hi // MB} MiB (size is not integrity; this catches stubs and bloat)")
    return None


def deltas(current: dict, baseline: dict, top: int) -> list[tuple[str, int, int, int]]:
    rows = []
    for comp in sorted(set(current) | set(baseline)):
        now, was = current.get(comp, 0), baseline.get(comp, 0)
        if now != was:
            rows.append((comp, was, now, now - was))
    return sorted(rows, key=lambda r: -abs(r[3]))[:top]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", type=Path)
    ap.add_argument("--lean", action="store_true", help="the Agent is a lean (no AI) build")
    ap.add_argument("--setup-ui", type=Path)
    ap.add_argument("--setup", type=Path)
    ap.add_argument("--repair", type=Path)
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    ap.add_argument("--json", type=Path, help="write the report as JSON")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--check-setup-ui", type=Path, metavar="EXE")
    args = ap.parse_args()

    if args.check_setup_ui:
        problems = check_setup_ui(args.check_setup_ui)
        exe = pyi_archive.PyInstallerExe(args.check_setup_ui)
        plugins = pyi_archive.qt_plugins(exe)
        print(f"Setup UI {args.check_setup_ui.name}: {exe.bytes:,} bytes, "
              f"{sum(len(v) for v in plugins.values())} Qt plugin files, BUILD_SHA {exe.baked_build_sha()}")
        for family, files in plugins.items():
            print(f"  plugins/{family}: {', '.join(files)}")
        for p in problems:
            print("FAIL:", p)
        if not problems:
            print("Setup UI content check PASSED: no Agent runtime, no AI/FFmpeg stack, no Qt Addons.")
        return 1 if problems else 0

    report: dict = {"schema": "watchlog.size_report.v1", "artifacts": {}}
    failures: list[str] = []
    baseline = _load_json(args.baseline) if args.baseline.exists() else {}
    for kind, path in (("agent", args.agent), ("setup-ui", args.setup_ui)):
        if not path:
            continue
        info = describe(path)
        report["artifacts"][kind] = info
        bound_kind = "agent-lean" if (kind == "agent" and args.lean) else kind
        if (err := check_bounds(bound_kind, info["bytes"])):
            failures.append(err)
        base = (baseline.get(kind) or {})
        print(f"\n{path.name}: {info['bytes']:,} bytes ({info['bytes'] / MB:.1f} MiB)"
              + (f", baseline {base.get('bytes', 0):,} ({base.get('label', '')})" if base else ""))
        print(f"  {'component':46} {'compressed':>14}")
        for comp, size in info["components"].items():
            print(f"  {comp:46} {size:>14,}")
        if base:
            print(f"  largest deltas vs baseline {base.get('label', '')}:")
            for comp, was, now, d in deltas(info["components"], base.get("components", {}), args.top):
                print(f"    {comp:44} {was:>13,} -> {now:>13,}  ({d:+,})")
    for kind, path in (("setup", args.setup), ("repair", args.repair)):
        if not path:
            continue
        size = path.stat().st_size
        report["artifacts"][kind] = {"bytes": size}
        print(f"\n{path.name}: {size:,} bytes ({size / MB:.1f} MiB)")
        if (err := check_bounds(kind, size)):
            failures.append(err)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.write_baseline:
        new = {k: {"label": "refreshed", "bytes": v["bytes"], "components": v["components"]}
               for k, v in report["artifacts"].items() if "components" in v}
        args.baseline.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written: {args.baseline}")
    for f in failures:
        print("FAIL:", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
