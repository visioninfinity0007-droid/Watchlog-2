"""Fault-injection tests for the isolated WatchLog heartbeat publisher.

Hardware-free tests: passing does not prove field connectivity or Windows uptime.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from watchlog_heartbeat import HeartbeatPublisher, error_label, isolated_cloud


class StubCloud:
    created = []

    def __init__(self, url, key):
        self.url = url
        self.key = key
        self.request_timeout = 30
        self.s = SimpleNamespace(close=lambda: None)
        self.created.append(self)


class HeartbeatTransportTests(unittest.TestCase):
    def setUp(self):
        StubCloud.created.clear()
        self.stop = threading.Event()

    def tearDown(self):
        self.stop.set()

    def test_separate_cloud_has_bounded_timeout(self):
        cfg = SimpleNamespace(supabase_url="https://example.invalid", publishable_key="redacted")
        separate = isolated_cloud(StubCloud, cfg)
        self.assertEqual(separate.request_timeout, (5.0, 10.0))
        self.assertEqual(len(StubCloud.created), 1)

    def test_blocked_upload_does_not_delay_heartbeat(self):
        upload_release, upload_started = threading.Event(), threading.Event()
        acks = []

        def blocking_upload():
            upload_started.set()
            upload_release.wait(1)

        worker = threading.Thread(target=blocking_upload, daemon=True)
        worker.start()
        self.assertTrue(upload_started.wait(.5))
        publisher = HeartbeatPublisher(
            cloud_factory=lambda: object(),
            send=lambda cloud: acks.append(time.monotonic()),
            stop=self.stop, interval=.05, retry_delay=.01,
        )
        publisher.start()
        try:
            time.sleep(.22)
            self.assertGreaterEqual(len(acks), 3, acks)
            self.assertTrue(worker.is_alive(), "upload must still be blocked")
        finally:
            self.stop.set()
            publisher.join(.5)
            upload_release.set()
            worker.join(.5)

    def test_network_failures_retry_without_false_acks(self):
        attempted, recorded = [], []

        def fails(_cloud):
            attempted.append(1)
            raise OSError("secret://invalid credentials must never enter logs")

        publisher = HeartbeatPublisher(
            cloud_factory=lambda: object(), send=fails,
            stop=self.stop, interval=.1, retry_delay=.02, log=recorded.append)
        publisher.start()
        time.sleep(.1)
        snapshot = publisher.snapshot()
        self.assertGreaterEqual(len(attempted), 2)
        self.assertIsNone(snapshot["last_ack_monotonic"])
        self.assertGreaterEqual(snapshot["consecutive_failures"], 2)
        self.assertNotIn("secret://", " ".join(recorded))
        self.assertIn("OSError", " ".join(recorded))
        self.stop.set()
        publisher.join(.5)
        self.assertFalse(publisher.is_alive())

    def test_recovery_after_failure(self):
        sent = []

        def flaky(_cloud):
            sent.append(time.monotonic())
            if len(sent) == 1:
                raise ConnectionError("temporary WAN outage")

        publisher = HeartbeatPublisher(
            cloud_factory=lambda: object(), send=flaky,
            stop=self.stop, interval=.04, retry_delay=.01)
        publisher.start()
        time.sleep(.13)
        snapshot = publisher.snapshot()
        self.assertGreaterEqual(len(sent), 2)
        self.assertIsNotNone(snapshot["last_ack_monotonic"])
        self.assertEqual(snapshot["consecutive_failures"], 0)
        self.stop.set()
        publisher.join(.5)

    def test_failure_in_logging_cannot_kill_thread(self):
        sent = []

        def raises(_cloud):
            sent.append(1)
            raise SystemExit("artificial failure")

        publisher = HeartbeatPublisher(
            cloud_factory=lambda: object(), send=raises,
            stop=self.stop, interval=.04, retry_delay=.01,
            log=lambda line: (_ for _ in ()).throw(OSError("logger broken")))
        publisher.start()
        time.sleep(.07)
        self.assertGreaterEqual(len(sent), 3)
        self.assertTrue(publisher.is_alive())
        self.stop.set()
        publisher.join(.5)

    def test_error_label_never_includes_sensitive_message(self):
        self.assertEqual(error_label(RuntimeError("Bearer secret at https://host")), "RuntimeError")
        error = type("CloudError", (RuntimeError,), {})()
        error.status = 401
        self.assertEqual(error_label(error), "CloudError HTTP-401")

    def test_invalid_intervals_refused(self):
        with self.assertRaises(ValueError):
            HeartbeatPublisher(cloud_factory=lambda: object(), send=lambda c: None,
                               stop=self.stop, interval=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
