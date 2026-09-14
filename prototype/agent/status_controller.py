#!/usr/bin/env python3
"""Site Status controller — the testable brain behind the panel's data + action buttons (P2/P3).

The Qt "WatchLog Site Status" window is a thin view; this controller holds all the logic. Rather
than re-implement anything, it DRIVES the installed agent's already-proven CLI (--status-json,
--accept, --check-update, --update, --support-bundle) and parses the machine-readable lines they
print. The customer gets buttons; the buttons run the same commands support would run by hand.

Every command runs through an injected ``run_agent(args) -> (exit_code, stdout)`` so the whole
controller — including the update apply/rollback and support-bundle flows — is unit-testable with
no subprocess, no recorder and no cloud. Nothing here parses or prints a secret.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def _programdata_watchlog() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog"


class StatusController:
    def __init__(self, *, run_agent=None, agent_cmd=None, timeout: int = 90,
                 log_dir=None):
        self._run = run_agent or self._default_runner
        self._agent_cmd = list(agent_cmd) if agent_cmd else self._resolve_agent_cmd()
        self._timeout = timeout
        self._log_dir = Path(log_dir) if log_dir else _programdata_watchlog()

    # --- command plumbing ---------------------------------------------------
    @staticmethod
    def _resolve_agent_cmd() -> list:
        """The installed appliance runs watchlog-agent.exe beside the setup exe; from source we run
        the module. (Only used by the default subprocess runner; tests inject run_agent.)"""
        if getattr(sys, "frozen", False):
            exe = Path(sys.executable).resolve().parent / "watchlog-agent.exe"
            if exe.exists():
                return [str(exe)]
        return [sys.executable, str(Path(__file__).resolve().parent / "watchlog_agent.py")]

    def _default_runner(self, args, timeout=None):  # pragma: no cover - real subprocess path
        import subprocess
        proc = subprocess.run(self._agent_cmd + list(args), capture_output=True, text=True,
                              timeout=timeout or self._timeout)
        return proc.returncode, (proc.stdout or "")

    @staticmethod
    def _tagged(stdout: str, tag: str):
        for line in (stdout or "").splitlines():
            if line.startswith(tag + " "):
                try:
                    return json.loads(line[len(tag) + 1:])
                except Exception:  # noqa: BLE001
                    return None
        return None

    # --- data ---------------------------------------------------------------
    def snapshot(self) -> dict:
        rc, out = self._run(["--status-json"])
        return {"ok": rc == 0, "status": self._tagged(out, "STATUS_JSON")}

    # focused refresh actions reuse the snapshot (which probes recorder/cameras/archive)
    def test_recorder(self) -> dict:
        snap = self.snapshot().get("status") or {}
        return {"ok": (snap.get("recorder", {}).get("connection") == "connected"),
                "recorder": snap.get("recorder")}

    def rediscover_cameras(self) -> dict:
        snap = self.snapshot().get("status") or {}
        return {"ok": True, "cameras": snap.get("cameras")}

    def recheck_recording(self) -> dict:
        snap = self.snapshot().get("status") or {}
        return {"ok": True, "recording": snap.get("recording")}

    def recheck_archive(self) -> dict:
        snap = self.snapshot().get("status") or {}
        return {"ok": True, "archive": snap.get("archive")}

    # --- actions ------------------------------------------------------------
    def run_acceptance(self) -> dict:
        rc, out = self._run(["--accept"])
        report = self._tagged(out, "ACCEPTANCE_JSON")
        checks = (report or {}).get("checks", []) or []
        failed = [c.get("label") or c.get("key") for c in checks
                  if c.get("hard") and c.get("status") != "pass"]
        warnings = [c.get("label") or c.get("key") for c in checks if c.get("status") == "warn"]
        if rc == 0:
            verdict = "WATCHLOG READY WITH WARNINGS" if warnings else "WATCHLOG READY"
        else:
            verdict = "SETUP INCOMPLETE — ACTION REQUIRED"
        return {"exit": rc, "ready": rc == 0, "verdict": verdict,
                "failed": failed, "warnings": warnings, "report": report}

    def check_update(self) -> dict:
        rc, out = self._run(["--check-update"])
        plan = self._tagged(out, "UPDATE_JSON") or {}
        action = plan.get("action")
        if action == "update":
            headline = f"WatchLog {plan.get('target')} is available."
        elif action == "up-to-date":
            headline = "WatchLog is up to date."
        else:
            headline = "Update status could not be confirmed."
        return {"exit": rc, "action": action, "headline": headline, "plan": plan}

    def apply_update(self) -> dict:
        rc, out = self._run(["--update"])
        result = self._tagged(out, "UPDATE_APPLY_JSON") or {}
        if result.get("ok"):
            message = f"WatchLog updated to {result.get('detail', 'the new version')}."
        elif result.get("rolled_back"):
            message = "Update failed. WatchLog restored the previous version and monitoring has resumed."
        else:
            message = "Update could not be completed."
        return {"exit": rc, "ok": bool(result.get("ok")), "rolled_back": bool(result.get("rolled_back")),
                "message": message, "result": result}

    def export_support_bundle(self, dest_path=None) -> dict:
        rc, out = self._run(["--support-bundle"])
        src = None
        for line in (out or "").splitlines():
            if line.startswith("support bundle written:"):
                src = line.split(":", 1)[1].strip()
                break
        if rc != 0 or not src:
            return {"ok": False, "path": None, "detail": "Support bundle could not be created."}
        final = src
        if dest_path:
            try:
                shutil.copy2(src, str(dest_path))
                final = str(dest_path)
            except Exception:  # noqa: BLE001
                final = src
        return {"ok": True, "path": final}

    def restart_agent(self, _runner=None) -> dict:
        """Restart the background agent task safely (Windows scheduled task). Injected for tests."""
        runner = _runner or self._default_task_restart
        try:
            ok = runner()
            return {"ok": bool(ok), "detail": "WatchLog Agent restarted." if ok else
                    "WatchLog Agent could not be restarted."}
        except Exception:  # noqa: BLE001
            return {"ok": False, "detail": "WatchLog Agent could not be restarted."}

    def _default_task_restart(self):  # pragma: no cover - Windows appliance path
        import subprocess
        task = "WatchLog Agent"
        subprocess.run(["schtasks", "/End", "/TN", task], capture_output=True)
        rc = subprocess.run(["schtasks", "/Run", "/TN", task], capture_output=True).returncode
        return rc == 0

    def open_logs(self) -> dict:
        """Return the sanitized log/support location for the GUI to open (no secrets are there)."""
        return {"ok": self._log_dir.exists() or True, "path": str(self._log_dir)}


__all__ = ["StatusController"]
