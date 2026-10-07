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

import threading
import time
from dataclasses import dataclass, field

import capability_sync
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


def _resolved_event() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


# Probes run side by side; recorders.json is read-modify-written, so their observed
# identity updates take turns.
_REGISTRY_WRITE_LOCK = threading.Lock()
# Site workers may already be reading recorders.json while a late probe writes it, and on
# Windows replacing a file another thread has open fails with PermissionError. The observed
# identity write is retried after these pauses, then skipped: it is a non-secret cache
# rewritten by the next probe, and must not cost the recorder its camera sync.
_IDENTITY_WRITE_RETRY_SECONDS = (0.05, 0.2, 0.5)


def _apply_cloud_mapping(mapping: dict) -> None:
    """Persist the cloud binding, retrying a recorders.json that is briefly busy.

    Unlike the observed-identity cache, the binding must be saved: a PermissionError that
    outlasts the pauses is raised (the caller treats it as transient, not a refusal)."""
    for pause in (*_IDENTITY_WRITE_RETRY_SECONDS, None):
        try:
            with _REGISTRY_WRITE_LOCK:
                recorder_registry.apply_cloud_mapping(mapping)
            return
        except PermissionError:
            if pause is None:
                raise
            time.sleep(pause)


def _save_observed_identity(local_id: str, **facts) -> None:
    for pause in (*_IDENTITY_WRITE_RETRY_SECONDS, None):
        try:
            with _REGISTRY_WRITE_LOCK:
                recorder_registry.update_observed_identity(local_id, **facts)
            return
        except PermissionError:
            if pause is None:
                return
            time.sleep(pause)


@dataclass
class PreparedRecorder:
    context: recorder_runtime.RecorderContext
    device: object | None
    channels: list[dict]
    capabilities: dict | None
    camera_mapping: dict | None
    error: str | None = None
    # True while this recorder's preflight probe is still running in its own
    # thread (prepare_recorders(probe_wait=...)); ``ready`` is set once the
    # fields above hold its final result.
    pending: bool = False
    ready: threading.Event = field(default_factory=_resolved_event, repr=False)
    _listeners: list = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def on_ready(self, callback) -> None:
        """Call ``callback(self)`` once the probe result is final (now, if it is)."""
        with self._lock:
            if not self.ready.is_set():
                self._listeners.append(callback)
                return
        callback(self)

    def _resolve(self, result: "PreparedRecorder") -> None:
        with self._lock:
            self.device = result.device
            self.channels = result.channels
            self.capabilities = result.capabilities
            self.camera_mapping = result.camera_mapping
            self.error = result.error
            self.pending = False
            self.ready.set()
            listeners, self._listeners = self._listeners, []
        for callback in listeners:
            try:
                callback(self)
            except Exception:  # noqa: BLE001 — a listener never breaks the probe thread
                pass


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


def _bind_contexts(contexts, mapping: dict) -> None:
    """Copy a persisted mapping onto runtime contexts.

    Disabled registry rows are part of the lifecycle mapping but have no
    runtime context, so they are skipped here.
    """
    by_local = {ctx.local_id: ctx for ctx in contexts}
    for local_id, cloud_id in mapping.items():
        ctx = by_local.get(str(local_id))
        if ctx is None:
            continue
        cloud_id = str(cloud_id)
        ctx.cloud_recorder_id = cloud_id
        ctx.config.recorder_cloud_id = cloud_id
        ctx.holder["recorder_cloud_id"] = cloud_id


def _apply_mapping(contexts, mapping: dict) -> None:
    _apply_cloud_mapping(mapping)
    _bind_contexts(contexts, mapping)


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
    _apply_cloud_mapping(mapping)
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

    if continuity is None:
        # Contract v4: the immutable continuity recorder must stay configured
        # throughout WatchLog 5.1. Registry validation and the DB CHECK enforce
        # the same rule; runtime fails closed if local state was hand-edited or
        # otherwise corrupted before we can stamp any historical singleton data.
        raise RuntimeError(
            "configured continuity recorder is missing from runtime contexts"
        )

    primaries = [ctx for ctx in contexts if ctx.is_primary]
    if len(primaries) != 1:
        raise RuntimeError("multi-recorder cutover requires exactly one primary")

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

    # One complete lifecycle sync binds every configured recorder and also
    # propagates disabled historical rows and the current preferred-primary
    # designation. A separate configured-only sync would be redundant.
    mapping = _sync_registry_state(cloud, state)
    _bind_contexts(contexts, mapping)
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
    if getattr(ctx, "credential_error", None):
        # Never contact a recorder with an empty login: that is a failed sign-in on
        # the recorder (lockout risk) and would be reported as a wrong password.
        return PreparedRecorder(
            context=ctx, device=None, channels=[], capabilities=None,
            camera_mapping=None,
            error=f"recorder login unavailable on this PC ({ctx.credential_error})",
        )

    driver = None
    try:
        driver, device = open_driver_fn(ctx.config)
        channels = [
            {"channel": str(ch.channel), "name": getattr(ch, "name", None)}
            for ch in driver.list_channels()
        ]
        # Capability enrichment is NOT read here: it is deferred off the startup path
        # (capability_sync, field Build 41/69) and sent once monitoring has started.
        capabilities = None

        _save_observed_identity(
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
        # WatchLog now maps these channels to camera UUIDs: keep each ONVIF camera on the
        # channel synced for the rest of the process (positional renumbering would move a
        # camera onto one that disappeared before it). Other recorders are not bound.
        pin = getattr(driver, "pin_inventory", None)
        if callable(pin):
            pin()

        capability_sync.defer(ctx.config, state, cloud, open_driver_fn,
                              recorder_id=ctx.cloud_recorder_id)

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


def _probe_into(slot: PreparedRecorder, cloud, state: dict, open_driver_fn) -> None:
    try:
        result = probe_and_sync_recorder(cloud, state, slot.context, open_driver_fn)
    except BaseException as exc:  # noqa: BLE001 — recorder-local; the slot must always resolve
        result = PreparedRecorder(
            context=slot.context, device=None, channels=[], capabilities=None,
            camera_mapping=None,
            error=f"{type(exc).__name__}: {str(exc).splitlines()[0][:160] if str(exc) else ''}",
        )
    slot._resolve(result)


def probe_all(cloud, state: dict, contexts, open_driver_fn,
              probe_wait: float | None = None) -> list[PreparedRecorder]:
    """Probe every recorder in its own thread, side by side (MNVR-021).

    An offline recorder's connect timeouts never delay its siblings. With
    ``probe_wait`` None this returns once every probe has finished; otherwise it
    returns after at most ``probe_wait`` seconds, and a probe still running stays
    ``pending`` and delivers its result through ``on_ready`` when it finishes."""
    slots = [
        PreparedRecorder(context=ctx, device=None, channels=[], capabilities=None,
                         camera_mapping=None, pending=True, ready=threading.Event())
        for ctx in contexts
    ]
    for slot in slots:
        threading.Thread(
            target=_probe_into, args=(slot, cloud, state, open_driver_fn),
            daemon=True, name=f"probe-{str(slot.context.local_id)[:8]}",
        ).start()
    deadline = None if probe_wait is None else time.monotonic() + max(0.0, probe_wait)
    for slot in slots:
        slot.ready.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))
    return slots


def prepare_recorders(base_cfg, state: dict, cloud, open_driver_fn,
                      probe_wait: float | None = None) -> list[PreparedRecorder]:
    """Bind all identities first, then independently probe/sync each recorder.

    A recorder whose login cannot be read on this PC is still bound (identity is
    non-secret) but is never probed: it comes back as a degraded context with
    ``error`` set, and its siblings are unaffected (MNVR-009)."""
    contexts = recorder_runtime.load_contexts(base_cfg, degrade_credential_errors=True)
    if not contexts:
        return []

    require_cloud_contract(cloud, state)
    bind_cloud_identities(cloud, state, contexts, base_cfg=base_cfg)
    return probe_all(cloud, state, contexts, open_driver_fn, probe_wait=probe_wait)
