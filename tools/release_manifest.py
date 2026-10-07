#!/usr/bin/env python3
"""Write (and gate) the WatchLog Windows build manifest: what was built, from what, with what.

tools/build_windows_release.ps1 runs this after NSIS and before deleting its staging folder:

    python tools/release_manifest.py --out dist-installer/WatchLog-Build-Manifest.json \\
        --agent prototype/dist/watchlog-agent.exe --setup-ui prototype/dist/watchlog-setup-ui.exe \\
        --setup dist-installer/WatchLog-Setup.exe --repair dist-installer/WatchLog-Repair-Upgrade.exe \\
        --stage <NSIS staging dir> --nsis-version v3.13 [--lean] [--allow-dirty] [--signed]

It records: the runner image (ImageOS / ImageVersion when GitHub sets them), the OS, Python,
pip, PyInstaller, PySide6/Qt (the Qt6Core.dll version read from the frozen Setup UI), the NSIS
version, the full installed set of each isolated build venv (from prototype/dist/
build-env-*.json, written by prototype/packaging/build_env.ps1), the dependency lock hashes,
the YOLO model SHA-256 (source and as bundled), the FFmpeg binary SHA-256 and version (as
bundled), the source SHA and dirty-tree flag, the BUILD_SHA baked into each EXE, and SHA-256
plus bytes of every artifact and of every staged NSIS payload file.

It FAILS (exit 1) when a release invariant is broken: a baked BUILD_SHA differs from the
source SHA, a build venv did not match its lock, a dirty tree without --allow-dirty, or the
bundled YOLO model / FFmpeg differ from their sources. The manifest is still written so the
failure can be read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pyi_archive  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "prototype"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def pe_string_bytes(data: bytes, key: str) -> str | None:
    return pyi_archive.pe_version_string(data, key)


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def wl_version() -> str:
    text = (PROTO / "agent" / "wl_version.py").read_text(encoding="utf-8")
    return re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M).group(1)


def exe_info(path: Path) -> dict:
    data = path.read_bytes()
    info = {"bytes": len(data), "sha256": sha256_bytes(data),
            "product_version": pe_string_bytes(data, "ProductVersion"),
            "file_version": pe_string_bytes(data, "FileVersion")}
    try:
        exe = pyi_archive.PyInstallerExe(path)
        info["baked_build_sha"] = exe.baked_build_sha()
        info["python_library"] = exe.python_library
        info["pkg_entries"] = len(exe.entries)
    except Exception:  # noqa: BLE001  (an NSIS installer is not a PyInstaller archive)
        pass
    return info


def bundled(exe_path: Path, pattern: str) -> tuple[str, bytes] | None:
    exe = pyi_archive.PyInstallerExe(exe_path)
    for name in exe.names():
        if re.search(pattern, name.replace("\\", "/"), re.I):
            return name, exe.extract(name)
    return None


def build_envs() -> dict:
    out = {}
    for name in ("agent", "setup-ui"):
        p = PROTO / "dist" / f"build-env-{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text(encoding="utf-8"))
    return out


def pinned(env: dict, package: str) -> str | None:
    for line in env.get("freeze", []):
        n, _, v = line.partition("==")
        if n.lower() == package.lower():
            return v
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--agent", type=Path, required=True)
    ap.add_argument("--setup-ui", type=Path, required=True)
    ap.add_argument("--setup", type=Path)
    ap.add_argument("--repair", type=Path)
    ap.add_argument("--stage", type=Path, help="the NSIS staging folder (payload as compiled)")
    ap.add_argument("--nsis-version", default="")
    ap.add_argument("--source-sha", default="", help="defaults to git rev-parse HEAD")
    ap.add_argument("--lean", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--signed", action="store_true")
    args = ap.parse_args()

    failures: list[str] = []
    source_sha = args.source_sha or git("rev-parse", "HEAD")
    porcelain = git("status", "--porcelain")
    dirty = bool(porcelain)
    if dirty and not args.allow_dirty:
        failures.append("the source tree is dirty; a release is built only from a committed tree "
                        "(-AllowDirty is for developer builds)")
    github_sha = os.environ.get("GITHUB_SHA", "")
    if github_sha and source_sha and github_sha != source_sha:
        failures.append(f"GITHUB_SHA {github_sha} != checked-out HEAD {source_sha}")

    envs = build_envs()
    for name in ("agent", "setup-ui"):
        env = envs.get(name)
        if not env:
            failures.append(f"no build-env record for {name} (prototype/dist/build-env-{name}.json)")
        elif not env.get("matches_lock"):
            failures.append(f"the {name} build venv did not match its lock")

    artifacts = {"watchlog-agent.exe": exe_info(args.agent),
                 "watchlog-setup-ui.exe": exe_info(args.setup_ui)}
    for label, path in (("WatchLog-Setup.exe", args.setup), ("WatchLog-Repair-Upgrade.exe", args.repair)):
        if path:
            artifacts[label] = exe_info(path)
    version = wl_version()
    for label, info in artifacts.items():
        if info.get("product_version") != version:
            failures.append(f"{label} ProductVersion {info.get('product_version')!r} != wl_version {version!r}")
    for label in ("watchlog-agent.exe", "watchlog-setup-ui.exe"):
        baked = artifacts[label].get("baked_build_sha")
        if baked != source_sha:
            failures.append(f"{label} baked BUILD_SHA {baked!r} != source SHA {source_sha!r}")

    model = None
    model_src = PROTO / "models" / "yolov8n.onnx"
    hit = bundled(args.agent, r"(^|/)yolov8n\.onnx$")
    if hit:
        model = {"bundled_entry": hit[0], "sha256": sha256_bytes(hit[1]),
                 "source_sha256": sha256(model_src) if model_src.exists() else None}
        if model["source_sha256"] != model["sha256"]:
            failures.append("the bundled YOLO model differs from prototype/models/yolov8n.onnx")
    elif not args.lean:
        failures.append("the AI Agent build bundles no yolov8n.onnx")

    ffmpeg = None
    hit = bundled(args.agent, r"imageio_ffmpeg/binaries/ffmpeg[^/]*\.exe$")
    if hit:
        m = re.search(r"-v([0-9][^/\\]*?)\.exe$", hit[0])
        ffmpeg = {"bundled_entry": hit[0], "sha256": sha256_bytes(hit[1]), "bytes": len(hit[1]),
                  "version": m.group(1) if m else None,
                  "imageio_ffmpeg": pinned(envs.get("agent", {}), "imageio-ffmpeg")}
    elif not args.lean:
        failures.append("the Agent build bundles no FFmpeg binary (imageio-ffmpeg)")

    qt = {"pyside6": pinned(envs.get("setup-ui", {}), "PySide6")}
    hit = bundled(args.setup_ui, r"(^|/)Qt6Core\.dll$")
    if hit:
        qt["qt6core_file_version"] = pe_string_bytes(hit[1], "FileVersion")
        qt["qt6core_sha256"] = sha256_bytes(hit[1])
    try:
        qt["plugins"] = pyi_archive.qt_plugins(pyi_archive.PyInstallerExe(args.setup_ui))
    except pyi_archive.ArchiveError:
        pass

    staged = {}
    if args.stage and args.stage.is_dir():
        for p in sorted(args.stage.iterdir()):
            if p.is_file() and p.suffix.lower() != ".nsi":
                staged[p.name] = {"bytes": p.stat().st_size, "sha256": sha256(p)}
            elif p.is_file():
                staged[p.name] = {"bytes": p.stat().st_size, "sha256": sha256(p), "role": "nsis-script"}

    locks = {p.name: sha256(p) for p in sorted((PROTO / "packaging").glob("requirements-*.lock"))}
    manifest = {
        "schema": "watchlog.build_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "version": version,
        "source": {"sha": source_sha, "dirty": dirty, "allow_dirty": args.allow_dirty,
                   "dirty_paths": porcelain.splitlines()[:50] if dirty else [],
                   "github_sha": github_sha or None, "ref": os.environ.get("GITHUB_REF"),
                   "run_id": os.environ.get("GITHUB_RUN_ID"),
                   "run_number": os.environ.get("GITHUB_RUN_NUMBER")},
        "runner": {"image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"),
                   "runner_os": os.environ.get("RUNNER_OS"), "runner_arch": os.environ.get("RUNNER_ARCH"),
                   "runner_name": os.environ.get("RUNNER_NAME"), "platform": platform.platform()},
        "toolchain": {
            "python": {k: v.get("python") for k, v in envs.items()},
            "pip": {k: v.get("pip") for k, v in envs.items()},
            "pyinstaller": {k: pinned(v, "pyinstaller") for k, v in envs.items()},
            "pyinstaller_hooks_contrib": {k: pinned(v, "pyinstaller-hooks-contrib") for k, v in envs.items()},
            "nsis": args.nsis_version.strip() or None,
            "qt": qt,
        },
        "dependency_locks": locks,
        "build_envs": envs,
        "build": {"lean": args.lean, "signed": args.signed},
        "yolo_model": model,
        "ffmpeg": ffmpeg,
        "artifacts": artifacts,
        "staged_payload": staged,
        "invariants_ok": not failures,
        "failures": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"build manifest written: {args.out} (sha256 {sha256(args.out)})")
    for f in failures:
        print("RELEASE INVARIANT FAILED:", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
