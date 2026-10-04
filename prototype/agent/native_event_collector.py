"""Production collector policy for recorder-native smart events.

Generic recorder motion remains eligible for WatchLog's local false-alarm
filter. Explicit vendor smart classifications (Dahua SMD/IVS, Hikvision smart
analytics) are retained as first-class incidents without requiring a second
person/vehicle inference pass. This both uses the NVR's built-in AI and avoids
throwing away a recorder-native line/intrusion event because a single still did
not contain an object at capture time.
"""
from __future__ import annotations

import base64
import time

import requests

import watchlog_agent as core
import native_verification
import nvr_health
from drivers import DriverError


def spool_row(ev, agent_ts) -> dict:
    """The event as spooled for wl_ingest_events.

    A recorder-scoped or channel-less event (channel None) is sent with a JSON null
    channel so ingest joins no camera; Event.to_json alone would write the string "None".
    """
    row = ev.to_json(agent_ts)
    if ev.channel is None:
        row["channel"] = None
    return row


def _note_stream_error(stream: dict, reports_stream, error: BaseException) -> None:
    """Record a collector-level failure in the per-recorder event-stream state.

    Connected becomes False when the recorder could not be opened at all or its driver
    reports its stream; a driver that cannot report its stream stays unknown (None)."""
    if reports_stream is not False:
        stream["connected"] = False
    stream["last_error"] = nvr_health.redact(str(error)) or type(error).__name__


def collector(cfg, spool, stop, holder=None) -> None:
    """Core event collector with recorder-native AI precedence.

    Never dies: on any driver error it backs off and re-opens. CONFIRMED recorder auth
    failures escalate 5->15->30 min (via ``core._reconnect_wait``) so a wrong password can
    never hammer the recorder into an account lockout, and a credential change in Setup wakes
    the wait immediately. Native VideoLoss/disconnect events are fed to the shared health
    monitor so a camera drop is reflected without waiting for the next probe.

    Recorder liveness is event-stream liveness. A driver that reports its stream
    (Hikvision, Dahua) is live only on stream activity, never because its probe answered;
    one per-recorder ``event_stream`` state survives driver re-opens for the heartbeat."""
    detector = core.vision.build(cfg, core.log)
    auth_failures = 0
    last_gen = core.credential_store.credential_generation()
    stream = {"connected": None, "connected_at": None, "last_frame_at": None,
              "last_error": None}
    if holder is not None:
        holder["event_stream"] = stream
    while not stop.is_set():
        driver = None
        reports_stream = None
        auth_error = False
        try:
            driver, info = core.open_driver(cfg)
            reports_stream = bool(getattr(driver, "reports_stream_activity", False))
            if reports_stream:
                stream["connected"] = False
                driver.event_stream = stream
            else:
                stream["connected"] = None        # this driver cannot say: unknown
            if holder is not None:
                holder["live_driver"] = driver
                if not reports_stream:
                    # A driver that cannot report its event stream keeps the transport
                    # stamp; a stream-reporting driver is stamped by stream activity only.
                    holder["recorder_live_at"] = time.monotonic()
                    holder["recorder_live_wall"] = core.now_utc()
                holder["recorder_vendor"] = info.vendor
                holder["recorder_model"] = info.model
            core.log(
                f"driver {driver.name}: {info.vendor} {info.model or ''} "
                f"fw={info.firmware or '?'}".rstrip()
            )
            if not driver.verified_against_hardware:
                core.log(
                    f"NOTE: driver '{driver.name}' has not been verified against "
                    "this hardware family. Treat untested capabilities as pilot."
                )

            last_shot: dict[str, float] = {}
            for ev in driver.stream_events(stop):
                if stop.is_set():
                    break
                if holder is not None:
                    holder["recorder_live_at"] = time.monotonic()
                    holder["recorder_live_wall"] = core.now_utc()

                raw = None
                # A recorder-scoped or channel-less event (channel None) has no camera
                # to take a still from.
                if (cfg.snapshots and ev.channel is not None
                        and ev.event_type not in core.NO_SNAPSHOT_EVENTS):
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
                            raw = None

                native_ai = bool((ev.payload or {}).get("native_ai"))
                if native_ai:
                    # The recorder has already classified this as a smart
                    # event. Keep that provenance and do not make a second
                    # local model a prerequisite for the incident to exist.
                    core.log(
                        f"kept ch{ev.channel} {ev.event_type}: "
                        f"recorder-native AI ({(ev.payload or {}).get('native_code', 'smart event')})"
                    )
                    # Secondary verification (never drops the event): for a simple
                    # object classification (person/vehicle) with a fresh still, cross-
                    # check the recorder's label against the local model and attach a
                    # verification state. This is what stops an indoor native-Vehicle
                    # false positive from being promoted as a *verified* Vehicle incident
                    # downstream — the raw recorder event is still preserved and reported.
                    if detector is not None and native_verification.is_verifiable(ev.event_type):
                        local = None
                        if raw:
                            _, found = detector.classify_event(raw)
                            local = [d.label for d in (found or [])]
                        state = native_verification.annotate_event(ev.payload, ev.event_type, local)
                        if state == native_verification.CONFLICT:
                            core.log(
                                f"WARNING: ch{ev.channel} native {ev.event_type} conflicts with "
                                f"local model ({', '.join(sorted(set(local)))}); kept but flagged unverified"
                            )
                elif detector is not None and ev.event_type not in core.NO_SNAPSHOT_EVENTS:
                    # Generic motion still gets the existing local false-alarm
                    # filter. This retains the useful reduction in noisy DVR
                    # motion without overriding vendor-native smart analytics.
                    keep, found = detector.classify_event(raw)
                    if not keep:
                        core.log(
                            f"discarded ch{ev.channel} {ev.event_type}: "
                            "no person/vehicle in frame"
                        )
                        continue
                    if found:
                        ev.payload["objects"] = [item.as_dict() for item in found]
                        ev.payload["detector"] = detector.model_name
                        ev.payload["source"] = "watchlog_local_ai"
                        core.log(
                            f"kept ch{ev.channel}: "
                            f"{', '.join(sorted({item.label for item in found}))}"
                        )
                else:
                    ev.payload.setdefault("source", "recorder_event")

                spool.add(spool_row(ev, core.now_utc()))

                # A native VideoLoss/disconnect is an immediate camera OFFLINE — feed it to the
                # shared health monitor straight from the event stream (best-effort; a health-side
                # error must never disturb ingestion).
                if (holder is not None and ev.channel is not None
                        and ev.event_type in core.NATIVE_FAULT_TYPES):
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
            _note_stream_error(stream, reports_stream, error)
            for line in str(error).splitlines():
                if line.strip():
                    core.log(f"ERROR: driver: {line.strip()[:200]}")
            core.log(
                "run watchlog-agent.exe --probe to identify the recorder at that address"
            )
        except SystemExit as error:
            _note_stream_error(stream, reports_stream, error)
            core.log(
                f"ERROR: driver not configured: {str(error).splitlines()[0][:200]}"
            )
        except Exception as error:  # noqa: BLE001
            _note_stream_error(stream, reports_stream, error)
            core.log(f"ERROR: driver crashed: {type(error).__name__}: {error}")
        finally:
            if driver:
                if holder is not None and holder.get("live_driver") is driver:
                    holder.pop("live_driver", None)
                    # Keep the dropped driver's last real stream activity visible, so
                    # liveness ages out from the last frame, not from the drop.
                    activity = float(getattr(driver, "last_activity_monotonic", 0.0) or 0.0)
                    if activity > float(holder.get("recorder_live_at") or 0.0):
                        holder["recorder_live_at"] = activity
                try:
                    driver.close()
                except Exception:
                    pass
        if not stop.is_set():
            # Escalating backoff on CONFIRMED auth failure (lockout guard); short retry
            # otherwise. A credential change in Setup wakes the wait and retries immediately.
            auth_failures = auth_failures + 1 if auth_error else 0
            if auth_failures == 0:
                core.log(f"driver reconnecting in {core.DRIVER_RETRY_SECONDS}s")
            outcome, last_gen = core._reconnect_wait(stop, cfg, auth_failures, last_gen)
            if outcome == "stop":
                break
