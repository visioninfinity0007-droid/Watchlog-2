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
  * --requires-repair (a human decision, e.g. a Setup UI or registry-format change the old
    Setup UI cannot handle)                                          -> REQUIRES_REPAIR_PACKAGE;
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


def classify(version: str, previous_version: str | None, changed_scripts: list[str] | None, *,
             requires_repair: str = "", min_installed_components: str = "",
             build_sha: str = "") -> dict:
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


def changed_component_scripts(previous_ref: str, ref: str) -> list[str] | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", previous_ref, ref, "--",
                              *COMPONENT_SCRIPTS], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


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
    args = ap.parse_args(argv)
    changed = changed_component_scripts(args.previous_ref, args.ref) if args.previous_ref else None
    contract = classify(args.version, args.previous_version or None, changed,
                        requires_repair=args.requires_repair,
                        min_installed_components=args.min_installed_components,
                        build_sha=args.build_sha)
    text = json.dumps(contract, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
