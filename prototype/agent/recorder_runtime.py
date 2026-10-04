"""Recorder-scoped runtime configuration.

This module introduces the unit of isolation for multi-recorder execution without
changing the current Agent orchestration yet.

Each RecorderContext owns:
- one stable local recorder UUID;
- optional cloud recorder UUID;
- one decrypted credential;
- one recorder-specific Config copy;
- one independent runtime holder/state dictionary.

No recorder secret is copied into recorders.json or cloud payloads.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import credential_store
import recorder_registry


@dataclass
class RecorderContext:
    local_id: str
    cloud_recorder_id: str | None
    display_name: str
    config: object
    vendor: str | None = None
    model: str | None = None
    firmware: str | None = None
    identity_fingerprint: str | None = None
    is_primary: bool = False
    continuity_owner: bool = False
    holder: dict = field(default_factory=dict)

    def cloud_descriptor(self) -> dict:
        """Non-secret identity payload for wl_sync_recorders."""
        return {
            "local_key": self.local_id,
            "display_name": self.display_name,
            "vendor": self.vendor,
            "model": self.model,
            "driver": str(getattr(self.config, "nvr_driver", "") or ""),
            "firmware": self.firmware,
            "identity_fingerprint": self.identity_fingerprint,
            "is_primary": bool(self.is_primary),
            "is_configured": True,
        }

    def credential_generation(self) -> str:
        return credential_store.recorder_credential_generation(self.local_id)

    def reload_credential(self) -> None:
        cred = credential_store.load_recorder_credential(self.local_id)
        self.config.nvr_username = cred.get("username") or ""
        self.config.nvr_password = cred.get("password") or ""


def _bound_config(base_cfg, row: dict):
    """Copy the common Agent config, then override recorder-local fields only."""
    bound = copy.copy(base_cfg)
    cred = credential_store.load_recorder_credential(row["local_id"])
    bound.nvr_url = str(row.get("url") or "").rstrip("/")
    bound.nvr_driver = str(row.get("driver") or "auto").strip().lower() or "auto"
    bound.nvr_username = cred.get("username") or ""
    bound.nvr_password = cred.get("password") or ""
    bound.recorder_local_id = row["local_id"]
    bound.recorder_cloud_id = row.get("cloud_recorder_id")
    bound.recorder_display_name = row.get("display_name") or "Recorder"

    # Recorder-scoped durable state. At cutover the PRIMARY must preserve
    # every historical singleton path in place: moving its spool, health ledger
    # or last-live marker would strand queued evidence / lose the outage boundary.
    # Secondary recorders get independent state files.
    state_parent = Path(getattr(base_cfg, "state_path")).parent
    recorder_state = state_parent / "recorders" / row["local_id"]
    bound.recorder_state_dir = recorder_state
    if row.get("continuity_owner", row.get("is_primary")):
        bound.spool_path = Path(getattr(base_cfg, "spool_path"))
        bound.health_store_path = Path(
            getattr(base_cfg, "health_store_path", state_parent / "health.sqlite")
        )
        bound.last_live_path = Path(
            getattr(base_cfg, "last_live_path", state_parent / "last_live.json")
        )
    else:
        bound.spool_path = recorder_state / "spool.sqlite"
        bound.health_store_path = recorder_state / "health.sqlite"
        bound.last_live_path = recorder_state / "last_live.json"
    return bound


def load_contexts(base_cfg) -> list[RecorderContext]:
    """Load all configured recorders from recorders.json.

    The registry must already have been staged by Setup/upgrade code. Missing or
    corrupt per-recorder credentials fail closed; this function never falls back
    to the old singleton credential for one recorder while using the registry for
    another.
    """
    rows = [
        row for row in recorder_registry.recorders()
        if row.get("is_configured")
    ]
    if not rows:
        return []

    contexts: list[RecorderContext] = []
    seen_cloud: set[str] = set()
    for row in rows:
        cloud_id = row.get("cloud_recorder_id")
        if cloud_id:
            if cloud_id in seen_cloud:
                raise ValueError("duplicate cloud recorder id in local registry")
            seen_cloud.add(cloud_id)

        bound = _bound_config(base_cfg, row)
        contexts.append(RecorderContext(
            local_id=row["local_id"],
            cloud_recorder_id=cloud_id,
            display_name=row["display_name"],
            config=bound,
            vendor=row.get("vendor"),
            model=row.get("model"),
            firmware=row.get("firmware"),
            identity_fingerprint=row.get("identity_fingerprint"),
            is_primary=bool(row.get("is_primary")),
            continuity_owner=bool(
                row.get("continuity_owner", row.get("is_primary"))
            ),
            holder={
                "recorder_local_id": row["local_id"],
                "recorder_cloud_id": cloud_id,
                "recorder_display_name": row["display_name"],
            },
        ))

    primaries = [ctx for ctx in contexts if ctx.is_primary]
    if len(primaries) != 1:
        raise ValueError("configured recorder contexts require exactly one primary")
    return contexts


def stage_and_load_legacy(base_cfg, config_path) -> list[RecorderContext]:
    """Compatibility helper used during upgrade development.

    Stage the singleton into the new registry, then load recorder contexts. The
    old singleton remains untouched until runtime cutover is separately proven.
    """
    recorder_registry.migrate_legacy_singleton(config_path)
    return load_contexts(base_cfg)


def config_for_cloud_recorder(base_cfg, recorder_id: str | None):
    """Resolve a cloud job to exactly one local recorder Config.

    A missing recorder_id is accepted only while local identity is absent or
    unambiguously singleton. Multi-recorder jobs never fall back to "primary".
    """
    wanted = str(recorder_id or "").strip() or None
    base_cloud = str(getattr(base_cfg, "recorder_cloud_id", "") or "").strip() or None

    if wanted and base_cloud == wanted:
        return base_cfg

    contexts = load_contexts(base_cfg)

    if wanted:
        matches = [
            ctx for ctx in contexts
            if str(ctx.cloud_recorder_id or "") == wanted
        ]
        if len(matches) != 1:
            raise ValueError(
                "cloud recorder target is not mapped to exactly one local recorder"
            )
        return matches[0].config

    if len(contexts) > 1:
        raise ValueError("recorder_id required for multi-recorder job")
    if len(contexts) == 1:
        return contexts[0].config
    return base_cfg
