#!/usr/bin/env python3
"""The packaged Agent refuses the console setup wizard (legacy installer path audit, item 6).

watchlog-agent.exe --setup, and the automatic wizard when no recorder is configured, wrote the
recorder password in plain text into Program Files\\WatchLog\\watchlog.ini, replaced the whole
INI and ignored the recorder registry. WatchLog Setup is the only configuration path, so the
packaged entry point (release_agent.py) must refuse both before asking or writing anything, and
exit non-zero. Each case runs the real entry point in a child process with an isolated config
directory and a closed stdin (a wizard that still ran would fail on its first prompt).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"

CHILD = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
home = Path(sys.argv[2])
import agent_core, watchlog_agent
agent_core.base_dir = lambda: home
watchlog_agent.base_dir = lambda: home
import release_agent
sys.argv = ["watchlog-agent.exe"] + sys.argv[3:]
release_agent.main()
"""


def _run(tmp_path, ini_text, *args):
    home = tmp_path / "Program Files" / "WatchLog"
    home.mkdir(parents=True)
    if ini_text is not None:
        (home / "watchlog.ini").write_text(ini_text, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("WATCHLOG_")}
    env["PROGRAMDATA"] = str(tmp_path / "ProgramData")
    proc = subprocess.run([sys.executable, "-c", CHILD, str(AGENT), str(home), *args],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
                          env=env)
    return proc, home


INI = "[watchlog]\nsupabase_url = https://example.invalid\nsupabase_publishable_key = k\n"


@pytest.mark.parametrize("args,ini", [(["--setup"], INI),          # explicit
                                      ([], INI),                   # automatic: no recorder
                                      (["--setup"], None)])        # no INI at all
def test_the_packaged_agent_refuses_the_console_wizard_and_writes_nothing(tmp_path, args, ini):
    proc, home = _run(tmp_path, ini, *args)
    assert proc.returncode == 2, (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
    assert "WatchLog Setup" in proc.stderr
    ini_path = home / "watchlog.ini"
    if ini is None:
        assert not ini_path.exists()
    else:
        assert ini_path.read_text(encoding="utf-8") == ini
    assert "password" not in proc.stdout.lower()       # never prompted


def test_the_legacy_installers_that_drove_the_wizard_are_gone():
    installer = AGENT.parent / "installer"
    for name in ("Install-WatchLog.ps1", "Install WatchLog.cmd", "Uninstall-WatchLog.ps1",
                 "run-agent.cmd"):
        assert not (installer / name).exists(), name


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
