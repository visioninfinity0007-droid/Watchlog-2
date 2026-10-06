#!/usr/bin/env python3
"""Windows packaging / reproducibility contract for the 5.1.x release line.

Covers docs/release/WINDOWS_PACKAGING.md end to end, without building anything:

  R1  hash-locked dependency sets for the Agent and the Setup UI, pinned to what the passing
      5.1.0 build bundled, installed only from the locks (--require-hashes --no-deps), exact
      installed set verified, Python 3.12.10;
  R2  separate, freshly created build venvs per EXE;
  R3  selective Qt: no ``--collect-all PySide6``, an explicit Qt plugin inventory and Addon ban,
      checked against a synthesized frozen archive;
  R4  size report bounds and deltas (size is never integrity);
  R5  the NSIS payload proof: the expected file list derived from the .nsi files, hash
      comparison, stray/missing/unexpected files and executables rejected;
  V1/V2 one version source, one-command bump, Agent ``--version`` keeps line 1 bare and adds the
      build SHA, both EXEs bake BUILD_SHA, the release build gates on it;
  8.  the committed field update bootstrap (prototype/update/production.json).
"""
from __future__ import annotations

import json
import marshal
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "prototype" / "agent"
PACKAGING = ROOT / "prototype" / "packaging"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import bump_version  # noqa: E402
import lock_build_deps as locks  # noqa: E402
import pyi_archive  # noqa: E402
import release_manifest  # noqa: E402
import release_size_report as sizes  # noqa: E402
import verify_installer_payload as payload  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _version() -> str:
    return re.search(r'^VERSION\s*=\s*"([^"]+)"', _read("prototype/agent/wl_version.py"), re.M).group(1)


# --- a synthesized PyInstaller one-file EXE ----------------------------------------------------
def make_exe(path: Path, files: dict[str, bytes] | None = None, modules: dict[str, bytes] | None = None,
             version: str = "5.1.0") -> Path:
    """Bootloader stand-in + VS_VERSIONINFO strings + a real CArchive/PYZ layout."""
    files = dict(files or {})
    modules = dict(modules or {})
    pyz_body, pyz_toc = b"", []
    header_len = 12
    for name, content in modules.items():
        blob = zlib.compress(content)
        pyz_toc.append((name, (0, header_len + len(pyz_body), len(blob))))
        pyz_body += blob
    pyz = b"PYZ\0" + b"\0\0\0\0" + struct.pack("!i", header_len + len(pyz_body)) + pyz_body + marshal.dumps(pyz_toc)
    data, toc = b"", b""
    for name, content, typecode in [*((n, c, "b") for n, c in files.items()), ("PYZ-00.pyz", pyz, "z")]:
        enc = name.encode("utf-8") + b"\0"
        enc += b"\0" * (-len(enc) % 16)
        toc += struct.pack("!IIIIBc", 18 + len(enc), len(data), len(content), len(content), 0,
                           typecode.encode()) + enc
        data += content
    cookie_len = struct.calcsize("!8sIIII64s")
    pkg_len = len(data) + len(toc) + cookie_len
    cookie = struct.pack("!8sIIII64s", b"MEI\014\013\012\013\016", pkg_len, len(data), len(toc), 312,
                         b"python312.dll")
    pe = (b"MZ" + b"\0" * 62 + "ProductVersion\0".encode("utf-16-le") + b"\0\0"
          + f"{version}\0".encode("utf-16-le") + "FileVersion\0".encode("utf-16-le")
          + f"{version}\0".encode("utf-16-le"))
    path.write_bytes(pe + data + toc + cookie)
    return path


SHA = "0123456789abcdef0123456789abcdef01234567"
BUILD_INFO = f'BUILD_SHA = "{SHA}"\nBUILD_CHANNEL = "production"\n'.encode()


def _qt_inventory_files() -> dict[str, bytes]:
    inv = json.loads((PACKAGING / "setup-ui-qt-inventory.json").read_text(encoding="utf-8"))
    files = {f"PySide6\\plugins\\{fam}\\{f}": b"x" for fam, fs in inv["plugins"].items() for f in fs}
    files.update({f"PySide6\\{lib}": b"x" for lib in inv["libraries"]})
    return files


def clean_setup_ui(tmp: Path, **extra) -> Path:
    files = _qt_inventory_files()
    files.update(extra.pop("files", {}))
    modules = {"setup_gui": b"code", "agent_core": b"code", "build_info": BUILD_INFO}
    modules.update(extra.pop("modules", {}))
    return make_exe(tmp / "watchlog-setup-ui.exe", files, modules)


# === R1: hash-locked dependency sets ==========================================================
AGENT_510 = {"cryptography": "50.0.2", "imageio-ffmpeg": "0.6.0", "numpy": "2.5.3",
             "onnxruntime": "1.30.0", "pyinstaller": "6.22.3", "requests": "2.34.2",
             "psutil": "7.2.2", "tzdata": "2026.5", "pyinstaller-hooks-contrib": "2026.8"}
SETUP_510 = {"pyside6": "6.11.2", "pyside6-essentials": "6.11.2", "pyside6-addons": "6.11.2",
             "shiboken6": "6.11.2", "pyinstaller": "6.22.2", "requests": "2.34.2", "psutil": "7.2.2",
             "pyinstaller-hooks-contrib": "2026.8"}
AGENT_ONLY = {"onnxruntime", "numpy", "pillow", "imageio-ffmpeg", "cryptography", "cffi",
              "flatbuffers", "protobuf", "tzdata"}


def test_locks_are_consistent_with_their_in_files():
    assert locks.check("agent") == [] and locks.check("setup-ui") == []


@pytest.mark.parametrize("env,expected", [("agent", AGENT_510), ("setup-ui", SETUP_510)])
def test_locks_pin_what_the_passing_5_1_0_build_bundled(env, expected):
    lock = locks.parse_lock(PACKAGING / f"requirements-{env}.lock")
    for name, version in expected.items():
        assert lock[name][0] == version, f"{env}: {name} locked {lock[name][0]}, 5.1.0 used {version}"


@pytest.mark.parametrize("env", ["agent", "setup-ui"])
def test_every_lock_line_is_exact_hashed_and_for_cp312_win_amd64(env):
    text = (PACKAGING / f"requirements-{env}.lock").read_text(encoding="utf-8")
    lock = locks.parse_lock(PACKAGING / f"requirements-{env}.lock")
    assert len(lock) >= 10
    for name, (version, hashes) in lock.items():
        assert re.fullmatch(r"[0-9][0-9A-Za-z.]*", version), (name, version)
        assert len(hashes) == 1 and re.fullmatch(r"[0-9a-f]{64}", hashes[0]), name
    wheels = re.findall(r"#\s+(\S+\.whl)", text)
    assert len(wheels) == len(lock), "every lock entry names the exact wheel it hashes"
    for wheel in wheels:
        tags = wheel[:-4].split("-")[-3:]
        assert tags[2] in ("any", "win_amd64"), wheel
        assert tags[0] in ("py3", "py2.py3", "cp312") or tags[1] == "abi3", wheel
    assert "--require-hashes --no-deps --only-binary=:all:" in text


def test_setup_ui_lock_cannot_carry_the_agent_stack():
    lock = locks.parse_lock(PACKAGING / "requirements-setup-ui.lock")
    assert not (set(lock) & AGENT_ONLY), sorted(set(lock) & AGENT_ONLY)
    agent = locks.parse_lock(PACKAGING / "requirements-agent.lock")
    assert not ({"pyside6", "pyside6-addons", "pyside6-essentials", "shiboken6"} & set(agent))


def test_installed_set_check_rejects_missing_extra_and_different():
    lock = {"requests": ("2.34.2", ["a" * 64]), "psutil": ("7.2.2", ["b" * 64])}
    assert locks.compare_installed(lock, {"requests": "2.34.2", "psutil": "7.2.2", "pip": "25.0.1"}) == []
    problems = locks.compare_installed(lock, {"requests": "2.34.1", "numpy": "2.5.3"})
    assert any(p.startswith("different: requests") for p in problems)
    assert any(p.startswith("missing: psutil") for p in problems)
    assert any(p.startswith("extra: numpy") for p in problems)


def test_build_venv_helper_installs_only_the_lock_into_a_fresh_venv():
    ps = _read("prototype/packaging/build_env.ps1")
    assert "--require-hashes --no-deps --only-binary=:all: -r $lock" in ps
    assert "-m pip check" in ps and "--verify-installed $lock" in ps
    assert '$script:WatchLogReleasePython = "3.12.10"' in ps
    assert 'Join-Path $proto "build-venvs\\$Name"' in ps and "Remove-Item -Recurse -Force $venv" in ps
    assert re.search(r'ValidateSet\("agent", "setup-ui"\)', ps)


@pytest.mark.parametrize("script,name", [("prototype/agent/build_exe.ps1", "agent"),
                                         ("prototype/agent/build_setup_gui.ps1", "setup-ui")])
def test_each_exe_is_frozen_in_its_own_locked_venv(script, name):
    ps = _read(script)
    assert f'New-WatchLogBuildVenv -Name "{name}"' in ps
    assert "& $py -m PyInstaller" in ps
    assert not re.search(r"(?m)^\s*python\s+-m\s+(pip|PyInstaller)\b", ps), "global python used"
    assert "pip install" not in ps, "a build script installs outside the lock"


def test_release_paths_never_install_outside_the_locks():
    for rel in ("tools/build_windows_release.ps1", ".github/workflows/windows-release.yml"):
        assert "pip install" not in _read(rel), rel
    wr = _read(".github/workflows/windows-release.yml")
    assert "python-version: '3.12.10'" in wr
    assert "choco install nsis -y --no-progress --version 3.13.0" in wr
    ci = _read(".github/workflows/ci.yml")
    setup_job = ci[ci.index("  setup-ui-build:"):ci.index("  installer-contract:")]
    assert "python-version: '3.12.10'" in setup_job


def test_build_trees_are_ignored_so_the_clean_tree_gate_can_hold():
    gi = _read(".gitignore")
    for path in ("prototype/build-setup-ui/", "prototype/build-venvs/", "build/", "dist/",
                 "dist-installer/", "build_info.py"):
        assert path in gi, path


# === R3: selective Qt =========================================================================
def test_setup_ui_build_does_not_collect_all_of_pyside6():
    ps = _read("prototype/agent/build_setup_gui.ps1")
    assert not re.search(r'"--collect-all",\s*"PySide6"', ps)
    assert "--collect-all" not in re.sub(r"(?m)#.*$", "", ps)
    for mod in ("QtCore", "QtGui", "QtWidgets"):
        assert f'"--hidden-import", "PySide6.{mod}"' in ps
    assert "--check-setup-ui" in ps


def test_code_imports_only_the_qt_modules_the_build_names():
    used = set()
    for p in AGENT.glob("*.py"):
        used |= set(re.findall(r"\bPySide6\.(Qt\w+)", p.read_text(encoding="utf-8")))
    assert used <= {"QtCore", "QtGui", "QtWidgets"}, used


def test_qt_inventory_is_the_field_proven_build_69_set():
    inv = json.loads((PACKAGING / "setup-ui-qt-inventory.json").read_text(encoding="utf-8"))
    flat = {f"{k}/{f}" for k, v in inv["plugins"].items() for f in v}
    assert len(flat) == 22
    for need in ("platforms/qwindows.dll", "platforms/qoffscreen.dll", "styles/qmodernwindowsstyle.dll",
                 "imageformats/qico.dll", "iconengines/qsvgicon.dll", "tls/qschannelbackend.dll"):
        assert need in flat
    for lib in inv["libraries"] + sorted(flat):
        assert not sizes.FORBIDDEN_QT.search(f"PySide6/{lib}"), lib


def test_clean_frozen_setup_ui_passes_the_content_check(tmp_path):
    assert sizes.check_setup_ui(clean_setup_ui(tmp_path)) == []


@pytest.mark.parametrize("extra,needle", [
    ({"files": {"PySide6\\Qt6WebEngineCore.dll": b"x"}}, "Qt Addon"),
    ({"files": {"PySide6\\Qt63DRender.dll": b"x"}}, "Qt Addon"),
    ({"files": {"PySide6\\Qt6Multimedia.dll": b"x"}}, "Qt Addon"),
    ({"files": {"PySide6\\Qt6Designer.dll": b"x"}}, "Qt Addon"),
    ({"files": {"PySide6\\qml\\QtQuick\\Controls\\qmldir": b"x"}}, "Qt Addon"),
    ({"files": {"PySide6\\Qt6QuickControls2.dll": b"x"}}, "Qt Addon"),
    ({"files": {"PySide6\\plugins\\multimedia\\ffmpegmediaplugin.dll": b"x"}}, "Qt Addon"),
    ({"files": {"onnxruntime\\capi\\onnxruntime.dll": b"x"}}, "Agent-only file"),
    ({"files": {"yolov8n.onnx": b"x"}}, "Agent-only file"),
    ({"files": {"imageio_ffmpeg\\binaries\\ffmpeg-win-x86_64-v7.1.exe": b"x"}}, "Agent-only file"),
    ({"modules": {"numpy": b"x"}}, "heavy module"),
    ({"modules": {"watchlog_agent": b"x"}}, "heavy module"),
    ({"modules": {"cryptography.hazmat": b"x"}}, "heavy module"),
])
def test_content_check_rejects_agent_stack_and_qt_addons(tmp_path, extra, needle):
    problems = sizes.check_setup_ui(clean_setup_ui(tmp_path, **extra))
    assert any(needle in p for p in problems), problems


def test_content_check_requires_the_plugins_the_ui_loads(tmp_path):
    files = {k: v for k, v in _qt_inventory_files().items() if not k.endswith("qwindows.dll")}
    exe = make_exe(tmp_path / "ui.exe", files, {"build_info": BUILD_INFO})
    problems = sizes.check_setup_ui(exe)
    assert any("platforms/qwindows.dll" in p for p in problems)


def test_content_check_requires_a_baked_build_sha(tmp_path):
    exe = make_exe(tmp_path / "ui.exe", _qt_inventory_files(), {"setup_gui": b"x"})
    assert any("BUILD_SHA" in p for p in sizes.check_setup_ui(exe))


# === R4: size report ==========================================================================
def test_size_bounds_are_gross_sanity_only():
    mb = sizes.MB
    assert sizes.check_bounds("agent", 92_905_793) is None            # 5.1.0 AI Agent
    assert sizes.check_bounds("setup-ui", 49_932_693) is None         # selective Qt, measured
    assert sizes.check_bounds("setup-ui", 325_540_293)                # 5.1.0 with every Qt Addon
    assert sizes.check_bounds("agent", 2 * mb)                         # stub
    assert sizes.check_bounds("setup", 416_917_181)                    # 5.1.0 Setup
    assert sizes.check_bounds("repair", 150 * mb) is None


def test_size_report_reads_components_and_deltas(tmp_path):
    exe = make_exe(tmp_path / "a.exe", {"numpy\\core.pyd": b"n" * 1000, "yolov8n.onnx": b"m" * 500},
                   {"build_info": BUILD_INFO})
    info = sizes.describe(exe)
    assert info["components"]["numpy"] == 1000 and info["components"]["ONNX model"] == 500
    assert info["baked_build_sha"] == SHA
    rows = sizes.deltas(info["components"], {"numpy": 400, "Qt WebEngine": 9000}, 5)
    assert rows[0][0] == "Qt WebEngine" and rows[0][3] == -9000


def test_baseline_is_the_5_1_0_inventory():
    base = json.loads((PACKAGING / "size-baseline.json").read_text(encoding="utf-8"))
    assert base["agent"]["bytes"] == 92_905_793 and base["setup-ui"]["bytes"] == 325_540_293
    assert base["setup-ui"]["components"]["Qt WebEngine"] == 126_996_445


def test_reader_matches_pyinstallers_layout(tmp_path):
    exe = pyi_archive.PyInstallerExe(make_exe(tmp_path / "x.exe", {"a\\b.dll": b"123"},
                                              {"m": b"hello", "build_info": BUILD_INFO}))
    assert exe.names() == ["a\\b.dll", "PYZ-00.pyz"] and exe.python_library == "python312.dll"
    assert exe.extract("a\\b.dll") == b"123"
    assert exe.pyz_modules() == ["build_info", "m"] and exe.pyz_raw("m") == b"hello"
    assert exe.baked_build_sha() == SHA
    with pytest.raises(pyi_archive.ArchiveError):
        pyi_archive.PyInstallerExe(tmp_path.joinpath("plain.exe").write_bytes(b"MZ" * 50) and tmp_path / "plain.exe")


# === R5: NSIS payload proof ===================================================================
# 7-Zip's listing of the 5.1.0 release (run 134, BUILD69-FORENSIC-REPORT inventory).
R134_SETUP = ["watchlog-setup-ui.exe", "watchlog-agent.exe", "$PLUGINSDIR\\modern-wizard.bmp",
              "$PLUGINSDIR\\wl-upgrade.ps1", "wl-upgrade.ps1", "$PLUGINSDIR\\System.dll",
              "$PLUGINSDIR\\nsDialogs.dll", "run-agent.ps1", "apply-remote-update.ps1",
              "register-service.ps1", "setup.ico", "READ ME FIRST.txt", "watchlog.defaults.ini",
              "watchlog.ini", "uninstall.exe"]
_RC = "$APPDATA\\WatchLog\\repair-candidate\\"
R134_REPAIR = [_RC + "watchlog-setup-ui.exe", _RC + "watchlog-agent.exe", "$PLUGINSDIR\\modern-wizard.bmp",
               _RC + "wl-upgrade.ps1", "$PLUGINSDIR\\System.dll", "$PLUGINSDIR\\nsDialogs.dll",
               _RC + "run-agent.ps1", _RC + "apply-remote-update.ps1", _RC + "register-service.ps1",
               _RC + "watchlog.defaults.ini", _RC + "wl-repair-upgrade.ps1"]


def _payload_names(listing: list[str]) -> list[str]:
    return sorted(re.split(r"[\\/]", p)[-1].lower() for p in listing
                  if not payload.NSIS_INTERNAL.match(p) and p.lower() != "uninstall.exe")


@pytest.mark.parametrize("kind,listing", [("setup", R134_SETUP), ("repair", R134_REPAIR)])
def test_expected_file_list_derived_from_the_nsi_matches_a_real_release(kind, listing):
    expected = sorted(item["target_name"].lower() for item in payload.nsi_payload(payload.NSI[kind]))
    assert expected == _payload_names(listing)
    assert payload.writes_uninstaller(payload.NSI[kind]) == (kind == "setup")


def _fake_installer(tmp_path: Path, kind: str, listing: list[str], content: dict[str, bytes],
                    monkeypatch) -> Path:
    inst = tmp_path / f"{kind}.exe"
    inst.write_bytes(b"NSIS")

    def fake_list(_sz, _installer):
        return list(listing)

    def fake_extract(_sz, _installer, dest):
        for rel in listing:
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            name = re.split(r"[\\/]", rel)[-1]
            p.write_bytes(content.get(rel, content.get(name, b"internal")))

    monkeypatch.setattr(payload, "list_7z", fake_list)
    monkeypatch.setattr(payload, "extract_7z", fake_extract)
    return inst


def _good_content(tmp_path: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    agent = make_exe(tmp_path / "agent-src.exe", {"x.dll": b"a"}, {"build_info": BUILD_INFO}).read_bytes()
    ui = make_exe(tmp_path / "ui-src.exe", {"y.dll": b"b"}, {"build_info": BUILD_INFO}).read_bytes()
    content = {"watchlog-agent.exe": agent, "watchlog-setup-ui.exe": ui,
               "watchlog.defaults.ini": b"[watchlog]\n", "watchlog.ini": b"[watchlog]\n"}
    for name, path in payload.SOURCE_FILES.items():
        content[name] = path.read_bytes()
    expected = {name: payload.sha256(path) for name, path in payload.SOURCE_FILES.items()}
    import hashlib
    for name in ("watchlog-agent.exe", "watchlog-setup-ui.exe", "watchlog.defaults.ini"):
        expected[name] = hashlib.sha256(content[name]).hexdigest().upper()
    return content, expected


@pytest.mark.parametrize("kind,listing", [("setup", R134_SETUP), ("repair", R134_REPAIR)])
def test_payload_proof_passes_on_the_exact_payload(tmp_path, monkeypatch, kind, listing):
    content, expected = _good_content(tmp_path)
    inst = _fake_installer(tmp_path, kind, listing, content, monkeypatch)
    r = payload.prove(kind, inst, sevenzip="7z", nsi=payload.NSI[kind], expected=expected,
                      version="5.1.0", expected_sha=SHA, run_agent=False, workdir=tmp_path / "w")
    assert r["ok"], r["problems"]
    assert r["identity"]["watchlog-agent.exe"]["baked_build_sha"] == SHA
    assert r["identity"]["watchlog-setup-ui.exe"]["product_version"] == "5.1.0"


@pytest.mark.parametrize("mutate,needle", [
    (lambda c, l: c.__setitem__("watchlog-agent.exe", c["watchlog-agent.exe"] + b"tampered"), "SHA-256"),
    (lambda c, l: c.__setitem__("register-service.ps1", b"# edited after the build"), "SHA-256"),
    (lambda c, l: l.append("helper.exe"), "unexpected executable"),
    (lambda c, l: l.append("notes.txt"), "unexpected file"),
    (lambda c, l: l.remove("run-agent.ps1"), "missing from"),
    (lambda c, l: l.remove("watchlog-setup-ui.exe"), "missing from"),
])
def test_payload_proof_rejects_anything_but_the_exact_payload(tmp_path, monkeypatch, mutate, needle):
    content, expected = _good_content(tmp_path)
    listing = list(R134_SETUP)
    mutate(content, listing)
    inst = _fake_installer(tmp_path, "setup", listing, content, monkeypatch)
    r = payload.prove("setup", inst, sevenzip="7z", nsi=payload.NSI["setup"], expected=expected,
                      version="5.1.0", expected_sha=SHA, run_agent=False, workdir=tmp_path / "w")
    assert not r["ok"] and any(needle in p for p in r["problems"]), r["problems"]


def test_payload_proof_rejects_a_wrong_build_sha_or_version(tmp_path, monkeypatch):
    content, expected = _good_content(tmp_path)
    inst = _fake_installer(tmp_path, "repair", R134_REPAIR, content, monkeypatch)
    r = payload.prove("repair", inst, sevenzip="7z", nsi=payload.NSI["repair"], expected=expected,
                      version="5.1.1", expected_sha="f" * 40, run_agent=False, workdir=tmp_path / "w")
    assert any("ProductVersion" in p for p in r["problems"])
    assert any("baked BUILD_SHA" in p for p in r["problems"])


def test_repair_may_not_carry_an_uninstaller(tmp_path, monkeypatch):
    content, expected = _good_content(tmp_path)
    inst = _fake_installer(tmp_path, "repair", R134_REPAIR + ["uninstall.exe"], content, monkeypatch)
    r = payload.prove("repair", inst, sevenzip="7z", nsi=payload.NSI["repair"], expected=expected,
                      version="5.1.0", expected_sha=SHA, run_agent=False, workdir=tmp_path / "w")
    assert any("uninstall.exe" in p for p in r["problems"])


def test_payload_proof_fails_clearly_without_7zip(monkeypatch, tmp_path):
    monkeypatch.setattr(payload, "SEVENZIP_CANDIDATES", [str(tmp_path / "none" / "7z.exe")])
    monkeypatch.setattr(payload.shutil, "which", lambda _n: None)
    monkeypatch.delenv("SEVENZIP", raising=False)
    with pytest.raises(payload.ProofError, match="7-Zip"):
        payload.find_7z()


def test_release_workflow_uses_the_proof_not_size_heuristics():
    wr = _read(".github/workflows/windows-release.yml")
    assert "$repair.Length -lt $agent.Length" not in wr and "$setup.Length + 1MB" not in wr
    assert "tools/verify_installer_payload.py" in wr and "tools/release_size_report.py" in wr
    for f in ("WatchLog-Build-Manifest.json", "WatchLog-Payload-Proof.json", "WatchLog-Size-Report.json"):
        assert f"dist-installer/{f}" in wr and f"{f}.sha256=" in wr
    build = _read("tools/build_windows_release.ps1")
    assert "($setupBytes + 1MB)" not in build
    assert "tools\\verify_installer_payload.py" in build and "tools\\release_manifest.py" in build


# === V1/V2: version and build identity ========================================================
def test_one_version_source_and_nsis_fallbacks_agree():
    copies = bump_version.copies()
    assert set(copies.values()) == {_version()}, copies


def test_bump_is_one_command(tmp_path, monkeypatch):
    for rel in ("prototype/agent/wl_version.py", "prototype/installer/nsis/watchlog.nsi",
                "prototype/installer/nsis/watchlog-repair.nsi"):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst)
    monkeypatch.setattr(bump_version, "ROOT", tmp_path)
    monkeypatch.setattr(bump_version, "WL_VERSION", tmp_path / "prototype/agent/wl_version.py")
    monkeypatch.setattr(bump_version, "NSIS", [tmp_path / "prototype/installer/nsis/watchlog.nsi",
                                               tmp_path / "prototype/installer/nsis/watchlog-repair.nsi"])
    monkeypatch.setattr(sys, "argv", ["bump_version.py", "9.8.7"])
    assert bump_version.main() == 0
    assert set(bump_version.copies().values()) == {"9.8.7"}
    # nothing else in the two NSIS scripts changed
    for nsi in bump_version.NSIS:
        before = (ROOT / nsi.relative_to(tmp_path)).read_text(encoding="utf-8-sig")
        after = nsi.read_text(encoding="utf-8-sig")
        assert after.replace("9.8.7", _version()) == before.replace("\r\n", "\n") or \
            after.replace("9.8.7", _version()).replace("\r\n", "\n") == before.replace("\r\n", "\n")


def test_build_scripts_take_the_pe_version_from_wl_version_and_bake_build_sha():
    env = _read("prototype/packaging/build_env.ps1")
    assert "wl_version.py" in env and "BUILD_SHA = " in env and "build_info.py" in env
    for script in ("prototype/agent/build_exe.ps1", "prototype/agent/build_setup_gui.ps1"):
        ps = _read(script)
        assert "Get-WatchLogVersion" in ps and "Write-WatchLogBuildInfo" in ps
        assert '"--hidden-import", "build_info"' in ps
        assert "--version-file" in ps
        assert not re.search(r"ProductVersion', u'\d", ps), "hardcoded PE version"
    build = _read("tools/build_windows_release.ps1")
    assert "/DAPPVERSION=$appVersion" in build and "$env:WATCHLOG_BUILD_SHA = $sourceSha" in build
    assert "refusing to build a release from a dirty tree" in build and "[switch]$AllowDirty" in build


def test_agent_version_keeps_line_one_bare_and_adds_the_build_sha():
    out = subprocess.run([sys.executable, str(AGENT / "watchlog_agent.py"), "--version"],
                         capture_output=True, text=True, timeout=120, cwd=str(AGENT))
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == _version()
    assert re.fullmatch(r"build_sha=([0-9a-f]{40})?", lines[1])
    assert lines[2].startswith("build_channel=")


def test_version_consumers_read_only_the_first_line():
    for rel in ("prototype/installer/apply-remote-update.ps1", "prototype/installer/nsis/wl-upgrade.ps1",
                "prototype/installer/wl-repair-upgrade.ps1"):
        text = _read(rel)
        calls = list(re.finditer(r"&\s*\$\w+\s+--version", text))
        assert calls, f"{rel} no longer runs --version"
        for m in calls:
            window = text[m.start():m.start() + 200]
            assert "Select-Object -First 1" in window, f"{rel} reads more than line 1 of --version"


def test_manifest_gate_reads_the_baked_sha_and_pe_version(tmp_path):
    exe = make_exe(tmp_path / "a.exe", {"x": b"1"}, {"build_info": BUILD_INFO}, version="5.1.0")
    info = release_manifest.exe_info(exe)
    assert info["baked_build_sha"] == SHA and info["product_version"] == "5.1.0"
    assert release_manifest.wl_version() == _version()


def test_release_manifest_records_what_the_spec_requires():
    src = _read("tools/release_manifest.py")
    for key in ('"image_os"', '"image_version"', '"python"', '"pip"', '"pyinstaller"', '"qt"', '"nsis"',
                '"build_envs"', '"yolo_model"', '"ffmpeg"', '"dirty"', '"artifacts"', '"staged_payload"',
                "baked BUILD_SHA"):
        assert key in src, key


# === 8: committed field update bootstrap =======================================================
FIELD_KEY = "0OM6vRLc4lCxY7uj2OpnEMNUikyK8uv5arw/04bFOM0="
FIELD_URL = "https://oyvgubyxmjlijiczjona.supabase.co/functions/v1/watchlog-update-manifest"


def test_production_update_bootstrap_is_the_field_feed():
    boot = json.loads(_read("prototype/update/production.json"))
    assert boot["schema"] == "watchlog.update_bootstrap.v1" and boot["channel"] == "production"
    assert boot["public_key_b64"] == FIELD_KEY
    assert boot["manifest_url"] == FIELD_URL
    assert boot["release_tag"] == "watchlog-production" and boot["min_remote_update_version"] == "5.0.24"
    import base64
    assert len(base64.b64decode(boot["public_key_b64"])) == 32      # an Ed25519 public key


def test_release_build_falls_back_to_the_bootstrap_after_params_and_env():
    build = _read("tools/build_windows_release.ps1")
    url = build[build.index("$updUrl = $UpdateUrl"):build.index("$updKey = $UpdatePublicKey")]
    order = [url.index("$updUrl = $UpdateUrl"), url.index("$env:WATCHLOG_UPDATE_URL"),
             url.index('$cfg["WATCHLOG_UPDATE_URL"]'), url.index("$updateBootstrap.manifest_url")]
    assert order == sorted(order)
    key = build[build.index("$updKey = $UpdatePublicKey"):]
    order = [key.index("$updKey = $UpdatePublicKey"), key.index("$env:WATCHLOG_UPDATE_PUBLIC_KEY"),
             key.index('$cfg["WATCHLOG_UPDATE_PUBLIC_KEY"]'), key.index("$updateBootstrap.public_key_b64")]
    assert order == sorted(order)
    assert 'Join-Path $root "prototype\\update\\production.json"' in build


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
