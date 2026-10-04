"""Multi-recorder event envelope + credential-scope tests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs  # noqa: E402
import native_event_collector as native  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DeviceInfo, Event  # noqa: E402
import windows_secret as ws  # noqa: E402


def _install_fake_crypto():
    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(text[5:])

    cs.write_json_secret = wjs
    cs.read_json_secret = rjs


class _Env:
    def __enter__(self):
        self.saved = os.environ.get("PROGRAMDATA")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self.tmp.name
        _install_fake_crypto()
        return Path(self.tmp.name) / "WatchLog"

    def __exit__(self, *args):
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def test_event_positional_compatibility_and_recorder_serialization():
    ts = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    # Historical positional argument order: sixth positional field is snapshot_b64.
    ev = Event("1", "motion", ts, None, {"x": 1}, "YWJj")
    assert ev.snapshot_b64 == "YWJj"
    assert ev.recorder_id is None

    tagged = ev.with_recorder_id("11111111-1111-1111-1111-111111111111")
    out = tagged.to_json(ts)
    assert out["channel"] == "1"
    assert out["snapshot_b64"] == "YWJj"
    assert out["recorder_id"] == "11111111-1111-1111-1111-111111111111"

    # Singleton path remains unchanged: no empty recorder_id field is emitted.
    assert "recorder_id" not in ev.to_json(ts)


def test_credential_generation_and_reload_are_recorder_scoped():
    with _Env():
        local_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        local_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        cs.save_recorder_credential(local_a, "a", "one")
        cs.save_recorder_credential(local_b, "b", "two")

        cfg_a = SimpleNamespace(
            recorder_local_id=local_a,
            nvr_username="old",
            nvr_password="old",
        )
        cfg_b = SimpleNamespace(
            recorder_local_id=local_b,
            nvr_username="b",
            nvr_password="two",
        )

        gen_a = core._credential_generation_for_cfg(cfg_a)
        gen_b = core._credential_generation_for_cfg(cfg_b)
        assert gen_a != "absent" and gen_b != "absent"

        cs.save_recorder_credential(local_a, "a2", "one2")
        assert core._credential_generation_for_cfg(cfg_a) != gen_a
        assert core._credential_generation_for_cfg(cfg_b) == gen_b

        core._reload_credential_for_cfg(cfg_a)
        assert cfg_a.nvr_username == "a2" and cfg_a.nvr_password == "one2"
        assert cfg_b.nvr_username == "b" and cfg_b.nvr_password == "two"


class _Spool:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    def trim(self):
        return 0


class _Driver:
    name = "fake"
    verified_against_hardware = True

    def __init__(self, stop):
        self.stop = stop
        self.closed = False

    def stream_events(self, _stop):
        ts = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
        yield Event("1", "motion", ts, payload={"source": "test"})
        self.stop.set()

    def close(self):
        self.closed = True


def _stop_instead_of_reconnecting(monkeypatch, stop):
    """A collector error must fail the test, not back off and retry forever."""
    def _reconnect_wait(_stop, _cfg, _failures, last_gen):
        stop.set()
        return "stop", last_gen
    monkeypatch.setattr(core, "_reconnect_wait", _reconnect_wait)


def test_packaged_native_collector_stamps_recorder_id(monkeypatch):
    stop = threading.Event()
    driver = _Driver(stop)
    # The real DeviceInfo, so the collector's startup log line can read firmware.
    info = DeviceInfo(vendor="Test", model="Recorder")
    spool = _Spool()

    cfg = SimpleNamespace(
        snapshots=False,
        snapshot_min_interval=60,
        recorder_cloud_id="22222222-2222-2222-2222-222222222222",
        recorder_local_id=None,
    )

    monkeypatch.setattr(core.vision, "build", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "open_driver", lambda _cfg: (driver, info))
    monkeypatch.setattr(core, "_credential_generation_for_cfg", lambda _cfg: "g0")
    _stop_instead_of_reconnecting(monkeypatch, stop)

    native.collector(cfg, spool, stop, holder={})

    assert driver.closed is True
    assert len(spool.rows) == 1
    assert spool.rows[0]["recorder_id"] == cfg.recorder_cloud_id
    assert spool.rows[0]["channel"] == "1"


def test_core_collector_stamps_recorder_id(monkeypatch):
    stop = threading.Event()
    driver = _Driver(stop)
    info = DeviceInfo(vendor="Test", model="Recorder")
    spool = _Spool()
    cfg = SimpleNamespace(
        snapshots=False,
        snapshot_min_interval=60,
        recorder_cloud_id="33333333-3333-3333-3333-333333333333",
        recorder_local_id=None,
    )

    monkeypatch.setattr(core.vision, "build", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "open_driver", lambda _cfg: (driver, info))
    monkeypatch.setattr(core, "_credential_generation_for_cfg", lambda _cfg: "g0")
    _stop_instead_of_reconnecting(monkeypatch, stop)

    core.collector(cfg, spool, stop, holder={})
    assert len(spool.rows) == 1
    assert spool.rows[0]["recorder_id"] == cfg.recorder_cloud_id
