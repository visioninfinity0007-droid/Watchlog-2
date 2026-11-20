#!/usr/bin/env python3
"""The running Agent finds its recorder again after a DHCP address change (5.1.14).

Field (17-20 Nov 2026): after power cuts the recorder's DHCP address changed; Al-Khalid had to
reinstall WatchLog three times and Chai Wala's recorder stayed unreachable. The running Agent
only retried other web ports on the SAME host; the conservative same-recorder rediscovery
(connector_rediscovery) was never called.

agent_core.open_driver now runs it after REDISCOVERY_AFTER_FAILURES non-auth failures, at most
once per REDISCOVERY_MIN_SECONDS per recorder, never after an auth failure, and adopts a new
address only when the saved serial is proven there. The address is persisted (registry row for a
registry recorder, watchlog.ini where it still names the old address) with the identity, so a
restart uses it. A site installed before identity saving existed gets its identity saved after
the first good connect. Discovery and drivers are fakes here: real DHCP movement on recorder
hardware stays IMPLEMENTED_UNVERIFIED.
"""
from __future__ import annotations

import configparser
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "agent"))
sys.path.insert(0, str(TESTS))

import agent_core  # noqa: E402
import connector_rediscovery as cr  # noqa: E402
import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import recorder_runtime  # noqa: E402
from drivers.base import DriverError  # noqa: E402
from test_multi_recorder_cloud_binding import Env  # noqa: E402

OLD = "http://192.0.2.10"
NEW_IP = "192.0.2.77"
NEW = f"http://{NEW_IP}"
SERIAL = "6J0ABCD1234567"
MODEL = "DH-XVR1B08-I"


class FakeDriver:
    name = "dahua-cgi"

    def __init__(self, url, serial=SERIAL, model=MODEL):
        self.url, self.serial, self.model = url, serial, model
        self.closed = False

    def probe(self):
        return info(self.serial, self.model)

    def close(self):
        self.closed = True


def info(serial=SERIAL, model=MODEL, vendor="Dahua"):
    return SimpleNamespace(vendor=vendor, model=model, serial=serial, firmware="V1",
                           driver="dahua-cgi")


class Net:
    """A fake LAN: which addresses answer, as which device, and who looked."""

    def __init__(self, monkeypatch, *, answers=None, auth_fail=(), found=None):
        self.answers = dict(answers or {})        # base url -> (serial, model)
        self.auth_fail = set(auth_fail)           # base urls that refuse the login
        self.found = found if found is not None else [(NEW_IP, [80, 37777])]
        self.sweeps = 0
        self.logins = []
        self.logs = []
        monkeypatch.setattr(agent_core, "_connect_recorder", self.connect)
        monkeypatch.setattr(cr.discover, "sweep", self.sweep)
        monkeypatch.setattr(cr, "build", self.build)
        monkeypatch.setattr(agent_core, "log", self.logs.append)
        # The bounded same-host port fallback: no other ports in these tests.
        monkeypatch.setattr(agent_core.recorder_probe, "candidates", lambda *_a, **_k: [])

    def _device(self, url):
        url = url.rstrip("/")
        if url in self.auth_fail:
            raise DriverError("the recorder rejected the username or password (HTTP 401)")
        if url not in self.answers:
            raise DriverError(f"could not connect to {url}: timed out")
        serial, model = self.answers[url]
        return FakeDriver(url, serial, model)

    def connect(self, cfg, base_url):
        driver = self._device(base_url)
        return driver, driver.probe()

    def sweep(self, *_a, **_k):
        self.sweeps += 1
        return list(self.found)

    def build(self, name, url, username, password, timeout=15):
        self.logins.append(url)
        return self._device(url)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    agent_core._reset_rediscovery_state()
    clock = {"now": 1000.0}
    monkeypatch.setattr(agent_core, "_rediscovery_clock", lambda: clock["now"])
    yield clock
    agent_core._reset_rediscovery_state()


def _write_ini(path: Path, url: str) -> None:
    ini = configparser.ConfigParser()
    ini["watchlog"] = {"nvr_url": url, "nvr_driver": "dahua-cgi", "site_type": "office"}
    with path.open("w", encoding="utf-8") as handle:
        ini.write(handle)


def _ini_url(path: Path) -> str:
    ini = configparser.ConfigParser()
    ini.read(path, encoding="utf-8")
    return ini["watchlog"]["nvr_url"]


def legacy_cfg(tmp_path: Path, *, identity=True, username="admin"):
    ini_path = tmp_path / "watchlog.ini"
    _write_ini(ini_path, OLD)
    cfg = SimpleNamespace(
        state_path=tmp_path / "agent_state.json", nvr_url=OLD, nvr_driver="dahua-cgi",
        nvr_username=username, nvr_password="pw", _ini_path=ini_path,
        legacy_credential_error=None, recorder_identity_fingerprint=None,
        require_nvr=lambda: None)
    if identity:
        cr.save_identity(cfg, info(), OLD)
    return cfg


def fail_until_due(cfg, net, times=None):
    """Open the recorder until rediscovery is due; return the last outcome."""
    times = times or cr.REDISCOVERY_AFTER_FAILURES
    for _ in range(times - 1):
        with pytest.raises(DriverError):
            agent_core.open_driver(cfg)
    return agent_core.open_driver(cfg)


# --- legacy singleton -------------------------------------------------------------

def test_moved_recorder_with_matching_serial_is_adopted_and_persisted(tmp_path, monkeypatch):
    cfg = legacy_cfg(tmp_path)
    net = Net(monkeypatch, answers={NEW: (SERIAL, MODEL)})

    driver, device = fail_until_due(cfg, net)

    assert driver.url == NEW and device.serial == SERIAL
    assert net.sweeps == 1 and net.logins == [NEW]
    assert cfg.nvr_url == NEW
    assert _ini_url(cfg._ini_path) == NEW, "a restart would go back to the old address"
    saved = json.loads(cr.identity_path(cfg).read_text(encoding="utf-8"))
    assert saved["url"] == NEW and saved["serial"] == SERIAL
    assert any("same recorder proven at http://192.0.2.77" in m for m in net.logs), net.logs

    # The next open goes straight to the new address: no further search.
    agent_core.open_driver(cfg)
    assert net.sweeps == 1


def test_a_device_with_another_serial_is_refused(tmp_path, monkeypatch):
    cfg = legacy_cfg(tmp_path)
    net = Net(monkeypatch, answers={NEW: ("OTHER-SERIAL", MODEL)})

    with pytest.raises(DriverError):
        fail_until_due(cfg, net)

    assert net.sweeps == 1 and net.logins == [NEW]
    assert cfg.nvr_url == OLD
    assert _ini_url(cfg._ini_path) == OLD
    assert json.loads(cr.identity_path(cfg).read_text(encoding="utf-8"))["url"] == OLD
    assert not any("proven" in m for m in net.logs)


def test_auth_failure_never_triggers_a_scan(tmp_path, monkeypatch):
    cfg = legacy_cfg(tmp_path)
    net = Net(monkeypatch, auth_fail={OLD}, answers={NEW: (SERIAL, MODEL)})

    for _ in range(cr.REDISCOVERY_AFTER_FAILURES * 3):
        with pytest.raises(DriverError, match="username or password"):
            agent_core.open_driver(cfg)

    assert net.sweeps == 0 and net.logins == []
    assert cfg.nvr_url == OLD


def test_an_auth_failure_resets_the_failure_count(tmp_path, monkeypatch):
    cfg = legacy_cfg(tmp_path)
    net = Net(monkeypatch, answers={NEW: (SERIAL, MODEL)})
    for _ in range(cr.REDISCOVERY_AFTER_FAILURES - 1):
        with pytest.raises(DriverError):
            agent_core.open_driver(cfg)
    net.auth_fail.add(OLD)
    with pytest.raises(DriverError, match="username or password"):
        agent_core.open_driver(cfg)
    net.auth_fail.clear()
    with pytest.raises(DriverError):          # one non-auth failure after the reset
        agent_core.open_driver(cfg)
    assert net.sweeps == 0


def test_rate_limit_is_honoured(tmp_path, monkeypatch, _fresh_state):
    clock = _fresh_state
    cfg = legacy_cfg(tmp_path)
    net = Net(monkeypatch, found=[])           # the recorder is nowhere (still powered off)

    for _ in range(cr.REDISCOVERY_AFTER_FAILURES - 1):
        with pytest.raises(DriverError):
            agent_core.open_driver(cfg)
    assert net.sweeps == 0, "searched before REDISCOVERY_AFTER_FAILURES failures"

    with pytest.raises(DriverError):
        agent_core.open_driver(cfg)
    assert net.sweeps == 1

    clock["now"] += cr.REDISCOVERY_MIN_SECONDS - 1
    for _ in range(10):
        with pytest.raises(DriverError):
            agent_core.open_driver(cfg)
    assert net.sweeps == 1, "searched again inside REDISCOVERY_MIN_SECONDS"

    clock["now"] += 1
    with pytest.raises(DriverError):
        agent_core.open_driver(cfg)
    assert net.sweeps == 2


def test_no_scan_without_a_usable_login(tmp_path, monkeypatch):
    cfg = legacy_cfg(tmp_path, username="")
    net = Net(monkeypatch, answers={NEW: (SERIAL, MODEL)})
    for _ in range(cr.REDISCOVERY_AFTER_FAILURES * 2):
        with pytest.raises(DriverError):
            agent_core.open_driver(cfg)
    assert net.sweeps == 0


def test_no_scan_without_a_saved_identity(tmp_path, monkeypatch):
    cfg = legacy_cfg(tmp_path, identity=False)
    net = Net(monkeypatch, answers={NEW: (SERIAL, MODEL)})
    with pytest.raises(DriverError):
        fail_until_due(cfg, net)
    assert net.sweeps == 0
    assert any("no saved recorder identity" in m for m in net.logs)


def test_identity_is_saved_after_the_first_connect_when_missing(tmp_path, monkeypatch):
    cfg = legacy_cfg(tmp_path, identity=False)
    Net(monkeypatch, answers={OLD: (SERIAL, MODEL)})
    assert not cr.identity_path(cfg).exists()

    agent_core.open_driver(cfg)

    saved = json.loads(cr.identity_path(cfg).read_text(encoding="utf-8"))
    assert (saved["serial"], saved["model"], saved["vendor"], saved["url"]) == \
        (SERIAL, MODEL, "Dahua", OLD)


def test_an_existing_identity_is_not_overwritten_by_a_connect(tmp_path, monkeypatch):
    cfg = legacy_cfg(tmp_path, identity=False)
    cr.save_identity(cfg, info("SETUP-SERIAL"), OLD)
    Net(monkeypatch, answers={OLD: (SERIAL, MODEL)})
    agent_core.open_driver(cfg)
    assert json.loads(cr.identity_path(cfg).read_text(encoding="utf-8"))["serial"] == "SETUP-SERIAL"


def test_ini_is_only_rewritten_where_it_names_the_old_address(tmp_path):
    path = tmp_path / "watchlog.ini"
    _write_ini(path, "http://192.0.2.99")
    assert cr.replace_ini_url(path, OLD, NEW) is False
    assert _ini_url(path) == "http://192.0.2.99"
    _write_ini(path, OLD + "/")
    assert cr.replace_ini_url(path, OLD, NEW) is True
    assert _ini_url(path) == NEW


def test_redacted_url_has_no_login_or_path():
    assert agent_core.redact_url("http://admin:secret@192.0.2.7:8080/cgi-bin/x?a=1") == \
        "http://192.0.2.7:8080"
    assert agent_core.redact_url("https://192.0.2.7") == "https://192.0.2.7"


# --- registry (multi-recorder) sites -------------------------------------------------

CLOUD_A = "11111111-1111-1111-1111-111111111111"
CLOUD_B = "22222222-2222-2222-2222-222222222222"
B_OLD = "http://192.0.2.20"
B_SERIAL = "6J0BBBB7654321"


def seed_registry(root: Path, *, a_fp=f"serial:{SERIAL}", b_fp=f"serial:{B_SERIAL}"):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    cs.save_recorder_credential(a, "admin", "pw-a")
    cs.save_recorder_credential(b, "admin", "pw-b")
    rr.save_registry({
        "schema": rr.REGISTRY_SCHEMA,
        "recorders": [
            {"local_id": a, "display_name": "Recorder A", "url": OLD, "driver": "dahua-cgi",
             "vendor": "Dahua", "model": MODEL, "identity_fingerprint": a_fp,
             "cloud_recorder_id": CLOUD_A, "is_primary": True, "is_configured": True,
             "continuity_owner": True},
            {"local_id": b, "display_name": "Recorder B", "url": B_OLD, "driver": "dahua-cgi",
             "vendor": "Dahua", "model": MODEL, "identity_fingerprint": b_fp,
             "cloud_recorder_id": CLOUD_B, "is_primary": False, "is_configured": True},
        ],
    })
    root.mkdir(parents=True, exist_ok=True)
    ini_path = root / "watchlog.ini"
    _write_ini(ini_path, OLD)                # the continuity recorder's legacy mirror
    base = SimpleNamespace(
        state_path=root / "agent_state.json", spool_path=root / "spool.sqlite",
        health_store_path=root / "health.sqlite", last_live_path=root / "last_live.json",
        nvr_url=OLD, nvr_driver="dahua-cgi", nvr_username="admin", nvr_password="pw-a",
        _ini_path=ini_path, legacy_credential_error=None, require_nvr=lambda: None)
    contexts = {ctx.local_id: ctx for ctx in recorder_runtime.load_contexts(base)}
    for ctx in contexts.values():
        ctx.config.require_nvr = lambda: None
    return a, b, base, contexts


def test_registry_recorder_move_updates_its_row_and_keeps_its_identities(monkeypatch):
    with Env() as env:
        a, b, base, contexts = seed_registry(env.root)
        net = Net(monkeypatch, answers={B_OLD: (B_SERIAL, MODEL), NEW: (SERIAL, MODEL)})
        cfg_a = contexts[a].config

        driver, device = fail_until_due(cfg_a, net)

        assert driver.url == NEW and device.serial == SERIAL
        row = rr.recorder(a)
        assert row["url"] == NEW
        assert row["local_id"] == a and row["cloud_recorder_id"] == CLOUD_A
        assert row["identity_fingerprint"] == f"serial:{SERIAL}"
        assert rr.recorder(b)["url"] == B_OLD, "another recorder's row was touched"
        assert cs.load_recorder_credential(a)["password"] == "pw-a", "the login changed"
        # Restart path: a freshly loaded context opens the new address.
        reloaded = {c.local_id: c for c in recorder_runtime.load_contexts(base)}
        assert reloaded[a].config.nvr_url == NEW
        # The continuity recorder's legacy mirror follows it.
        assert _ini_url(base._ini_path) == NEW
        ident = json.loads(cr.identity_path(cfg_a).read_text(encoding="utf-8"))
        assert ident["url"] == NEW and ident["serial"] == SERIAL
        assert cr.identity_path(cfg_a) != cr.site_identity_path(cfg_a)


def test_other_workers_of_the_moved_recorder_use_the_proven_address(monkeypatch):
    with Env() as env:
        a, _b, base, _contexts = seed_registry(env.root)
        net = Net(monkeypatch, answers={NEW: (SERIAL, MODEL)})
        # Two workers holding their own copies of recorder A's config.
        cfg_one = recorder_runtime.load_contexts(base)[0].config
        cfg_two = recorder_runtime.load_contexts(base)[0].config
        assert cfg_one.recorder_local_id == cfg_two.recorder_local_id == a

        fail_until_due(cfg_one, net)
        driver, _ = agent_core.open_driver(cfg_two)

        assert driver.url == NEW and cfg_two.nvr_url == NEW
        assert net.sweeps == 1


def test_secondary_recorder_move_never_rewrites_the_ini(monkeypatch):
    with Env() as env:
        a, b, base, contexts = seed_registry(env.root)
        net = Net(monkeypatch, answers={OLD: (SERIAL, MODEL), NEW: (B_SERIAL, MODEL)})

        driver, device = fail_until_due(contexts[b].config, net)

        assert device.serial == B_SERIAL
        assert rr.recorder(b)["url"] == NEW
        assert rr.recorder(a)["url"] == OLD
        assert _ini_url(base._ini_path) == OLD


def test_a_secondary_never_searches_with_the_primarys_site_identity(monkeypatch):
    with Env() as env:
        # B's serial was never observed; only the site-level identity (A's, seeded by Setup)
        # exists. B must not adopt A at a new address.
        a, b, base, contexts = seed_registry(env.root, b_fp=None)
        cr.save_identity(SimpleNamespace(state_path=base.state_path, nvr_driver="dahua-cgi",
                                         nvr_url=OLD), info(), OLD)
        net = Net(monkeypatch, answers={NEW: (SERIAL, MODEL)})

        cfg_b = contexts[b].config
        expected = agent_core._expected_identity(cfg_b)
        assert expected.get("serial") != SERIAL

        with pytest.raises(DriverError):
            fail_until_due(cfg_b, net)
        assert rr.recorder(b)["url"] == B_OLD
        assert rr.recorder(a)["url"] == OLD


def test_registry_serial_wins_over_a_stale_identity_file(monkeypatch):
    with Env() as env:
        a, _b, _base, contexts = seed_registry(env.root)
        cfg_a = contexts[a].config
        cr.save_identity(cfg_a, info("STALE-SERIAL"), OLD)
        assert agent_core._expected_identity(cfg_a) == {
            "vendor": "Dahua", "model": MODEL, "serial": SERIAL, "driver": "dahua-cgi",
            "url": OLD}


def test_registry_identity_saved_per_recorder_after_first_connect(monkeypatch):
    with Env() as env:
        a, b, _base, contexts = seed_registry(env.root)
        Net(monkeypatch, answers={OLD: (SERIAL, MODEL), B_OLD: (B_SERIAL, MODEL)})
        agent_core.open_driver(contexts[a].config)
        agent_core.open_driver(contexts[b].config)
        ia = json.loads(cr.identity_path(contexts[a].config).read_text(encoding="utf-8"))
        ib = json.loads(cr.identity_path(contexts[b].config).read_text(encoding="utf-8"))
        assert (ia["serial"], ib["serial"]) == (SERIAL, B_SERIAL)


def test_update_recorder_address_refuses_another_rows_address_or_serial():
    with Env() as env:
        a, b, _base, _contexts = seed_registry(env.root)
        with pytest.raises(rr.DuplicateRecorder):
            rr.update_recorder_address(a, url=B_OLD)
        with pytest.raises(rr.RecorderIdentityConflict):
            rr.update_recorder_address(a, url=NEW, identity_fingerprint="serial:OTHER")
        assert rr.recorder(a)["url"] == OLD


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
