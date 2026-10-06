#!/usr/bin/env python3
"""Forensic inventory of WatchLog release artifacts (read-only analysis).

For every downloaded release artifact directory:
  * every NSIS installer is listed and extracted with 7-Zip; each embedded file is hashed;
  * every embedded PyInstaller EXE (watchlog-agent.exe, watchlog-setup-ui.exe, and the
    release-channel watchlog-agent-<ver>.exe) is opened with PyInstaller's CArchiveReader:
    each entry's typecode and compressed/uncompressed size, plus the PYZ module list;
  * entries are grouped into components (Qt, onnxruntime, numpy, FFmpeg, OpenSSL, VC runtime...).
Writes <out>/<label>.json and a markdown summary. Never modifies the inputs.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

SEVENZIP = r"C:\Program Files\7-Zip\7z.exe"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


COMPONENT_RULES = [
    ("Qt WebEngine", r"qtwebengine|webengine"),
    ("Qt QML/Quick", r"qt6qml|qt6quick|/qml/|\\qml\\|qtquick"),
    ("Qt 3D", r"qt63d|3d(core|render|input|logic|animation|extras)"),
    ("Qt Multimedia", r"multimedia|qt6spatialaudio|avcodec|avformat|avutil|swscale|swresample"),
    ("Qt translations", r"translations[\\/].*\.qm$"),
    ("Qt plugins", r"pyside6[\\/]plugins|qt6[\\/]plugins|[\\/]plugins[\\/]"),
    ("Qt Designer/tools", r"designer|assistant|linguist|lupdate|lrelease|uic\.exe|rcc\.exe|qmllint|qmlformat|qmlls"),
    ("Qt PDF", r"qt6pdf"),
    ("Qt core libs (Core/Gui/Widgets/Network/Svg)", r"qt6(core|gui|widgets|network|svg|opengl|dbus|xml|concurrent|printsupport)"),
    ("Qt other libs", r"qt6|pyside6|shiboken6"),
    ("onnxruntime", r"onnxruntime"),
    ("ONNX model", r"\.onnx$"),
    ("numpy", r"numpy"),
    ("FFmpeg (imageio-ffmpeg)", r"imageio_ffmpeg|ffmpeg"),
    ("OpenCV", r"cv2|opencv"),
    ("Pillow", r"pil[\\/]|_imaging|pillow"),
    ("cryptography/OpenSSL", r"cryptography|libcrypto|libssl|_rust"),
    ("VC runtime", r"vcruntime|msvcp|concrt|vcomp|ucrtbase|api-ms-win"),
    ("Python runtime", r"python3\d*\.dll|^python|base_library|_socket|_ssl|_hashlib|select\.pyd|unicodedata|_ctypes|libffi|pyexpat|_bz2|_lzma|_decimal|_elementtree|_overlapped|_queue|_asyncio|_multiprocessing|_uuid|_wmi|_zoneinfo"),
    ("tzdata", r"tzdata"),
    ("certifi", r"certifi|cacert"),
    ("psutil", r"psutil"),
    ("xhtml2pdf/reportlab", r"reportlab|xhtml2pdf|pypdf|html5lib|svglib|arabic_reshaper|bidi"),
    ("PYZ (pure-Python modules)", r"^PYZ"),
]


def component(name: str) -> str:
    n = name.lower().replace("\\", "/")
    for label, rx in COMPONENT_RULES:
        if re.search(rx, n):
            return label
    return "other"


def pyz_modules(reader: CArchiveReader, pyz_name: str) -> list[tuple[str, int]]:
    try:
        data = reader.extract(pyz_name)
    except Exception:
        return []
    tmp = Path("_pyz.tmp")
    tmp.write_bytes(data)
    try:
        z = ZlibArchiveReader(str(tmp))
        out = []
        for name, entry in z.toc.items():
            # entry = (typecode, offset, length)
            out.append((name, int(entry[2]) if len(entry) > 2 else 0))
        return out
    except Exception as exc:  # noqa: BLE001
        return [(f"<pyz unreadable: {type(exc).__name__}>", 0)]
    finally:
        tmp.unlink(missing_ok=True)


def inspect_pyinstaller(exe: Path) -> dict:
    r = CArchiveReader(str(exe))
    entries = []
    pyz = None
    for name, (offset, clen, ulen, compressed, typecode) in r.toc.items():
        entries.append({"name": name, "type": typecode, "compressed": clen, "uncompressed": ulen})
        if typecode == "z":
            pyz = name
    comp = defaultdict(lambda: {"compressed": 0, "uncompressed": 0, "files": 0})
    for e in entries:
        c = component(e["name"]) if e["type"] != "z" else "PYZ (pure-Python modules)"
        comp[c]["compressed"] += e["compressed"]
        comp[c]["uncompressed"] += e["uncompressed"]
        comp[c]["files"] += 1
    mods = pyz_modules(r, pyz) if pyz else []
    top_pkgs = defaultdict(int)
    for m, ln in mods:
        top_pkgs[m.split(".")[0]] += ln
    return {
        "file": exe.name,
        "bytes": exe.stat().st_size,
        "sha256": sha256(exe),
        "entries": sorted(entries, key=lambda e: -e["compressed"]),
        "components": dict(sorted(comp.items(), key=lambda kv: -kv[1]["compressed"])),
        "pyz_module_count": len(mods),
        "pyz_top_packages": dict(sorted(top_pkgs.items(), key=lambda kv: -kv[1])[:40]),
    }


def list_nsis(installer: Path, dest: Path) -> dict:
    lst = subprocess.run([SEVENZIP, "l", "-slt", str(installer)], capture_output=True, text=True)
    files, cur = [], {}
    for line in lst.stdout.splitlines():
        if line.startswith("Path = "):
            if cur:
                files.append(cur)
            cur = {"path": line[7:]}
        elif line.startswith("Size = ") and cur:
            cur["size"] = int(line[7:] or 0)
        elif line.startswith("Packed Size = ") and cur:
            cur["packed"] = int(line[14:] or 0) if line[14:].strip() else None
    if cur:
        files.append(cur)
    files = [f for f in files if f.get("path") and f["path"] != str(installer)]
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run([SEVENZIP, "x", "-y", f"-o{dest}", str(installer)], capture_output=True, text=True, check=True)
    for f in files:
        p = dest / f["path"]
        if p.is_file():
            f["sha256"] = sha256(p)
    return {"file": installer.name, "bytes": installer.stat().st_size, "sha256": sha256(installer),
            "embedded": sorted(files, key=lambda f: -(f.get("size") or 0))}


def main() -> None:
    src, out, label = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    out.mkdir(parents=True, exist_ok=True)
    report = {"label": label, "artifacts": {}, "installers": [], "pyinstaller": []}
    for f in sorted(src.rglob("*")):
        if f.is_file():
            report["artifacts"][str(f.relative_to(src))] = {"bytes": f.stat().st_size, "sha256": sha256(f)}
    hashes = {}
    for hf in src.rglob("WatchLog-Release.hashes.txt"):
        for line in hf.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                hashes[k.strip()] = v.strip()
    report["release_manifest"] = hashes
    seen = set()
    for inst in sorted(src.rglob("*.exe")):
        if inst.name.lower().startswith("watchlog-setup") or "repair" in inst.name.lower():
            if inst.name.lower() == "watchlog-setup-ui.exe":
                continue
            dest = out / f"x-{label}-{inst.stem}"
            info = list_nsis(inst, dest)
            report["installers"].append(info)
            for emb in dest.rglob("*.exe"):
                key = (emb.name.lower(), sha256(emb))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    pi = inspect_pyinstaller(emb)
                    pi["found_in"] = inst.name
                    report["pyinstaller"].append(pi)
                except Exception as exc:  # noqa: BLE001  (NSIS helper EXEs are not PyInstaller)
                    pass
    for exe in sorted(src.rglob("watchlog-agent*.exe")):
        key = (exe.name.lower(), sha256(exe))
        if key not in seen:
            seen.add(key)
            pi = inspect_pyinstaller(exe)
            pi["found_in"] = "artifact"
            report["pyinstaller"].append(pi)
    (out / f"{label}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    lines = [f"# {label}", ""]
    for k, v in report["release_manifest"].items():
        lines.append(f"- {k} = {v}")
    for inst in report["installers"]:
        lines += ["", f"## NSIS {inst['file']}  {inst['bytes']} bytes  sha256 {inst['sha256']}", "",
                  "| embedded file | bytes | sha256 |", "|---|---:|---|"]
        for e in inst["embedded"][:25]:
            lines.append(f"| {e['path']} | {e.get('size','')} | {e.get('sha256','')} |")
    for pi in report["pyinstaller"]:
        lines += ["", f"## PyInstaller {pi['file']} (in {pi['found_in']})  {pi['bytes']} bytes  sha256 {pi['sha256']}",
                  f"PYZ modules: {pi['pyz_module_count']}", "", "| component | files | compressed | uncompressed |", "|---|---:|---:|---:|"]
        for c, v in pi["components"].items():
            lines.append(f"| {c} | {v['files']} | {v['compressed']} | {v['uncompressed']} |")
        lines += ["", "Top 30 entries:", "", "| entry | type | compressed | uncompressed |", "|---|---|---:|---:|"]
        for e in pi["entries"][:30]:
            lines.append(f"| {e['name']} | {e['type']} | {e['compressed']} | {e['uncompressed']} |")
        lines += ["", "Top PYZ packages (compressed bytes): " + ", ".join(f"{k}={v}" for k, v in list(pi["pyz_top_packages"].items())[:20])]
    (out / f"{label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:60]))


if __name__ == "__main__":
    main()
