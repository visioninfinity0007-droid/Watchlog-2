"""Nonproduction transport-isolated heartbeat publisher for WatchLog 5.1.2.

Heartbeat success is acknowledged only by the authenticated WatchLog RPC.
Site recorder monitoring/coverage remain separate and must never be inferred
from heartbeat alone. A process/host outage is NOT solved by this component.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

HEARTBEAT_CONNECT_TIMEOUT = 5.0
HEARTBEAT_READ_TIMEOUT = 10.0


def isolated_cloud(cloud_class: type, cfg: Any) -> Any:
    """Construct a dedicated session; never share the upload/session client."""
    cloud = cloud_class(cfg.supabase_url, cfg.publishable_key)
    cloud.request_timeout = (HEARTBEAT_CONNECT_TIMEOUT, HEARTBEAT_READ_TIMEOUT)
    return cloud


def error_label(exc: BaseException) -> str:
    """Return a bounded error type/status, not a potentially secret message."""
    label = type(exc).__name__
    status = getattr(exc, "status", None)
    if isinstance(status, int) and 100 <= status <= 599:
        label += f" HTTP-{status}"
    return label


class HeartbeatPublisher:
    """One transport worker, one HTTPS client, no overlapping heartbeat calls."""

    def __init__(
        self,
        *,
        cloud_factory: Callable[[], Any],
        send: Callable[[Any], None],
        stop: threading.Event,
        interval: float = 60.0,
        retry_delay: float = 15.0,
        log: Callable[[str], None] | None = None,
        name: str = "heartbeat-transport",
    ) -> None:
        if interval <= 0 or retry_delay <= 0:
            raise ValueError("positive interval and retry_delay required")
        self._factory = cloud_factory
        self._send = send
        self._stop = stop
        self._interval = float(interval)
        self._retry_delay = min(float(retry_delay), self._interval)
        self._logger = log
        self._thread = threading.Thread(target=self._run, daemon=True, name=name)
        self._lock = threading.Lock()
        self._last_attempt: float | None = None
        self._last_ack: float | None = None
        self._failures = 0
        self._last_error = ""
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("heartbeat publisher already started")
        self._started = True
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._started:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive() if self._started else False

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "last_attempt_monotonic": self._last_attempt,
                "last_ack_monotonic": self._last_ack,
                "consecutive_failures": self._failures,
                "last_error_kind": self._last_error,
                "thread_alive": self.is_alive(),
            }

    def _log(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger(message)
        except BaseException:
            pass

    def _run(self) -> None:
        # Create the session in this worker, never in the uploader's thread.
        cloud = None
        while not self._stop.is_set():
            attempt_at = time.monotonic()
            with self._lock:
                self._last_attempt = attempt_at
            try:
                if cloud is None:
                    cloud = self._factory()
                self._send(cloud)
                with self._lock:
                    self._last_ack = time.monotonic()
                    self._failures = 0
                    self._last_error = ""
                wait = max(0.0, self._interval - (time.monotonic() - attempt_at))
            except BaseException as exc:
                # No successful ACK is recorded on failure. Reset the HTTP session
                # and retry after a bounded delay; do not touch event/camera truth.
                with self._lock:
                    self._failures += 1
                    self._last_error = error_label(exc)
                self._log(f"heartbeat transport failed: {error_label(exc)}; retrying")
                self._close_cloud(cloud)
                cloud = None
                wait = self._retry_delay
            self._stop.wait(wait)
        self._close_cloud(cloud)

    @staticmethod
    def _close_cloud(cloud: Any) -> None:
        if cloud is None:
            return
        try:
            session = getattr(cloud, "s", None)
            if session is not None:
                session.close()
        except BaseException:
            pass
