#!/usr/bin/env python3
"""Deterministic NSIS payload proof for WatchLog-Setup.exe and WatchLog-Repair-Upgrade.exe.

Replaces the size heuristics ("Repair >= Agent", "within 1 MB of Setup"), which rejected a
correct 5.0.28 Repair (LZMA made it 49,004 B smaller than the Agent it carries) and could
never tell which Agent was inside. For each installer this:

  1. lists and extracts the installer with 7-Zip (7-Zip returns the exact stored bytes: all five
     inventoried releases B69, B75, #118, #119, #134 matched their own hash manifests,
     BUILD69-FORENSIC-REPORT.md section 8);
  2. derives the expected file list from the installer's own .nsi ``File`` commands and fails on
     a missing file, an unexpected file, or any executable that is not one of the WatchLog EXEs
     (NSIS's own $PLUGINSDIR helpers and the generated uninstall.exe are the only extras);
  3. hashes every embedded file and requires watchlog-agent.exe / watchlog-setup-ui.exe to equal
     the pre-NSIS dist hashes, the scripts/readme/icon to equal their source files, and the
     public defaults to equal the staged file the release build recorded;
  4. checks the extracted Agent's and Setup UI's PE ProductVersion, the BUILD_SHA baked into
     both (read from the PyInstaller archive, no execution), and the Agent's runtime
     ``--version`` (line 1 = version, ``build_sha=`` line = source commit).

    python tools/verify_installer_payload.py --setup dist-installer/WatchLog-Setup.exe \\
        --repair dist-installer/WatchLog-Repair-Upgrade.exe \\
        --agent prototype/dist/watchlog-agent.exe --setup-ui prototype/dist/watchlog-setup-ui.exe \\
        --defaults-sha256 <hash from the build manifest> --expected-sha <source commit> \\
        --json dist-installer/WatchLog-Payload-Proof.json

7-Zip is found via --sevenzip, %SEVENZIP%, PATH, or C:\\Program Files\\7-Zip\\7z.exe (present on
GitHub's Windows images). Without it the tool fails: there is no fallback that proves less.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pyi_archive  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "prototype" / "installer"
NSI = {"setup": INSTALLER / "nsis" / "watchlog.nsi", "repair": INSTALLER / "nsis" / "watchlog-repair.nsi"}
WATCHLOG_EXES = {"watchlog-agent.exe", "watchlog-setup-ui.exe"}
# Staged file name -> the repository file it is copied from by tools/build_windows_release.ps1.
SOURCE_FILES = {
    "run-agent.ps1": INSTALLER / "run-agent.ps1",
    "register-service.ps1": INSTALLER / "register-service.ps1",
    "apply-remote-update.ps1": INSTALLER / "apply-remote-update.ps1",
    "READ ME FIRST.txt": INSTALLER / "READ ME FIRST.txt",
    "setup.ico": INSTALLER / "setup.ico",
    "wl-upgrade.ps1": INSTALLER / "nsis" / "wl-upgrade.ps1",
    "wl-repair-upgrade.ps1": INSTALLER / "wl-repair-upgrade.ps1",
}
GENERATED = {"watchlog.defaults.ini"}   # staged by the release build; hash from the manifest
NSIS_INTERNAL = re.compile(r"(?i)^\$PLUGINSDIR[\\/][^\\/]+\.(dll|bmp)$")
SEVENZIP_CANDIDATES = [r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"]


class ProofError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


# --- .nsi parsing ------------------------------------------------------------------------------
_FILE_CMD = re.compile(r'^\s*File\s+(.*)$', re.I)
_TOKEN = re.compile(r'"([^"]*)"|(\S+)')


def nsi_payload(nsi: Path) -> list[dict]:
    """Every ``File`` command of an .nsi: [{target, source}] (target = installed file name)."""
    out = []
    for raw in nsi.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        m = _FILE_CMD.match(line)
        if not m:
            continue
        tokens = [a or b for a, b in _TOKEN.findall(m.group(1))]
        oname, sources = None, []
        for tok in tokens:
            low = tok.lower()
            if low.startswith("/oname="):
                oname = tok[len("/oname="):]
            elif low in ("/nonfatal", "/a"):
                continue
            elif low.startswith("/"):
                raise ProofError(f"{nsi.name}: unsupported File option {tok!r} in: {line}")
            else:
                sources.append(tok)
        if len(sources) != 1:
            raise ProofError(f"{nsi.name}: expected exactly one source in: {line}")
        src = sources[0]
        target = oname or src
        out.append({"target": target, "target_name": re.split(r"[\\/]", target)[-1],
                    "source": re.split(r"[\\/]", src)[-1]})
    if not out:
        raise ProofError(f"{nsi.name}: no File commands found")
    return out


def writes_uninstaller(nsi: Path) -> bool:
    return any(re.match(r"^\s*WriteUninstaller\b", l, re.I)
               for l in nsi.read_text(encoding="utf-8-sig").splitlines())


# --- 7-Zip -------------------------------------------------------------------------------------
def find_7z(explicit: str | None = None) -> str:
    for cand in (explicit, os.environ.get("SEVENZIP"), shutil.which("7z"), shutil.which("7z.exe"),
                 *SEVENZIP_CANDIDATES):
        if cand and Path(cand).is_file():
            return str(cand)
    raise ProofError("7-Zip (7z.exe) not found: install it, put it on PATH, or pass --sevenzip. "
                     "The payload proof cannot run without it (GitHub windows runners ship "
                     r"C:\Program Files\7-Zip\7z.exe).")


def list_7z(sevenzip: str, installer: Path) -> list[str]:
    p = subprocess.run([sevenzip, "l", "-slt", str(installer)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise ProofError(f"7z could not list {installer.name}: {p.stderr.strip() or p.stdout[-400:]}")
    paths, in_body = [], False
    for line in p.stdout.splitlines():
        if line.startswith("----------"):
            in_body = True
            continue
        if in_body and line.startswith("Path = "):
            paths.append(line[len("Path = "):])
    return paths


def extract_7z(sevenzip: str, installer: Path, dest: Path) -> None:
    p = subprocess.run([sevenzip, "x", "-y", f"-o{dest}", str(installer)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise ProofError(f"7z could not extract {installer.name}: {p.stderr.strip() or p.stdout[-400:]}")


# --- PE version --------------------------------------------------------------------------------
def pe_string(path: Path, key: str) -> str | None:
    """A VS_VERSIONINFO StringFileInfo value (e.g. ProductVersion), read without pywin32."""
    return pyi_archive.pe_version_string(path.read_bytes(), key)


def runtime_version(agent: Path) -> list[str]:
    p = subprocess.run([str(agent), "--version"], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise ProofError(f"{agent.name} --version exited {p.returncode}: {p.stderr.strip()[:300]}")
    return [l.strip() for l in p.stdout.splitlines() if l.strip()]


# --- the proof ---------------------------------------------------------------------------------
def prove(kind: str, installer: Path, *, sevenzip: str, nsi: Path, expected: dict[str, str],
          version: str, expected_sha: str | None, run_agent: bool, workdir: Path) -> dict:
    """expected: staged/target file name -> required SHA-256 (upper hex)."""
    problems: list[str] = []
    payload = nsi_payload(nsi)
    want = Counter(item["target_name"].lower() for item in payload)
    allow_uninstaller = writes_uninstaller(nsi)
    listed = list_7z(sevenzip, installer)
    dest = workdir / kind
    extract_7z(sevenzip, installer, dest)
    files = {}
    for rel in listed:
        p = dest / rel
        if p.is_file():
            files[rel] = p
    got: Counter = Counter()
    embedded = []
    for rel, p in sorted(files.items()):
        name = re.split(r"[\\/]", rel)[-1]
        digest = sha256(p)
        entry = {"path": rel, "bytes": p.stat().st_size, "sha256": digest}
        embedded.append(entry)
        if NSIS_INTERNAL.match(rel):
            entry["role"] = "nsis-internal"
            continue
        if allow_uninstaller and name.lower() == "uninstall.exe":
            entry["role"] = "generated-uninstaller"
            continue
        got[name.lower()] += 1
        entry["role"] = "payload"
        if name.lower().endswith(".exe") and name.lower() not in WATCHLOG_EXES:
            problems.append(f"unexpected executable in {installer.name}: {rel}")
        source = next((it["source"] for it in payload if it["target_name"].lower() == name.lower()), None)
        need = expected.get(source or name) or expected.get(name)
        if need is None:
            problems.append(f"no expected hash for {rel} (source {source})")
        elif digest != need.upper():
            problems.append(f"{rel}: SHA-256 {digest} != expected {need.upper()} (source {source})")
        else:
            entry["matches"] = source
    for name, count in (want - got).items():
        problems.append(f"missing from {installer.name}: {name} (x{count}) named by {nsi.name}")
    for name, count in (got - want).items():
        problems.append(f"unexpected file in {installer.name}: {name} (x{count}), not named by {nsi.name}")
    for exe_name in WATCHLOG_EXES:
        if want.get(exe_name) and not got.get(exe_name):
            problems.append(f"{installer.name} carries no {exe_name}")

    identity = {}
    for rel, p in files.items():
        name = re.split(r"[\\/]", rel)[-1].lower()
        if name not in WATCHLOG_EXES or name in identity:
            continue
        info = {"product_version": pe_string(p, "ProductVersion"),
                "file_version": pe_string(p, "FileVersion")}
        try:
            info["baked_build_sha"] = pyi_archive.PyInstallerExe(p).baked_build_sha()
        except pyi_archive.ArchiveError as exc:
            info["baked_build_sha"] = None
            problems.append(f"{rel}: not a readable PyInstaller archive ({exc})")
        if info["product_version"] != version:
            problems.append(f"{rel}: PE ProductVersion {info['product_version']!r} != {version!r}")
        if expected_sha and info["baked_build_sha"] != expected_sha:
            problems.append(f"{rel}: baked BUILD_SHA {info['baked_build_sha']!r} != source {expected_sha!r}")
        if name == "watchlog-agent.exe" and run_agent:
            try:
                lines = runtime_version(p)
                info["runtime_version"] = lines
                if not lines or lines[0] != version:
                    problems.append(f"{rel}: --version line 1 {lines[:1]} != {version!r}")
                if expected_sha and f"build_sha={expected_sha}" not in lines:
                    problems.append(f"{rel}: --version does not report build_sha={expected_sha}")
                # Field Build 69 Hikvision safety (30 s bounded alert stream) must be in THIS binary.
                if "hikvision_stream_slice_seconds=30" not in lines:
                    problems.append(f"{rel}: --version does not report the 30 s Hikvision stream slice")
            except (OSError, subprocess.SubprocessError, ProofError) as exc:
                problems.append(f"{rel}: --version could not be run ({exc})")
        identity[name] = info
    return {"installer": installer.name, "bytes": installer.stat().st_size, "sha256": sha256(installer),
            "nsi": nsi.name, "expected_files": sorted(want.elements()), "embedded": embedded,
            "identity": identity, "problems": problems, "ok": not problems}


def expected_hashes(agent: Path, setup_ui: Path, defaults_sha256: str | None,
                    defaults_file: Path | None) -> dict[str, str]:
    out = {"watchlog-agent.exe": sha256(agent), "watchlog-setup-ui.exe": sha256(setup_ui)}
    for name, path in SOURCE_FILES.items():
        out[name] = sha256(path)
    if defaults_file:
        out["watchlog.defaults.ini"] = sha256(defaults_file)
    elif defaults_sha256:
        out["watchlog.defaults.ini"] = defaults_sha256.upper()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setup", type=Path)
    ap.add_argument("--repair", type=Path)
    ap.add_argument("--agent", type=Path, required=True, help="pre-NSIS dist watchlog-agent.exe")
    ap.add_argument("--setup-ui", type=Path, required=True, help="pre-NSIS dist watchlog-setup-ui.exe")
    ap.add_argument("--defaults-sha256", help="SHA-256 of the staged watchlog.defaults.ini (build manifest)")
    ap.add_argument("--defaults-file", type=Path)
    ap.add_argument("--manifest", type=Path, help="read --defaults-sha256 / --expected-sha / version from the build manifest")
    ap.add_argument("--expected-version")
    ap.add_argument("--expected-sha")
    ap.add_argument("--sevenzip")
    ap.add_argument("--no-run", action="store_true", help="do not execute the extracted Agent --version")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    version, sha, defaults_sha = args.expected_version, args.expected_sha, args.defaults_sha256
    if args.manifest:
        m = json.loads(args.manifest.read_text(encoding="utf-8"))
        version = version or m.get("version")
        sha = sha or m.get("source", {}).get("sha")
        defaults_sha = defaults_sha or m.get("staged_payload", {}).get("watchlog.defaults.ini", {}).get("sha256")
    if not version:
        version = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                            (ROOT / "prototype/agent/wl_version.py").read_text(encoding="utf-8"), re.M).group(1)
    try:
        sevenzip = find_7z(args.sevenzip)
        expected = expected_hashes(args.agent, args.setup_ui, defaults_sha, args.defaults_file)
        if "watchlog.defaults.ini" not in expected:
            raise ProofError("no expected hash for watchlog.defaults.ini: pass --manifest, --defaults-sha256 or --defaults-file")
        results = []
        with tempfile.TemporaryDirectory(prefix="wl-payload-") as tmp:
            for kind, path in (("setup", args.setup), ("repair", args.repair)):
                if path:
                    results.append(prove(kind, path, sevenzip=sevenzip, nsi=NSI[kind], expected=expected,
                                         version=version, expected_sha=sha, run_agent=not args.no_run,
                                         workdir=Path(tmp)))
    except ProofError as exc:
        print(f"PAYLOAD PROOF FAILED: {exc}")
        return 2
    report = {"schema": "watchlog.payload_proof.v1", "version": version, "source_sha": sha,
              "expected": expected, "installers": results, "ok": all(r["ok"] for r in results)}
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for r in results:
        print(f"{r['installer']}: {r['bytes']:,} bytes, sha256 {r['sha256']}")
        for e in r["embedded"]:
            print(f"  {e['role']:22} {e['sha256']}  {e['bytes']:>12,}  {e['path']}")
        for name, info in r["identity"].items():
            print(f"  {name}: ProductVersion={info.get('product_version')} "
                  f"BUILD_SHA={info.get('baked_build_sha')} --version={info.get('runtime_version')}")
        for p in r["problems"]:
            print("  FAIL:", p)
    print("NSIS payload proof PASSED" if report["ok"] else "NSIS payload proof FAILED")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
