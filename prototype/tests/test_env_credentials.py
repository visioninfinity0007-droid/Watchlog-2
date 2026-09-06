"""Credential-storage and field-diagnostic regression tests (0.3.3).

Locks the change from DPAPI-encrypted storage to an ACL-restricted plaintext
env file that is read in *every* launch context (the root cause of the field
401: an agent started outside run-agent.ps1 had no password), plus the two
diagnostic fixes — the agent now labels a recorder 401 as wrong credentials,
and the port scan no longer crashes on a bytes banner. No hardware/network.
"""
import os
import sys
import tempfile
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import discover  # noqa: E402
import drivers  # noqa: E402
import setup_backend as backend  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret  # noqa: E402
from windows_secret import (NVR_PASSWORD_ENV_KEY, read_env_file,  # noqa: E402
                            write_env_file)

# These tests validate credential value I/O, not the Windows ACL. The real
# icacls lock restricts the file to SYSTEM+Administrators, which a non-elevated
# test process cannot then read back — so no-op it here. The ACL is asserted
# separately by the static installer-product contract, and enforced by the OS.
windows_secret._lock_acl = lambda _path: None


# --- 1. env file round-trips awkward passwords verbatim --------------------

def test_env_file_roundtrip_preserves_special_chars():
    for pw in ["p@ss:word", "a b c", "has#hash", "50%more", "trailing ", "eq=in=side"]:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "watchlog.env"
            write_env_file(path, {NVR_PASSWORD_ENV_KEY: pw})
            assert read_env_file(path)[NVR_PASSWORD_ENV_KEY] == pw


# --- 2. read_env_file ignores comments and blank lines ---------------------

def test_read_env_file_ignores_comments_and_blanks():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "watchlog.env"
        path.write_text("# a comment\n\nWATCHLOG_NVR_PASSWORD=secret\n  # indented\n",
                        encoding="utf-8")
        assert read_env_file(path) == {"WATCHLOG_NVR_PASSWORD": "secret"}


# --- 3. legacy plaintext-INI password migrates into the env file -----------

def test_migrate_plaintext_ini_to_env():
    saved = os.environ.get("PROGRAMDATA")
    with tempfile.TemporaryDirectory() as d:
        os.environ["PROGRAMDATA"] = d
        try:
            ini = Path(d) / "watchlog.ini"
            ini.write_text("[watchlog]\nnvr_url = http://x\nnvr_password = legacy123\n",
                           encoding="utf-8")
            assert backend.migrate_legacy_credentials(ini) is True
            assert read_env_file(backend.credential_path())[NVR_PASSWORD_ENV_KEY] == "legacy123"
            text = ini.read_text(encoding="utf-8")
            assert "nvr_password = legacy123" not in text
            assert "nvr_password_protected = env-file" in text
            # nothing left to migrate on a second run
            assert backend.migrate_legacy_credentials(ini) is False
        finally:
            if saved is None:
                os.environ.pop("PROGRAMDATA", None)
            else:
                os.environ["PROGRAMDATA"] = saved


# --- 4. agent loads the env credential in ANY launch context ---------------

def test_agent_loads_env_credential_and_respects_override():
    saved_pd = os.environ.get("PROGRAMDATA")
    saved_pw = os.environ.get(NVR_PASSWORD_ENV_KEY)
    with tempfile.TemporaryDirectory() as d:
        os.environ["PROGRAMDATA"] = d
        os.environ.pop(NVR_PASSWORD_ENV_KEY, None)
        try:
            wl = Path(d) / "WatchLog"
            wl.mkdir(parents=True, exist_ok=True)
            (wl / "watchlog.env").write_text(f"{NVR_PASSWORD_ENV_KEY}=fromfile\n",
                                             encoding="utf-8")
            core._load_program_credentials()
            assert os.environ.get(NVR_PASSWORD_ENV_KEY) == "fromfile"
            # an already-set value must win over the file
            os.environ[NVR_PASSWORD_ENV_KEY] = "explicit"
            core._load_program_credentials()
            assert os.environ[NVR_PASSWORD_ENV_KEY] == "explicit"
        finally:
            if saved_pd is None:
                os.environ.pop("PROGRAMDATA", None)
            else:
                os.environ["PROGRAMDATA"] = saved_pd
            if saved_pw is None:
                os.environ.pop(NVR_PASSWORD_ENV_KEY, None)
            else:
                os.environ[NVR_PASSWORD_ENV_KEY] = saved_pw


# --- 5. autodetect labels a recorder 401 as wrong credentials --------------

class _AuthFail:
    name = "fake-cgi"
    def __init__(self, *a, **k): pass
    def probe(self): raise drivers.DriverError("http://x/cgi: HTTP 401 unauthorized")
    def close(self): pass


class _NotVendor:
    name = "other"
    def __init__(self, *a, **k): pass
    def probe(self): raise drivers.DriverError("http://x: HTTP 404 not found")
    def close(self): pass


def _autodetect_with(order, **kw):
    original = drivers.DETECT_ORDER
    try:
        drivers.DETECT_ORDER = order
        drivers.autodetect("http://x", "admin", "pw", **kw)
    finally:
        drivers.DETECT_ORDER = original


def test_autodetect_classifies_401_as_credentials():
    try:
        _autodetect_with([_AuthFail])
        assert False, "expected DriverError"
    except drivers.DriverError as exc:
        assert "rejected the username or password" in str(exc)


def test_autodetect_non_auth_stays_generic():
    try:
        _autodetect_with([_NotVendor])
        assert False, "expected DriverError"
    except drivers.DriverError as exc:
        assert "no driver recognised the device" in str(exc)


# --- 6. scan vendor guess tolerates a bytes banner (no TypeError) ----------

def test_guess_tolerates_bytes_banner():
    # A failed/partial probe can hand _guess raw bytes; it must not raise.
    assert discover._guess(b"Server: Boa/0.94", None) == "generic embedded web server"
    assert discover._guess(b"\xff\xfe noise", None, "") is None
