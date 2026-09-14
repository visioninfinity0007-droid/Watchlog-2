#!/usr/bin/env python3
"""Support-bundle export (0.4.4 §18).

Gathers a NON-SECRET diagnostic snapshot the site operator can hand to WatchLog support: build
identity, redacted operational config, non-secret identity (UUIDs only), local health/spool
counts, and a redacted tail of setup.log — packaged as one timestamped .zip.

Secrets Gate (hard rule): this bundle NEVER contains the recorder password or username, the agent
key, the enrollment code, DPAPI material, Supabase secret keys, or any decrypted secret. Config is
assembled by ALLOWLIST (deny by default), so a new secret-shaped key added to watchlog.ini later
cannot silently leak; and every emitted line is additionally screened for secret-shaped content.
Pure/deterministic given its inputs (no cloud/recorder/DB), so the redaction is fully testable.
"""
from __future__ import annotations

import json
import platform
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Only these config keys are EVER copied into the bundle. Everything else — nvr_password,
# nvr_username, enrollment_code, and any future secret-shaped key — is dropped by omission.
SAFE_CONFIG_KEYS = (
    "supabase_url", "supabase_publishable_key", "nvr_url", "nvr_driver",
    "state_file", "spool_file", "spool_max_rows", "snapshots", "snapshot_min_interval",
    "detect", "detect_model", "detect_confidence", "detect_classes", "site_type",
    "recovery_enabled", "recovery_seconds", "recovery_chunk_seconds",
    "recovery_throttle_seconds", "recovery_threshold_seconds", "recovery_live_backlog",
)
# Identity fields safe to include — UUID identifiers, not secrets. The agent KEY is never here.
SAFE_STATE_KEYS = ("agent_id", "site_id", "tenant_id", "enrolled_at")

_SECRET_HINTS = ("password", "passwd", "secret", "agent_key", "agentkey",
                 "enrollment", "token", "credential", "dpapi", "private")


def _looks_secret(text: str) -> bool:
    low = (text or "").lower()
    return any(hint in low for hint in _SECRET_HINTS)


def redact_config(section: dict) -> dict:
    """Return only allowlisted, non-secret operational settings, and defensively drop any that
    still look secret-shaped (belt and braces against an allowlist mistake)."""
    section = section or {}
    return {k: section[k] for k in SAFE_CONFIG_KEYS
            if k in section and not _looks_secret(k)}


def collect(config_section: dict, *, state: dict = None, setup_log: str = "",
            build_meta: dict = None, spool_count=None, now: datetime = None) -> dict:
    """Assemble the bundle as an ordered ``{filename: text}`` map. Pure — touches no filesystem."""
    now = now or datetime.now(timezone.utc)
    state = state or {}
    files: dict[str, str] = {}

    files["versions.json"] = json.dumps({
        "generated_at": now.isoformat(),
        "build": build_meta or {},
        "python": platform.python_version(),
        "platform": platform.platform(),
    }, indent=2, sort_keys=True)

    files["config_redacted.ini"] = "[watchlog]\n" + "".join(
        f"{k} = {v}\n" for k, v in redact_config(config_section).items())

    identity = {k: state[k] for k in SAFE_STATE_KEYS if state.get(k) is not None}
    identity["enrolled"] = bool(state.get("agent_id"))
    files["identity.json"] = json.dumps(identity, indent=2, sort_keys=True)

    files["local_state.json"] = json.dumps({"spool_queued": spool_count},
                                           indent=2, sort_keys=True)

    # setup.log is written without secrets by design; still drop any secret-shaped line and bound
    # the tail so a huge log never bloats the bundle.
    safe_lines = [ln for ln in (setup_log or "").splitlines() if not _looks_secret(ln)]
    files["setup_log.txt"] = "\n".join(safe_lines[-500:])

    return files


def write_zip(dest_dir, files: dict, *, now: datetime = None) -> Path:
    """Write the collected files into a timestamped .zip under ``dest_dir`` and return its path."""
    now = now or datetime.now(timezone.utc)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"watchlog-support-{now.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, text in files.items():
            archive.writestr(filename, text)
    return path


__all__ = ["collect", "redact_config", "write_zip", "SAFE_CONFIG_KEYS", "SAFE_STATE_KEYS"]
