"""Connectivity-first production event collector.

This is the Site Connector policy: capture recorder-native events and bounded
snapshots, preserve vendor provenance, and ship them to WatchLog. It deliberately
runs NO local WatchLog inference. Raw/structured CCTV intelligence is evaluated by
WatchLog's private server-side vision pipeline.

Recorder-native smart classifications (Dahua SMD/IVS, Hikvision smart events) are
preserved because they are facts emitted by the recorder, not WatchLog inference.
"""
from __future__ import annotations

import base64
import time

import requests

import watchlog_agent as core
import connector_rediscovery
from drivers import DriverError


def collector(cfg, spool, stop, holder=None) -> None:
    """Resilient recorder event collector with no local AI dependency.

    The connector never dies on a recorder error. Confirmed authentication failures
    use the core escalating lockout guard; every other failure reconnects on the
    normal bounded cadence. Snapshot failure never suppresses the underlying event.
    """
    auth_failures = 0
    connect_failures = 0
    last_rediscovery = 0.0
    last_gen = core.credential_store.credential_generation()

    while not stop.is_set():
        driver = None
        auth_error = False
        try:
            moved = None
            if (connect_failures >= connector_rediscovery.REDISCOVERY_AFTER_FAILURES
                    and time.monotonic() - last_rediscovery
                    >= connector_rediscovery.REDISCOVERY_MIN_SECONDS):
                last_rediscovery = time.monotonic()
                moved = connector_rediscovery.rediscover_same_recorder(cfg, core.log)

            driver, info = moved if moved is not None else core.open_driver(cfg)
            connect_failures = 0
            connector_rediscovery.save_identity(
                cfg, info, getattr(driver, "base_url", cfg.nvr_url)
            )
            core.log(
                f"driver {driver.name}: {info.vendor} {info.model or ''} "
                f"fw={info.firmware or '?'}".rstrip()
            )
            if holder is not None:
                holder["recorder_live_at"] = time.monotonic()
                holder["recorder_vendor"] = info.vendor or ""
                holder["recorder_model"] = info.model or ""
                holder["recorder_driver"] = driver.name
            if not driver.verified_against_hardware:
                core.log(
                    f"NOTE: driver '{driver.name}' is not field-verified for every "
                    "firmware/model; unsupported capabilities remain fail-closed."
                )

            last_shot: dict[str, float] = {}
            for ev in driver.stream_events(stop):
                if stop.is_set():
                    break

                if holder is not None:
                    holder["recorder_live_at"] = time.monotonic()

                raw = None
                if cfg.snapshots and not ev.snapshot_b64 and ev.event_type not in core.NO_SNAPSHOT_EVENTS:
                    clock = time.monotonic()
                    if clock - last_shot.get(ev.channel, 0.0) >= cfg.snapshot_min_interval:
                        last_shot[ev.channel] = clock
                        try:
                            raw = driver.get_snapshot(ev.channel)
                        except Exception as error:  # noqa: BLE001
                            raw = None
                            core.log(
                                f"snapshot ch{ev.channel} failed: "
                                f"{type(error).__name__}: {str(error)[:120]}"
                            )

                        if raw and len(raw) <= core.SNAPSHOT_MAX_BYTES:
                            ev = ev.with_snapshot(base64.b64encode(raw).decode("ascii"))
                            core.log(f"snapshot ch{ev.channel} {len(raw) // 1024} KB")
                        elif raw:
                            core.log(
                                f"snapshot ch{ev.channel} discarded: "
                                f"{len(raw) // 1024} KB exceeds cap"
                            )

                if holder is not None and ev.snapshot_b64:
                    holder.setdefault("snapshot_ok", {})[str(ev.channel)] = time.monotonic()

                payload = ev.payload
                if payload.get("native_ai"):
                    payload.setdefault("source", "recorder_native_ai")
                    core.log(
                        f"forwarded ch{ev.channel} {ev.event_type}: recorder-native "
                        f"classification ({payload.get('native_code', 'smart event')})"
                    )
                else:
                    payload.setdefault("source", "recorder_event")

                spool.add(ev.to_json(core.now_utc()))

                if holder is not None and ev.event_type in core.NATIVE_FAULT_TYPES:
                    mon = holder.get("monitor")
                    if mon is not None:
                        try:
                            mon.record_native_fault(ev.channel)
                        except Exception:  # noqa: BLE001
                            pass

                dropped = spool.trim()
                if dropped:
                    core.log(
                        f"WARNING: spool over capacity, dropped {dropped} oldest events"
                    )

        except (DriverError, requests.RequestException, RuntimeError) as error:
            auth_error = core._is_auth_failure(error)
            connect_failures = 0 if auth_error else connect_failures + 1
            for line in str(error).splitlines():
                if line.strip():
                    core.log(f"ERROR: driver: {line.strip()[:200]}")
            core.log(
                "recorder connection lost; Site Connector will retry automatically. "
                "Run watchlog-agent.exe --probe for local diagnostics."
            )
        except SystemExit as error:
            connect_failures += 1
            core.log(
                f"ERROR: driver not configured: {str(error).splitlines()[0][:200]}"
            )
        except Exception as error:  # noqa: BLE001
            connect_failures += 1
            core.log(f"ERROR: driver crashed: {type(error).__name__}: {error}")
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:  # noqa: BLE001
                    pass

        if not stop.is_set():
            auth_failures = auth_failures + 1 if auth_error else 0
            if auth_failures == 0:
                core.log(f"driver reconnecting in {core.DRIVER_RETRY_SECONDS}s")
            outcome, last_gen = core._reconnect_wait(
                stop, cfg, auth_failures, last_gen
            )
            if outcome == "reload":
                auth_failures = 0
