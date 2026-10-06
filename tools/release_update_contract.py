#!/usr/bin/env python3
"""Compute a release's Agent-only vs Repair-package contract for the signed update manifest.

Every in-app update path (remote update, Site Status) replaces watchlog-agent.exe ONLY. The
launcher and updater scripts, the Setup UI, the uninstaller and the registry format stay as the
last full installer or Repair/Upgrade package left them. The release manifest entry therefore
carries (see prototype/agent/updater.py):

  update_class             AGENT_ONLY_COMPATIBLE | REQUIRES_REPAIR_PACKAGE
  min_installed_components oldest installed component set the new Agent can run beside
  min_agent_version        (REQUIRES_REPAIR_PACKAGE only) the release's own version, so Agents
                           older than 5.1.1, which ignore update_class, refuse it too

This tool decides them from facts, conservatively:

  * a major/minor change from the release currently on the channel  -> REQUIRES_REPAIR_PACKAGE;
  * any change since that release to a script installed beside the Agent (run-agent.ps1,
    apply-remote-update.ps1, register-service.ps1, wl-upgrade.ps1)   -> REQUIRES_REPAIR_PACKAGE
    (an Agent-only update would leave the old script running: e.g. 5.1.1's remote-update
    stage-trust gate and commit gate live in those scripts);
  * the previous release or its source unknown                       -> REQUIRES_REPAIR_PACKAGE;
  * a changed Setup UI source (SETUP_UI_SOURCES; the Setup UI is its own installed exe)
                                                                     -> REQUIRES_REPAIR_PACKAGE;
  * a changed data format the Setup UI/installers write and the Agent reads (recorder registry
    schema, credential store format, per-recorder camera choices; CONTRACT_CONSTANTS)
                                                                     -> REQUIRES_REPAIR_PACKAGE;
  * --requires-repair (a human decision for anything else the old components cannot handle)
                                                                     -> REQUIRES_REPAIR_PACKAGE;
  * otherwise AGENT_ONLY_COMPATIBLE, with min_installed_components = "<major>.<minor>.0" of
    this release unless --min-installed-components says otherwise.

Usage (release workflow, after the build):
  python tools/release_update_contract.py --version 5.1.1 --previous-version 5.1.0 \\
      --previous-ref <source sha of 5.1.0> --ref "$GITHUB_SHA" --build-sha "$GITHUB_SHA" \\
      --out WatchLog-Release.update-contract.json

The output is merged into the channel entry of the signed manifest by whoever builds it (the
watchlog-update-manifest edge function); this tool signs and publishes nothing.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))

import updater  # noqa: E402

COMPONENT_SCRIPTS = (
    "prototype/installer/run-agent.ps1",
    "prototype/installer/apply-remote-update.ps1",
    "prototype/installer/register-service.ps1",
    "prototype/installer/nsis/wl-upgrade.ps1",
)

# watchlog-setup-ui.exe is a separate installed component; an Agent-only update leaves the old
# one in place, so any change to what it is built from needs the Repair/Upgrade package.
SETUP_UI_SOURCES = (
    "prototype/agent/setup_gui.py",
    "prototype/agent/setup_backend.py",
    "prototype/agent/site_status_gui.py",
    "prototype/agent/status_controller.py",
    "prototype/agent/build_setup_gui.ps1",
    "prototype/packaging/requirements-setup-ui.lock",
)

# Data formats an Agent-only update cannot change by itself: the Setup UI (Manage Recorders) and
# the installers write them, the Agent reads them. A different declared value (or one that did
# not exist in the previous release) means the new Agent needs the matching Setup UI/installer.
CONTRACT_CONSTANTS = (
    ("prototype/agent/recorder_registry.py", "REGISTRY_SCHEMA", "recorder registry schema"),
    ("prototype/agent/windows_secret.py", "CREDENTIAL_STORE_VERSION", "credential store format"),
    ("prototype/agent/periodic_stills.py", "RECORDER_CAMERA_PROFILES_SCHEMA",
     "per-recorder camera choices format"),
)


def classify(version: str, previous_version: str | None, changed_scripts: list[str] | None, *,
             requires_repair: str = "", min_installed_components: str = "",
             build_sha: str = "", changed_formats: list[str] | None = (),
             changed_setup_ui: list[str] | None = ()) -> dict:
    """The manifest fields for ``version``. ``changed_scripts`` None = unknown (conservative)."""
    target = updater.parse_version(version)
    reasons: list[str] = []
    if not previous_version:
        reasons.append("the release currently on the channel is unknown")
    elif updater.parse_version(previous_version)[:2] != target[:2]:
        reasons.append(f"major/minor change from {previous_version} to {version}")
    if changed_scripts is None:
        reasons.append("installed scripts could not be compared with the previous release")
    elif changed_scripts:
        reasons.append("scripts installed beside the Agent changed: " + ", ".join(sorted(changed_scripts)))
    if changed_setup_ui is None:
        reasons.append("the Setup UI could not be compared with the previous release")
    elif changed_setup_ui:
        reasons.append("the Setup UI changed: " + ", ".join(sorted(changed_setup_ui)))
    if changed_formats is None:
        reasons.append("installed data formats could not be compared with the previous release")
    elif changed_formats:
        reasons.append("data formats written by Setup/installers changed: "
                       + ", ".join(sorted(changed_formats)))
    if requires_repair:
        reasons.append(requires_repair)
    update_class = updater.REQUIRES_REPAIR_PACKAGE if reasons else updater.AGENT_ONLY_COMPATIBLE
    contract = {
        "version": version,
        "update_class": update_class,
        "min_installed_components": min_installed_components or f"{target[0]}.{target[1]}.0",
        "build_sha": build_sha or None,
        "reasons": reasons or ["patch release; no installed script changed"],
    }
    if update_class == updater.REQUIRES_REPAIR_PACKAGE:
        # Agents older than 5.1.1 ignore update_class but honour min_agent_version: naming
        # this release's own version makes every older Agent refuse it ("agent_too_old")
        # instead of installing it Agent-only.
        contract["min_agent_version"] = version
    return contract


def changed_component_scripts(previous_ref: str, ref: str,
                              paths: tuple[str, ...] = COMPONENT_SCRIPTS) -> list[str] | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", previous_ref, ref, "--",
                              *paths], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _constant(ref: str, path: str, name: str):
    """The literal value of NAME in PATH at REF; None when absent; raises when unreadable."""
    import re
    out = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{path}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        if "does not exist" in out.stderr or "exists on disk, but not in" in out.stderr:
            return None
        raise OSError(out.stderr.strip())
    match = re.search(rf"^{name}\s*=\s*(.+?)\s*(?:#.*)?$", out.stdout, re.M)
    return match.group(1).strip() if match else None


def changed_contract_formats(previous_ref: str, ref: str) -> list[str] | None:
    try:
        return [label for path, name, label in CONTRACT_CONSTANTS
                if _constant(previous_ref, path, name) != _constant(ref, path, name)]
    except OSError:
        return None


# The deployed watchlog-update-manifest edge function reads watchlog-update-payload.json from this
# release and accepts only Agent assets under this prefix; it signs version, url, sha256, size,
# notes, generated_at and min_agent_version. It drops the other fields today; they are included
# for the day it passes them through. A REQUIRES_REPAIR_PACKAGE release is still enforced now:
# its min_agent_version is its own version, which every fielded Agent (5.0.17+) refuses.
RELEASE_ASSET_BASE = "https://github.com/visioninfinity0007-droid/Watchlog-2/releases/download"


def update_payload(contract: dict, bootstrap: dict, agent_sha256: str, agent_size: int,
                   generated_at: str) -> dict:
    """watchlog-update-payload.json for this release. Writing it publishes nothing."""
    version = contract["version"]
    floor = str(bootstrap["min_remote_update_version"])
    min_agent = str(contract.get("min_agent_version") or floor)
    if updater.parse_version(min_agent) < updater.parse_version(floor):
        min_agent = floor
    return {
        "version": version,
        "url": f"{RELEASE_ASSET_BASE}/{bootstrap['release_tag']}/watchlog-agent-{version}.exe",
        "sha256": agent_sha256.lower(),
        "size": int(agent_size),
        "notes": f"WatchLog {version} production Site Connector",
        "generated_at": generated_at,
        "min_agent_version": min_agent,
        "update_class": contract["update_class"],
        "min_installed_components": contract["min_installed_components"],
        "build_sha": contract.get("build_sha"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--version", required=True)
    ap.add_argument("--previous-version", default="")
    ap.add_argument("--previous-ref", default="", help="source commit of the previous release")
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--requires-repair", default="", metavar="REASON")
    ap.add_argument("--min-installed-components", default="")
    ap.add_argument("--build-sha", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--agent", default="", help="built watchlog-agent.exe (for --payload-out)")
    ap.add_argument("--bootstrap", default=str(ROOT / "prototype" / "update" / "production.json"))
    ap.add_argument("--payload-out", default="",
                    help="write watchlog-update-payload.json (written only, never uploaded)")
    args = ap.parse_args(argv)
    changed = changed_component_scripts(args.previous_ref, args.ref) if args.previous_ref else None
    formats = changed_contract_formats(args.previous_ref, args.ref) if args.previous_ref else None
    setup_ui = (changed_component_scripts(args.previous_ref, args.ref, SETUP_UI_SOURCES)
                if args.previous_ref else None)
    contract = classify(args.version, args.previous_version or None, changed,
                        changed_formats=formats, changed_setup_ui=setup_ui,
                        requires_repair=args.requires_repair,
                        min_installed_components=args.min_installed_components,
                        build_sha=args.build_sha)
    text = json.dumps(contract, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.payload_out:
        import datetime
        import hashlib
        if not args.agent:
            ap.error("--payload-out needs --agent")
        data = Path(args.agent).read_bytes()
        payload = update_payload(
            contract, json.loads(Path(args.bootstrap).read_text(encoding="utf-8")),
            hashlib.sha256(data).hexdigest(), len(data),
            datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        Path(args.payload_out).write_text(json.dumps(payload, separators=(",", ":")) + "\n",
                                          encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
