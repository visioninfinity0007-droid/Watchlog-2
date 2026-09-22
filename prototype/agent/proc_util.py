#!/usr/bin/env python3
"""One hardened way to run a child process with a deadline we can actually enforce.

THIS EXISTS BECAUSE THE OBVIOUS WAY IS WRONG, AND WE GOT IT WRONG TWICE.

``subprocess.run(capture_output=True, timeout=N)`` does not bound anything when the
child leaves a survivor holding the pipe. On timeout CPython kills the process it
started and then calls ``communicate()`` A SECOND TIME WITH NO TIMEOUT to drain the
pipes; any grandchild still holding the write end keeps that drain waiting on the
SURVIVOR rather than on our deadline. MEASURED: a 3s deadline returned after 25.6s
against a 25s survivor, and against a survivor that never exits it never returns.

That is not academic. It shipped twice:
  * 0.4.5 — the installer hung forever on "Running final acceptance checks" because
    watchlog-agent.exe is a PyInstaller --onefile build, so the process we launch is
    only a bootloader and the real Python child outlived the kill.
  * 0.4.7 — fixed in status_controller, then immediately REINTRODUCED in
    setup_backend.ensure_background_agent, which hung the wizard on step 06 again.

So the hardened implementation lives here once and both callers use it. Writing to a
FILE instead of a pipe removes the deadlock outright; killing the whole process TREE
removes the survivor.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def kill_tree(proc) -> None:
    """Kill the child AND everything it spawned."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=20)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 — best effort; the direct kill below still runs
        pass
    for finish in (proc.kill, lambda: proc.wait(timeout=10)):
        try:
            finish()
        except Exception:  # noqa: BLE001
            pass


def _emit_new_lines(path: Path, seen: int, on_output, skip_prefix: str | None) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return seen
    lines = text.splitlines()
    for line in lines[seen:]:
        stripped = line.strip()
        if stripped and not (skip_prefix and stripped.startswith(skip_prefix)):
            try:
                on_output(stripped)
            except Exception:  # noqa: BLE001 — a UI callback must never break the run
                pass
    return len(lines)


def run_bounded(cmd: list, timeout: float, *, on_output=None,
                skip_prefix: str | None = None) -> tuple:
    """Run ``cmd``, return ``(returncode, combined_output)``, and ACTUALLY honour ``timeout``.

    Returns ``-1`` as the code when the deadline was hit. Output written before the
    timeout is still returned. ``on_output`` is called with each new whole line while
    the process runs, for live progress; ``skip_prefix`` suppresses machine-readable
    lines from that stream.

    Never raises for a misbehaving child — a caller that wanted a bounded run must not
    be handed an unbounded exception path instead.
    """
    deadline = time.monotonic() + timeout
    workdir = tempfile.mkdtemp(prefix="wl-run-")
    code = -1
    text = ""
    try:
        sink_path = Path(workdir) / "output.txt"
        with sink_path.open("w", encoding="utf-8", errors="replace") as sink:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            try:
                proc = subprocess.Popen(cmd, stdout=sink, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, **kwargs)
            except OSError as exc:
                # Missing binary / bad path. A caller that asked for a bounded run must
                # not be handed an unbounded exception path instead.
                return -1, f"could not start {cmd[0]!r}: {exc}"
            seen = 0
            while True:
                try:
                    code = proc.wait(timeout=0.4)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if on_output is not None:
                    seen = _emit_new_lines(sink_path, seen, on_output, skip_prefix)
                if time.monotonic() >= deadline:
                    kill_tree(proc)
                    code = -1
                    break
        try:
            text = sink_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    finally:
        # NOT TemporaryDirectory(): if a survivor still holds the log open its cleanup
        # raises WinError 32, trading a hang for a crash.
        shutil.rmtree(workdir, ignore_errors=True)
    return code, text


__all__ = ["run_bounded", "kill_tree"]
