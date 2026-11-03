# Windows packaging: reproducible, isolated, proven builds (5.1.x)

Scope: how `watchlog-agent.exe`, `watchlog-setup-ui.exe`, `WatchLog-Setup.exe` and
`WatchLog-Repair-Upgrade.exe` are built, what each contains, and what the release build proves
before an artifact exists. Evidence base: the Build 69 forensic report (sizes, components,
root causes, the selective-Qt experiment) and the installer/update audit, section D.

Build path (`tools/build_windows_release.ps1`, run by `.github/workflows/windows-release.yml`):

```
clean committed tree (refuses a dirty tree unless -AllowDirty)
  -> Agent: fresh venv from requirements-agent.lock -> PyInstaller 6.22.3 (-WithAI)
  -> Setup UI: fresh venv from requirements-setup-ui.lock -> PyInstaller 6.22.2, selective Qt
       -> content check of the frozen archive (no Agent runtime, Qt inventory, BUILD_SHA)
  -> optional inner signing -> staged public config -> NSIS 3.13.0 (Setup, Repair/Upgrade)
  -> optional installer signing
  -> build manifest (dist-installer/WatchLog-Build-Manifest.json; fails on a SHA/lock/version mismatch)
  -> size sanity report (dist-installer/WatchLog-Size-Report.json)
  -> NSIS payload proof with 7-Zip (dist-installer/WatchLog-Payload-Proof.json)
  -> SHA-256 files; the workflow re-runs the proof and attests the three JSON files in
     WatchLog-Release.hashes.txt
```

---

## R1. Locked dependency sets

| File | What |
|---|---|
| `prototype/packaging/requirements-agent.in` | Top-level build deps of the Agent, pinned to what the passing 5.1.0 build bundled (run 134) |
| `prototype/packaging/requirements-agent.lock` | Every package, transitive deps included, `==` plus the SHA-256 of the exact cp312 win_amd64 wheel (23 packages) |
| `prototype/packaging/requirements-setup-ui.in` / `.lock` | The same for the Setup UI (17 packages) |
| `tools/lock_build_deps.py` | Regenerates the locks; `--check` (offline consistency); `--verify-installed` (exact-set check run inside a build venv) |

Pins, taken from the 5.1.0 release (frozen dist-info plus PyInstaller's own log line
`PyInstaller: 6.22.3, contrib hooks: 2026.8 / Python: 3.12.10`):

| Package | Agent | Setup UI |
|---|---|---|
| PyInstaller | 6.22.3 | 6.22.2 |
| pyinstaller-hooks-contrib | 2026.8 | 2026.8 |
| requests / urllib3 / idna / charset-normalizer / certifi | 2.34.2 / 2.8.0 / 3.20 / 3.5.2 / 2026.7.22 | same |
| psutil | 7.2.2 | 7.2.2 |
| onnxruntime (+ flatbuffers 25.12.19, protobuf 7.36.2, packaging 26.3) | 1.30.0 | absent |
| numpy | 2.5.3 | absent |
| Pillow | 12.3.0 (resolved as of the 5.1.0 build: no dist-info was bundled) | absent |
| tzdata | 2026.5 | absent |
| cryptography (+ cffi 2.1.1, pycparser 3.0) | 50.0.2 | absent |
| imageio-ffmpeg | 0.6.0 | absent |
| PySide6 / PySide6_Essentials / PySide6_Addons / shiboken6 | absent | 6.11.2 |
| altgraph / pefile / pywin32-ctypes / setuptools (PyInstaller deps) | 0.17.5 / 2024.8.26 / 0.2.3 / 84.0.0 | same |

Hashes are real wheel hashes, not placeholders: the locks were resolved with
`pip install --dry-run --report` for `--python-version 3.12 --platform win_amd64` (index
metadata only), restricted to uploads before 2026-10-06T06:50Z (the 5.1.0 build started 06:51Z),
and then every wheel was downloaded with `pip download --require-hashes` and matched.

Install rule (prototype/packaging/build_env.ps1, the only place a build installs anything):
`python -m pip install --require-hashes --no-deps --only-binary=:all: -r <lock>`, then
`pip check`, then `lock_build_deps.py --verify-installed`, which fails on any missing, extra or
different distribution (pip itself excepted; its version is recorded). pip therefore never
resolves anything: `--no-deps` stops it adding a package, `--require-hashes` stops it taking a
different file, and the exact-set check catches anything else in the venv.

Changing a dependency: edit the `.in`, run `python tools/lock_build_deps.py`, commit both. CI
(`test_release_packaging.py`) fails if a lock drifts from its `.in`.

The interpreter is pinned too: `actions/setup-python` uses `3.12.10` in windows-release.yml and
in ci.yml's `setup-ui-build`, and build_env.ps1 refuses any other base Python for a release
(`-AllowPythonMismatch` exists for developer builds; cp312 wheels then fail to install anyway).

### Build manifest

`tools/release_manifest.py` writes `dist-installer/WatchLog-Build-Manifest.json`:

- runner: `ImageOS`, `ImageVersion` (set by GitHub-hosted images), `RUNNER_*`, `platform.platform()`;
- toolchain: Python and pip per venv, PyInstaller and hooks-contrib per venv, PySide6 plus the
  Qt6Core.dll FileVersion read from the frozen Setup UI, the Qt plugin list, NSIS (`makensis /VERSION`);
- `build_envs`: the full installed set (`freeze`) of each venv and whether it matched its lock;
  the SHA-256 of each lock file;
- `yolo_model`: SHA-256 of `prototype/models/yolov8n.onnx` and of the copy bundled in the Agent
  (must be equal);
- `ffmpeg`: the bundled `imageio_ffmpeg/binaries/ffmpeg-*.exe` entry, its SHA-256, bytes and
  version (from the binary's file name, for example `7.1`), and the imageio-ffmpeg version;
- `source`: SHA, dirty flag (and paths), `-AllowDirty`, GitHub run identity;
- `artifacts`: SHA-256, bytes, PE ProductVersion/FileVersion and baked BUILD_SHA of all four EXEs;
- `staged_payload`: SHA-256 of every staged NSIS input (including the generated
  `watchlog.defaults.ini`, which the payload proof checks against).

It exits non-zero, failing the build, when a baked BUILD_SHA differs from the source SHA, a
venv did not match its lock, a PE ProductVersion differs from `wl_version`, the bundled model
differs from its source, the AI build bundles no model or FFmpeg, or the tree was dirty without
`-AllowDirty`. The workflow additionally requires `invariants_ok`, `source.sha == GITHUB_SHA`
and `dirty == false`, and puts the manifest's SHA-256 into `WatchLog-Release.hashes.txt`.

### GitHub-hosted image limits (stated honestly)

- `windows-latest` is a moving label. Build 69 ran on image `20260922.246.2`; 5.0.28 and 5.1.0 on
  `windows-2025-vs2026` `20260925.250.1`. A hosted runner cannot be pinned to a dated image
  version, only to a label (`windows-2025`, ...), and every label is still updated weekly.
- So this design pins everything *inside* the image that matters to the bytes we ship (Python
  3.12.10, every Python package by hash, NSIS 3.13.0) and records the image that was used. It
  does not make two builds byte-identical: PyInstaller output embeds timestamps, and the runner's
  own DLLs (for example `VCRUNTIME140.dll` that Python ships) come from the image.
- The forensic root cause of the 206 MB Qt growth was `--collect-all PySide6` behaving
  differently on the newer image. That dependency on what the image can import is removed (R3).
- If the owner wants a stronger guarantee, the options are a self-hosted runner with a frozen
  image, or a label such as `windows-2025` instead of `windows-latest` (still updated weekly).
  Neither is changed here.

## R2. Isolated build environments

`build_exe.ps1` and `build_setup_gui.ps1` each call `New-WatchLogBuildVenv`, which deletes and
re-creates `prototype/build-venvs/<agent|setup-ui>` from the base interpreter and installs only
that EXE's lock. The Setup UI venv contains no onnxruntime, numpy, Pillow, imageio-ffmpeg or
cryptography, so PyInstaller cannot collect them even if an import appeared. Both venv paths
and `prototype/build-setup-ui/` are gitignored (the clean-tree gate needs every generated path
ignored). The field release gates in windows-release.yml (`test_recorder_probe.py`,
`test_recorder_setup.py`, `test_existing_site_repair.py`) run with the Agent venv's Python,
because the runner's global Python no longer has anything installed.

## R2b. Setup no longer imports the Agent runtime

Audit (every `core.` use reachable from the Setup UI, 5.1.0):

| Caller | Names used from `watchlog_agent` |
|---|---|
| `setup_backend.py` (`import watchlog_agent as core`) | `Cloud`, `CloudError`, `heartbeat`, `iso`, `load_state`, `log`, `now_utc`, `save_state`, `upload_once` |
| `setup_gui.py` (`backend.core.`) | `Config` (Repair registry selftest/staging), `open_driver` (Repair recorder probe) |
| `site_status_gui.py`, `status_controller.py`, `site_status.py` | none (`status_controller` runs the installed Agent EXE as a subprocess) |

Through that one import Setup bundled `vision` (onnxruntime, Pillow), `recovery` / `recovery_ai`
(imageio-ffmpeg, cv2 import), `updater` (cryptography), `analytics`-side modules, `health_store`,
`periodic_stills` and more. `dahua_archive` (requests + drivers only) does not drag a heavy
dependency and stays.

Change: those names, plus what they need (`update_runtime_health`, `runtime_health_path`,
`_event_stream_health`, `base_dir`, `default_state_dir`, `mask`, the recorder open path
`_open_driver_unverified` / `_connect_recorder` / `require_recorder_identity` / ...,
`_registry_configures_recorders`, `_is_auth_failure` and the constants they read) moved verbatim
into `prototype/agent/agent_core.py`. `watchlog_agent` re-exports every one of them, so
`watchlog_agent.X`, `core.X`, `analytics_agent`'s `core.Config = Config` override and
`release_agent`'s `app.Config.__init__` override behave as before. `setup_backend` imports
`agent_core as core`; `setup_gui`'s `backend.core.Config/open_driver` now resolve there without
an edit. No `--exclude-module` hides anything: the modules are simply not imported.

One consequence for tests: a test that replaces a collaborator to change what a *moved*
function does must patch it on `agent_core` (or on `setup_backend.core` for Setup's calls).
`test_disable_secondary_runtime.py` was the one test that needed this (`sb.core.Cloud`).

Guards:

- `prototype/tests/test_setup_import_graph.py` walks the import graph PyInstaller walks (every
  import statement, including function-level ones, from `setup_gui` plus the build's
  `--hidden-import` list) and fails if any Agent runtime module or any of numpy, onnxruntime, PIL,
  imageio_ffmpeg, cv2, cryptography, torch, ultralytics is reachable; it also pins the allowed
  third-party set (PySide6, requests, urllib3, psutil) and checks that every `core.X` Setup uses
  exists in `agent_core`. It fails on the 5.1.0 code (verified).
- `tools/release_size_report.py --check-setup-ui <exe>` checks the *frozen* archive after every
  Setup UI build (build_setup_gui.ps1 and the release workflow).

Nothing that Setup "truly requires" had to stay: the frozen Setup UI built locally (below) has no
onnxruntime, numpy, Pillow, FFmpeg, cryptography package or Agent runtime module.

## R3. Selective Qt

`--collect-all PySide6` is gone from `build_setup_gui.ps1`. PyInstaller's PySide6 hooks collect
what `QtCore`, `QtGui` and `QtWidgets` need. The code imports no other Qt module
(`grep PySide6\.` over prototype/agent: setup_gui.py and site_status_gui.py only), enforced by
`test_code_imports_only_the_qt_modules_the_build_names`.

Why each excluded family is not required:

| Family | Why not needed |
|---|---|
| WebEngine / WebView / WebChannel | Setup renders no web content; links open the system browser via `QDesktopServices.openUrl` (QtGui) |
| QML / Quick-only modules (Controls2, Templates2, Dialogs, Layouts, Shapes, Quick3D, the `qml/` import tree) | The UI is built in Python with QtWidgets; no `.qml` file exists. `QFileDialog` and `QMessageBox` are QtWidgets classes |
| Multimedia, SpatialAudio, TextToSpeech | No audio or video in Setup. Footage decoding is the Agent's FFmpeg, not Qt |
| 3D, Charts, DataVisualization, Graphs, Location/Positioning, Bluetooth, NFC, Sensors, SerialPort, Sql, HttpServer, Lottie, Labs | Never imported |
| Designer / UiTools / Help, assistant/designer/linguist tools | UI is code, not `.ui` files loaded at runtime |
| QtNetwork as a transport | HTTPS to the cloud and HTTP(S) to recorders go through `requests` and Python's `ssl`. QtNetwork.pyd and the TLS plugins are still collected by the hooks (as in Build 69) |

`Qt6Qml`, `Qt6QmlMeta`, `Qt6QmlModels`, `Qt6QmlWorkerScript`, `Qt6Quick` and
`Qt6VirtualKeyboard` stay: the virtual-keyboard input-context plugin the hooks collect links
them, exactly as in every field-proven Setup UI (Build 69: 5,511,528 B "QML/Quick").

The committed inventory `prototype/packaging/setup-ui-qt-inventory.json` is Build 69's: 22 plugin
files (platforms qwindows/qoffscreen/qminimal/qdirect2d; styles qmodernwindowsstyle; imageformats
gif/icns/ico/jpeg/pdf/svg/tga/tiff/wbmp/webp; iconengines qsvgicon; tls
schannel/openssl/certonly; networkinformation qnetworklistmanager; platforminputcontexts
qtvirtualkeyboardplugin; generic qtuiotouchplugin) and the allowed Qt library list. The content
check requires qwindows + qoffscreen (desktop + CI offscreen), qmodernwindowsstyle, qico (the
`setup.ico` window icon), qsvgicon and qschannelbackend; requires the plugin set to equal the
inventory; rejects any Qt library outside it; and rejects WebEngine, Quick-only, 3D, Multimedia,
Designer and the other Addon families by name.

Measured locally (2026-10-06, this branch, PyInstaller 6.22.2 + PySide6 6.11.2 from the lock's
wheels, but Python 3.13: the PC has no 3.12): `watchlog-setup-ui.exe` = 49,932,693 B (5.1.0:
325,540,293 B). Qt core 12,204,952 B, plugins 2,294,613 B (22 files), QML/Quick 5,511,528 B,
translations 1,936,459 B, PDF 2,463,852 B: byte-for-byte the Build 69 component sizes. The
5.0.28 experiment measured cold launch to first paint at 3.95 s versus 21.89 s with collect-all.

## R4. Size budgets (size is never integrity)

`tools/release_size_report.py` reads each frozen EXE's PyInstaller table of contents
(`tools/pyi_archive.py`, a dependency-free reader of the CArchive and PYZ formats), prints
compressed bytes per component and the largest deltas against `prototype/packaging/size-baseline.json`
(the 5.1.0 inventory), and fails only outside gross bounds:

| Artifact | Bounds | Reference |
|---|---|---|
| Agent (AI) | 70-140 MiB | 5.1.0: 88.6 MiB |
| Agent (lean, `--lean`) | 8-70 MiB | 5.0.17 lean: 12.9 MiB |
| Setup UI | 30-180 MiB | Build 69: 51.0 MiB; this branch: 47.6 MiB; 5.1.0 collect-all: 310 MiB (fails) |
| Setup, Repair/Upgrade | 100-330 MiB | Agent + Setup UI + scripts |

Refresh the baseline after a reviewed release: `--write-baseline`.

## R5. NSIS payload proof

`tools/verify_installer_payload.py` (run by the release script and again by the workflow):

1. finds 7-Zip (`--sevenzip`, `%SEVENZIP%`, PATH, `C:\Program Files\7-Zip\7z.exe`) and fails
   with a clear message without it;
2. lists (`7z l -slt`) and extracts each installer;
3. derives the expected payload from the installer's `.nsi` `File` commands (`/oname=` targets
   included); allows only NSIS's own `$PLUGINSDIR\*.dll|bmp` and, for a script that has
   `WriteUninstaller` (Setup; Repair from 5.1.1), `uninstall.exe`; fails on any missing file, unexpected file, or
   executable other than `watchlog-agent.exe` / `watchlog-setup-ui.exe`;
4. hashes every embedded file: both EXEs must equal the pre-NSIS dist files, scripts / readme /
   icon must equal their source files, `watchlog.defaults.ini` (and Setup's `watchlog.ini`
   copy) must equal the staged file recorded in the build manifest;
5. for the extracted EXEs: PE ProductVersion = `wl_version`, baked BUILD_SHA = source SHA, and
   the Agent's runtime `--version` (line 1 = version, a `build_sha=<source SHA>` line).

The size heuristics it replaces ("Repair >= Agent", "within 1 MB of Setup") are removed from the
workflow and the release script; only broad lower bounds remain. Their test fixture is the real
7-Zip listing of the 5.1.0 release, so the `.nsi`-derived expectation is checked against shipped
bytes.

## V1/V2. Version and build identity

- `prototype/agent/wl_version.py` `VERSION` stays the single product-version source. The PE
  version resources of both EXEs are generated from it (`Get-WatchLogVersion` in
  build_env.ps1); NSIS gets it as `/DAPPVERSION`. The two NSIS `!ifndef APPVERSION` fallbacks
  (for a bare `makensis`) are the only copies.
- Bump = one command: `python tools/bump_version.py 5.1.1` rewrites `wl_version.py` and both
  NSIS fallbacks; `--check` prints them. The release record section in
  WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md remains the release owner's prose (its own contract test).
  Version stays 5.1.0 on this branch.
- Agent `--version`: line 1 is still the bare version (apply-remote-update.ps1, wl-upgrade.ps1 and
  wl-repair-upgrade.ps1 read only the first line; a test pins that), followed by
  `build_sha=<40 hex>` and `build_channel=<channel>`.
- Setup UI: `build_info.py` is now stamped by `build_setup_gui.ps1` too (it used to bundle
  whatever stale file was in the tree); `--version` prints
  `watchlog-setup-ui <version> build_sha=<sha>` (best-effort console on a windowed EXE; the
  authoritative read is the archive).
- The release build sets `WATCHLOG_BUILD_SHA` to `git rev-parse HEAD` (and refuses if
  `GITHUB_SHA` differs), and the manifest step fails if either EXE's baked SHA differs.

Server change needed to report the build SHA to the cloud (owner approval item, NOT done; it
needs a migration):

1. a new migration (next free number, checking open branches) adding `agents.agent_build_sha text`;
2. `wl_heartbeat` gaining `p_agent_build_sha text default null` and storing
   `coalesce(p_agent_build_sha, a.agent_build_sha)`. Because PostgREST resolves functions by
   argument names, the old 6-argument signature must be dropped in the same migration (or an
   overload kept deliberately) so 5.0.x Agents, which send six named arguments, keep working;
   grants to `anon` re-issued;
3. optionally the same parameter on `wl_enroll`;
4. the Agent then passes `p_agent_build_sha=wl_version.BUILD_SHA` in `agent_core.heartbeat`.
   It must not put `+sha` into `p_agent_version`: the server parses that column as a version
   triplet (0156).

## 8. Signed update feed bootstrap

`prototype/update/production.json` (ported byte-for-byte from the field line, `a3266326`) holds
the production manifest URL
`https://oyvgubyxmjlijiczjona.supabase.co/functions/v1/watchlog-update-manifest`, the Ed25519
public key `0OM6vRLc4lCxY7uj2OpnEMNUikyK8uv5arw/04bFOM0=` (32 bytes), release tag
`watchlog-production` and `min_remote_update_version` 5.0.24. Both are public material. The
release build uses them when `-UpdateUrl/-UpdatePublicKey`, `WATCHLOG_UPDATE_URL/_PUBLIC_KEY`
and `.env` give nothing (those still win, in that order). The workflow warns when repository
variables differ from the committed bootstrap, because field 5.0.24-5.0.26 Agents verify with
the committed key.

## What is proven where

| Claim | Proven by | Where it runs |
|---|---|---|
| Locks consistent, pinned to 5.1.0, cp312/win_amd64, Setup lock has no Agent stack | test_release_packaging.py | CI backend job |
| Lock hashes are real | `pip download --require-hashes` of all 40 wheels | locally, 2026-10-06 |
| Setup cannot import the Agent runtime | test_setup_import_graph.py; frozen check | CI backend; every Setup UI build |
| Frozen Setup UI content, Qt inventory, size | release_size_report.py `--check-setup-ui` | build_setup_gui.ps1 (CI setup-ui-build, release) |
| NSIS payload identity | verify_installer_payload.py | release script + workflow (needs 7-Zip) |
| Build SHA / version parity | release_manifest.py, `--version`, tests | release script + workflow + CI |
