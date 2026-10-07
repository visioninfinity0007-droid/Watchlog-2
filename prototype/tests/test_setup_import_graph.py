#!/usr/bin/env python3
"""Packaging guard: WatchLog Setup does not import the Agent runtime.

watchlog-setup-ui.exe used to carry onnxruntime, numpy, Pillow and the FFmpeg decoder
(about 58.6 MB compressed in 5.1.0) because setup_backend did ``import watchlog_agent as core``
and the release built the Agent with -WithAI in the same Python before freezing Setup
(BUILD69-FORENSIC-REPORT.md section 3B). Setup now imports the shared ``agent_core`` and is
frozen in its own venv.

This walks the static import graph PyInstaller walks: every ``import`` statement in a module,
including the ones inside functions, starting from the Setup UI entrypoint and the
``--hidden-import`` names build_setup_gui.ps1 adds. It fails if the Setup UI can reach the
Agent runtime or its heavy stack. ``--exclude-module`` is not used to hide anything: the
modules are simply not imported.

The frozen counterpart (the Setup UI's own PyInstaller archive) is checked by
tools/release_size_report.py --check-setup-ui after every Setup UI build.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "prototype" / "agent"
BUILD_SETUP_UI = AGENT / "build_setup_gui.ps1"

# Agent runtime modules Setup must never bundle.
FORBIDDEN_LOCAL = {
    "watchlog_agent", "analytics_agent", "analytics", "analytics_setup", "release_agent",
    "vision", "recovery", "recovery_ai", "backfill", "archive_runtime", "updater",
    "remote_update", "multi_recorder_fanout", "native_event_collector", "periodic_stills",
    "incident_evidence", "recorder_analytics", "action_runtime", "health_store",
}
# Heavy third-party stacks Setup must never bundle. cryptography is listed because only the
# Agent's signed-update path (updater.py, Ed25519) uses it; Setup's DPAPI is ctypes.
FORBIDDEN_THIRD_PARTY = {
    "numpy", "onnxruntime", "PIL", "imageio_ffmpeg", "cv2", "ultralytics", "torch",
    "cryptography", "matplotlib", "pandas", "scipy",
}
# What Setup legitimately needs from outside the standard library.
ALLOWED_THIRD_PARTY = {"PySide6", "requests", "urllib3", "psutil", "build_info"}


def _local_modules() -> set[str]:
    names = {p.stem for p in AGENT.glob("*.py")}
    names |= {d.name for d in AGENT.iterdir() if (d / "__init__.py").exists()}
    return names


def _module_file(name: str) -> Path | None:
    p = AGENT.joinpath(*name.split("."))
    if p.with_suffix(".py").exists():
        return p.with_suffix(".py")
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return None


def _imports(module: str, path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out: list[str] = []
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package
                for _ in range(node.level - 1):
                    base = base.rpartition(".")[0]
                mod = f"{base}.{node.module}" if node.module else base
            else:
                mod = node.module or ""
            out.append(mod)
            out += [f"{mod}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Call):
            # importlib.import_module("x") / __import__("x") with a literal name
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if fname in ("import_module", "__import__") and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.append(node.args[0].value)
    return out


def _hidden_imports() -> list[str]:
    ps = BUILD_SETUP_UI.read_text(encoding="utf-8")
    return re.findall(r'"--hidden-import",\s*"([^"]+)"', ps)


def import_graph(entries: list[str]) -> tuple[dict[str, str | None], dict[str, set[str]]]:
    """(reachable local modules -> importer, third-party top-level -> importing modules)."""
    local = _local_modules()
    seen: dict[str, str | None] = {}
    third: dict[str, set[str]] = {}
    stack: list[tuple[str, str | None]] = [(e, None) for e in entries]
    while stack:
        module, parent = stack.pop()
        if module in seen:
            continue
        path = _module_file(module)
        if path is None:
            continue
        seen[module] = parent
        for name in _imports(module, path):
            top = name.split(".")[0]
            if top in local:
                if _module_file(name):
                    stack.append((name, module))
                elif _module_file(name.rpartition(".")[0]):
                    stack.append((name.rpartition(".")[0], module))
            elif top:
                third.setdefault(top, set()).add(module)
    return seen, third


def _chain(seen: dict[str, str | None], module: str) -> str:
    chain = [module]
    while seen.get(chain[-1]):
        chain.append(seen[chain[-1]])
    return " <- ".join(chain)


def _setup_entries() -> list[str]:
    hidden = [h for h in _hidden_imports() if h.split(".")[0] in _local_modules()]
    return ["setup_gui", *hidden]


def test_build_script_names_the_real_entrypoint_and_hidden_imports():
    ps = BUILD_SETUP_UI.read_text(encoding="utf-8")
    assert "agent\\setup_gui.py" in ps
    hidden = _hidden_imports()
    for name in ("setup_backend", "site_status_gui", "status_controller", "discover",
                 "drivers.hikvision", "drivers.dahua", "drivers.onvif_driver"):
        assert name in hidden, f"build_setup_gui.ps1 no longer names --hidden-import {name}"


def test_setup_ui_cannot_reach_the_agent_runtime():
    seen, _third = import_graph(_setup_entries())
    leaked = sorted(set(seen) & FORBIDDEN_LOCAL)
    assert not leaked, "Setup UI imports the Agent runtime: " + "; ".join(_chain(seen, m) for m in leaked)


def test_setup_ui_cannot_reach_the_heavy_stack():
    seen, third = import_graph(_setup_entries())
    leaked = {k: sorted(v) for k, v in third.items() if k in FORBIDDEN_THIRD_PARTY}
    assert not leaked, ("Setup UI imports heavy packages: "
                        + "; ".join(f"{k} via {', '.join(_chain(seen, m) for m in v)}" for k, v in leaked.items()))


def test_setup_ui_third_party_set_is_exactly_what_setup_needs():
    import sys
    stdlib = set(sys.stdlib_module_names)
    _seen, third = import_graph(_setup_entries())
    unexpected = sorted(k for k in third if k not in stdlib and k not in ALLOWED_THIRD_PARTY
                        and k != "__future__")
    assert not unexpected, f"Setup UI gained third-party imports {unexpected}: add them to the " \
                           "Setup UI lock and to ALLOWED_THIRD_PARTY deliberately"


def test_agent_core_stays_light():
    seen, third = import_graph(["agent_core"])
    assert not (set(seen) & FORBIDDEN_LOCAL), sorted(set(seen) & FORBIDDEN_LOCAL)
    assert not (set(third) & FORBIDDEN_THIRD_PARTY), sorted(set(third) & FORBIDDEN_THIRD_PARTY)


def test_setup_backend_uses_agent_core_not_watchlog_agent():
    src = (AGENT / "setup_backend.py").read_text(encoding="utf-8")
    assert re.search(r"^import agent_core as core\b", src, re.M)
    assert not re.search(r"^\s*(import watchlog_agent|from watchlog_agent import)", src, re.M)


def test_every_core_name_setup_uses_is_defined_in_agent_core():
    """setup_backend's ``core.X`` and setup_gui's ``backend.core.X`` must resolve in
    agent_core, or Setup would fail at runtime on a path the tests do not drive."""
    import sys
    sys.path.insert(0, str(AGENT))
    import agent_core

    used = set()
    for name, pattern in (("setup_backend.py", r"\bcore\.([A-Za-z_]\w*)"),
                          ("setup_gui.py", r"\bbackend\.core\.([A-Za-z_]\w*)"),
                          ("site_status_gui.py", r"\bbackend\.core\.([A-Za-z_]\w*)")):
        used |= set(re.findall(pattern, (AGENT / name).read_text(encoding="utf-8")))
    missing = sorted(n for n in used if not hasattr(agent_core, n))
    assert not missing, f"Setup uses core names agent_core does not define: {missing}"


def test_watchlog_agent_reexports_the_shared_core():
    import sys
    sys.path.insert(0, str(AGENT))
    import agent_core
    import watchlog_agent

    public = [n for n in vars(agent_core) if not n.startswith("__")
              and getattr(vars(agent_core)[n], "__module__", "agent_core") == "agent_core"
              and (callable(vars(agent_core)[n]) or n.isupper())]
    missing = [n for n in public if getattr(watchlog_agent, n, None) is not getattr(agent_core, n)]
    assert not missing, f"watchlog_agent no longer re-exports {missing}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
