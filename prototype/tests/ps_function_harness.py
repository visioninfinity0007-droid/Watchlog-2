"""Run selected functions of a WatchLog PowerShell script without running its body.

Loads ONLY the top-level function definitions of the script through the PowerShell AST, then
runs a caller-supplied snippet that sets the script variables the functions need, stubs what
must not run for real (scheduled tasks, the upgrade helper, powercfg) and prints results. The
script's own body (which pauses or replaces a live install) never runs. Shared by the 5.1.1
installer/upgrade lifecycle tests; not a test module itself.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")

_PRELUDE = r"""
param([string]$Script)
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  . ([scriptblock]::Create($fn.Extent.Text))
}
"""


def run_functions(script: Path, body: str, *, timeout: int = 180,
                  work: Path | None = None) -> subprocess.CompletedProcess:
    """Run ``body`` after loading the functions of ``script``. Returns the finished process."""
    if not POWERSHELL:
        raise RuntimeError("Windows PowerShell is not available")
    folder = Path(work) if work else Path(tempfile.mkdtemp(prefix="wl-ps-harness-"))
    harness = folder / f"harness-{uuid.uuid4().hex}.ps1"
    harness.write_text(_PRELUDE + "\n" + body + "\n", encoding="utf-8")
    try:
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(harness), "-Script", str(script)],
            capture_output=True, text=True, timeout=timeout)
    finally:
        harness.unlink(missing_ok=True)


def run_script(script: Path, args: list[str], *, timeout: int = 180,
               env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the whole script (its real body) with arguments."""
    if not POWERSHELL:
        raise RuntimeError("Windows PowerShell is not available")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), *args],
        capture_output=True, text=True, timeout=timeout, env=env)


def ps_quote(value) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"
