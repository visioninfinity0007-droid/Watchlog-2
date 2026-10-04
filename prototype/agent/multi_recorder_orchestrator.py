"""Multi-recorder cloud binding and preflight.

This module deliberately stops before starting any worker thread. Its job is to:
1. bind every configured local recorder UUID to one cloud recorder UUID;
2. persist that mapping fail-closed;
3. probe each recorder independently;
4. sync that recorder's own cameras and capabilities explicitly.

Only after this completes may the later runtime fan-out start collector/health/recovery
workers. That prevents a thread from producing recorder-scoped events before its
cloud recorder identity exists.
"""
from __future__ import annotations

from dataclasses import dataclass

import recorder_registry
import recorder_runtime


MULTI_RECORDER_CONTRACT_VERSION = 4
MULTI_RECORDER_REQUIRED_FEATURES = frozenset({
    "recorders",
    "recorder_cameras",
    "recorder_events",
    "recorder_health",
    "recorder_recovery",
    "recorder_reconciliation",
    "recorder_capabilities",
    "recorder_job_routing",
    "recorder_analytics",
    "recorder_continuity",
})


@dataclass
class PreparedRecorder:
    context: recorder_runtime.RecorderContext
    device: object | None
    channels: list[dict]
    capabilities: dict | None
    camera_mapping: dict | None
    error: str | None = None


def require_cloud_contract(cloud, state: dict) -> dict:
    """Require the complete DB contract before any recorder cutover mutation.

    Contract v2 is introduced by the camera-job routing migration. Running a
    multi-recorder Agent against only part of the DB stack would make some
    recorder-dependent actions ambiguous, so preflight fails closed instead.
    """
    contract = cloud.call(
        "wl_multi_recorder_agent_contract",
        p_agent_id=state["agent_id"],
        p_agent_key=state["agent_key"],
    )
    if not isinstance(contract, dict) or not contract.get("ok"):
        raise RuntimeError("multi-recorder cloud contract unavailable")
    version = int(contract.get("version") or 0)
    features = set(contract.get("features") or [])
    if version != MULTI_RECORDER_CONTRACT_VERSION:
        raise RuntimeError(
            f"multi-recorder cloud contract version {version} is not supported"
        )
    missing = sorted(MULTI_RECORDER_REQUIRED_FEATURES - features)
    if missing:
        raise RuntimeError(
            "multi-recorder cloud contract missing: " + ", ".join(missing)
        )
    return contract


def _apply_mapping(contexts, mapping: dict) -> None:
    recorder_registry.apply_cloud_mapping(mapping)
    by_local = {ctx.local_id: ctx for ctx in contexts}
    for local_id, cloud_id in mapping.items():
        ctx = by_local[str(local_id)]
        cloud_id = str(cloud_id)
        ctx.cloud_recorder_id = cloud_id
        ctx.config.recorder_cloud_id = cloud_id
        ctx.holder["recorder_cloud_id"] = cloud_id


def _sync_identity_payload(cloud, state: dict, contexts) -> dict:
    mapping = cloud.call(
        "wl_sync_recorders",
        p_agent_id=state["agent_id"],
        p_agent_key=state["agent_key"],
        p_recorders=[ctx.cloud_descriptor() for ctx in contexts],
    )
    if not isinstance(mapping, dict):
        raise RuntimeError("recorder sync returned no identity mapping")
    expected = {ctx.local_id for ctx in contexts}
    returned = {str(k) for k in mapping}
    if returned != expected:
        raise RuntimeError("recorder sync mapping did not exactly match configured recorders")
    return mapping


def _sync_registry_state(cloud, state: dict) -> dict:
    """Synchronize the complete non-secret registry, including disabled rows.

    Worker contexts intentionally contain only configured recorders. Lifecycle
    state cannot use that filtered set or a disabled cloud recorder would remain
    configured forever. The server mapping must exactly cover every local row so
    local/cloud identity drift fails closed.
    """
    payload = recorder_registry.registry_cloud_descriptors()
    if not payload:
        return {}
    mapping = cloud.call(
        "wl_sync_recorders",
        p_agent_id=state["agent_id"],
        p_agent_key=state["agent_key"],
        p_recorders=payload,
    )
    if not isinstance(mapping, dict):
        raise RuntimeError("recorder registry sync returned no identity mapping")
    expected = {str(row["local_key"]) for row in payload}
    returned = {str(k) for k in mapping}
    if returned != expected:
        raise RuntimeError(
            "recorder registry sync mapping did not exactly match local registry"
        )
    recorder_registry.apply_cloud_mapping(mapping)
    return mapping


def _stamp_legacy_spool(cfg, recorder_id: str) -> int:
    """Attribute queued singleton events to the immutable continuity recorder."""
    if not recorder_id:
        raise RuntimeError("continuity recorder cloud identity is not bound")
    from spool import Spool
    spool = Spool(cfg.spool_path, cfg.spool_max_rows)
    try:
        return spool.stamp_missing_recorder_id(str(recorder_id))
    finally:
        spool.close()


def _stamp_legacy_health(cfg, recorder_id: str, state: dict) -> dict:
    """Attribute singleton health history to the immutable continuity recorder."""
    if not recorder_id:
        raise RuntimeError("continuity recorder cloud identity is not bound")
    from pathlib import Path
    path = Path(cfg.health_store_path)
    if not path.exists():
        return {
            "transitions": 0,
            "checkpoints": 0,
            "last_state": 0,
            "recorder_id": str(recorder_id),
        }

    import health_store
    store = health_store.HealthStore(path, agent_id=state["agent_id"])
    try:
        return store.stamp_missing_recorder_id(str(recorder_id))
    finally:
        store.close()


def bind_cloud_identities(cloud, state: dict,
                          contexts: list[recorder_runtime.RecorderContext],
                          base_cfg=None) -> dict:
    """Bind every configured recorder before workers start.

    The immutable continuity recorder is bound/stamped before any secondary can
    exist. Preferred-primary designation may later move, but continuity never
    follows it.
    """
    if not contexts:
        return {}

    primaries = [ctx for ctx in contexts if ctx.is_primary]
    if len(primaries) != 1:
        raise RuntimeError("multi-recorder cutover requires exactly one primary")

    registry_rows = recorder_registry.recorders()
    continuity_rows = [
        row for row in registry_rows if row.get("continuity_owner")
    ]
    if len(continuity_rows) != 1:
        raise RuntimeError("multi-recorder registry requires exactly one continuity owner")
    continuity_row = continuity_rows[0]
    continuity = next(
        (ctx for ctx in contexts if ctx.local_id == continuity_row["local_id"]),
        None,
    )

    if continuity is not None:
        if not continuity.cloud_recorder_id:
            # Before first cloud binding the continuity owner must still be the
            # preferred primary. Primary reassignment is blocked locally until
            # every configured recorder has a cloud identity.
            if not continuity.is_primary:
                raise RuntimeError(
                    "unbound continuity recorder must remain primary during cutover"
                )
            continuity_mapping = _sync_identity_payload(cloud, state, [continuity])
            _apply_mapping(contexts, continuity_mapping)
        continuity_id = str(continuity.cloud_recorder_id or "")
        _stamp_legacy_spool(continuity.config, continuity_id)
        _stamp_legacy_health(continuity.config, continuity_id, state)
    else:
        # The continuity owner may be deliberately disabled after a safe primary
        # switch. Its historical singleton paths still belong to it and must
        # never be stamped with the new preferred primary's UUID.
        continuity_id = str(continuity_row.get("cloud_recorder_id") or "")
        if not continuity_id:
            raise RuntimeError(
                "disabled continuity recorder must already be cloud-bound"
            )
        if base_cfg is None:
            raise RuntimeError(
                "base config required to preserve disabled continuity history"
            )
        _stamp_legacy_spool(base_cfg, continuity_id)
        _stamp_legacy_health(base_cfg, continuity_id, state)

    mapping = _sync_identity_payload(cloud, state, contexts)
    _apply_mapping(contexts, mapping)

    # Propagate the complete lifecycle state, including disabled historical rows
    # and the current preferred-primary designation.
    _sync_registry_state(cloud, state)
    return mapping


def _device_fact(device, name):
    value = getattr(device, name, None) if device is not None else None
    return str(value).strip() if value not in (None, "") else None


def probe_and_sync_recorder(cloud, state: dict,
                            ctx: recorder_runtime.RecorderContext,
                            open_driver_fn) -> PreparedRecorder:
    """Probe + explicitly sync one recorder. Failure is recorder-local."""
    if not ctx.cloud_recorder_id:
        raise RuntimeError("recorder cloud identity is not bound")

    driver = None
    try:
        driver, device = open_driver_fn(ctx.config)
        channels = [
            {"channel": str(ch.channel), "name": getattr(ch, "name", None)}
            for ch in driver.list_channels()
        ]
        try:
            capabilities = driver.capabilities()
        except Exception:
            capabilities = None

        recorder_registry.update_observed_identity(
            ctx.local_id,
            vendor=_device_fact(device, "vendor"),
            model=_device_fact(device, "model"),
            firmware=_device_fact(device, "firmware"),
            driver=getattr(driver, "name", None),
            identity_fingerprint=(
                f"serial:{_device_fact(device, 'serial')}"
                if _device_fact(device, "serial") else None
            ),
        )

        camera_mapping = cloud.call(
            "wl_sync_recorder_cameras",
            p_agent_id=state["agent_id"],
            p_agent_key=state["agent_key"],
            p_recorder_id=ctx.cloud_recorder_id,
            p_cameras=channels,
        )
        if not isinstance(camera_mapping, dict):
            raise RuntimeError("recorder camera sync returned no mapping")

        if capabilities and capabilities.get("channels"):
            cloud.call(
                "wl_sync_recorder_capabilities",
                p_agent_id=state["agent_id"],
                p_agent_key=state["agent_key"],
                p_recorder_id=ctx.cloud_recorder_id,
                p_capabilities=capabilities,
            )

        ctx.vendor = _device_fact(device, "vendor") or ctx.vendor
        ctx.model = _device_fact(device, "model") or ctx.model
        ctx.firmware = _device_fact(device, "firmware") or ctx.firmware
        return PreparedRecorder(
            context=ctx,
            device=device,
            channels=channels,
            capabilities=capabilities,
            camera_mapping=camera_mapping,
        )
    except Exception as exc:
        return PreparedRecorder(
            context=ctx,
            device=None,
            channels=[],
            capabilities=None,
            camera_mapping=None,
            error=f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}",
        )
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass


def prepare_recorders(base_cfg, state: dict, cloud, open_driver_fn) -> list[PreparedRecorder]:
    """Bind all identities first, then independently probe/sync each recorder."""
    contexts = recorder_runtime.load_contexts(base_cfg)
    if not contexts:
        return []

    require_cloud_contract(cloud, state)
    bind_cloud_identities(cloud, state, contexts, base_cfg=base_cfg)
    return [
        probe_and_sync_recorder(cloud, state, ctx, open_driver_fn)
        for ctx in contexts
    ]
