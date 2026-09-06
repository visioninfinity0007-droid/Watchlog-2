"""Phase-2 credential-store orchestration + auth-breaker unit tests.

The DPAPI encryption and the Windows DACL/owner hardening are proven on real
runners by .github/workflows/windows-security-gate.yml. These tests mock the
crypto layer to a filesystem-backed store so the ORCHESTRATION invariants are
verified cross-platform:
  * DPAPI is authoritative; a corrupt blob is a repair-required error and is
    NEVER downgraded to plaintext.
  * legacy watchlog.env migration is idempotent + crash-recoverable.
  * agent key is split out of agent_state.json; is_enrolled = state AND key.
  * the auth circuit-breaker is interruptible (wakes on credential change/stop)
    and only escalates on confirmed auth failures.
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs   # noqa: E402
import watchlog_agent as core   # noqa: E402
import windows_secret as ws     # noqa: E402


# --- mock the crypto layer to a filesystem-backed "encrypted" store ---------
# A real file exists (so .exists()/generation work); the plaintext is tagged so
# a deliberately-corrupted file fails to "decrypt".
def _install_fake_crypto():
    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError(f"corrupt DPAPI blob at {path}")
        return json.loads(text[5:])

    def wsec(path, payload):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"SEC:" + bytes(payload))

    def rsec(path):
        data = Path(path).read_bytes()
        if not data.startswith(b"SEC:"):
            raise ws.SecretError(f"corrupt DPAPI blob at {path}")
        return data[4:]

    cs.write_json_secret = wjs
    cs.read_json_secret = rjs
    cs.write_secret = wsec
    cs.read_secret = rsec
    cs.unprotect_bytes = lambda b: b   # for the old nvr_password.dpapi path


class _Env:
    """Isolate PROGRAMDATA to a temp dir and install the fake crypto."""
    def __enter__(self):
        self._saved = os.environ.get("PROGRAMDATA")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self._tmp.name
        _install_fake_crypto()
        return Path(self._tmp.name) / "WatchLog"

    def __exit__(self, *a):
        if self._saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self._saved
        self._tmp.cleanup()


# --- 1. save + load recorder credential (username+password atomic) ----------
def test_nvr_credential_roundtrip_atomic():
    with _Env():
        cs.save_nvr_credential("admin", "p@ss:w0rd")
        cred = cs.load_nvr_credential()
        assert cred["username"] == "admin" and cred["password"] == "p@ss:w0rd"
        assert cred["credential_version"] == ws.CREDENTIAL_STORE_VERSION


# --- 2. legacy watchlog.env migration: idempotent, authoritative, cleaned ---
def test_migration_from_env_is_idempotent_and_cleans_up():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        (wl / "watchlog.env").write_text("WATCHLOG_NVR_PASSWORD=legacy123\n", encoding="utf-8")
        ini = wl / "watchlog.ini"
        ini.write_text("[watchlog]\nnvr_username = operator\n", encoding="utf-8")

        cred = cs.load_nvr_credential(ini)                 # triggers migration
        assert cred["password"] == "legacy123" and cred["username"] == "operator"
        assert cs.nvr_credential_path().exists()           # DPAPI now authoritative
        assert not (wl / "watchlog.env").exists()          # legacy removed + verified
        # second call: DPAPI authoritative, no migration, still correct
        assert cs.load_nvr_credential(ini)["password"] == "legacy123"


# --- 3. corrupt DPAPI is repair-required, NEVER falls back to plaintext ------
def test_corrupt_dpapi_never_downgrades_to_plaintext():
    with _Env() as wl:
        wl.mkdir(parents=True, exist_ok=True)
        cs.save_nvr_credential("admin", "goodpw")
        # attacker/disk corrupts the authoritative blob AND drops a plaintext file
        cs.nvr_credential_path().write_text("NOT-A-VALID-BLOB", encoding="utf-8")
        (wl / "watchlog.env").write_text("WATCHLOG_NVR_PASSWORD=attacker\n", encoding="utf-8")
        try:
            cs.load_nvr_credential(wl / "watchlog.ini")
            assert False, "corrupt DPAPI must raise, not fall back to plaintext"
        except ws.SecretError:
            pass


# --- 4. is_enrolled requires BOTH state and a decryptable key ----------------
def test_is_enrolled_requires_state_and_key():
    with _Env():
        assert cs.is_enrolled() is False                    # nothing
        cs.save_state({"agent_id": "a1", "site_id": "s1", "tenant_id": "t1"})
        assert cs.is_enrolled() is False                    # state but no key
        cs.save_agent_key("bearer-key")
        assert cs.is_enrolled() is True                     # both
        cs.agent_key_path().write_text("CORRUPT", encoding="utf-8")
        assert cs.is_enrolled() is False                    # corrupt key -> not usable


# --- 5. the agent key is split OUT of agent_state.json -----------------------
def test_agent_key_never_in_state_file():
    with _Env():
        cs.save_state({"agent_id": "a1", "site_id": "s1", "tenant_id": "t1",
                       "agent_key": "TOP-SECRET"})
        raw = cs.state_path().read_text(encoding="utf-8")
        assert "TOP-SECRET" not in raw                       # not in the plaintext json
        assert "agent_id" in raw
        loaded = cs.load_state()
        assert loaded["agent_key"] == "TOP-SECRET"           # re-injected from the store


# --- 6. credential generation changes when the credential is rewritten -------
def test_credential_generation_changes():
    with _Env():
        assert cs.credential_generation() == "absent"
        cs.save_nvr_credential("admin", "one")
        g1 = cs.credential_generation()
        cs.save_nvr_credential("admin", "two")
        assert cs.credential_generation() != g1


# --- 7. 401 classification (breaker only escalates on confirmed auth) --------
def test_auth_failure_classification():
    assert core._is_auth_failure(Exception("recorder rejected the username or password"))
    assert core._is_auth_failure(Exception("http://x: HTTP 401 unauthorized"))
    assert not core._is_auth_failure(Exception("connection timed out"))
    assert not core._is_auth_failure(Exception("no driver recognised the device"))


# --- 8. breaker wait is interruptible by stop and by credential change -------
def test_reconnect_wait_is_interruptible():
    class _Cfg:
        reloaded = False
        def load_recorder_credential(self):
            self.reloaded = True

    # stop set -> returns immediately
    stop = threading.Event(); stop.set()
    assert core._reconnect_wait(stop, _Cfg(), 3, "gen0")[0] == "stop"

    # a credential change wakes the wait, reloads, and returns 'reload' fast
    saved_retry = core.DRIVER_RETRY_SECONDS
    saved_gen = core.credential_store.credential_generation
    core.DRIVER_RETRY_SECONDS = 0.1
    core.credential_store.credential_generation = lambda: "gen1"   # differs from last_gen
    try:
        cfg = _Cfg()
        outcome, gen = core._reconnect_wait(threading.Event(), cfg, 0, "gen0")
        assert outcome == "reload" and gen == "gen1" and cfg.reloaded is True
    finally:
        core.DRIVER_RETRY_SECONDS = saved_retry
        core.credential_store.credential_generation = saved_gen
