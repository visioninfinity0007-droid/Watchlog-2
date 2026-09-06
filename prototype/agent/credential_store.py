"""WatchLog local credential/identity store orchestration (0.3.4+).

On-disk footprint (secrets isolated in a hardened directory):

    %ProgramData%\\WatchLog\\agent_state.json            non-secret identity/config
    %ProgramData%\\WatchLog\\Secrets\\agent_key.dpapi     encrypted agent cloud key
    %ProgramData%\\WatchLog\\Secrets\\nvr_credential.dpapi encrypted {username,password,version}

Invariants (from the Phase-2 security review):

  * The DPAPI credential is AUTHORITATIVE once published. If it exists and
    decrypts, legacy plaintext is never read again — only cleaned up. A corrupt
    DPAPI credential is a repair-required error (SecretError), NEVER a downgrade
    to plaintext.

  * Legacy migration is idempotent and crash-recoverable: read legacy -> publish
    + verify DPAPI -> delete legacy -> verify absent. A crash between publish and
    delete is safe: the next start sees the authoritative DPAPI credential and
    removes the stale plaintext without using it. (No impossible cross-file
    atomic transaction is attempted.)

  * "Enrolled" locally requires BOTH a valid agent_state.json AND a decryptable
    agent_key.dpapi. agent_state.json alone is a half-installed state and must
    not let Setup skip enrollment or the agent pretend it has an identity.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path

from windows_secret import (CREDENTIAL_STORE_VERSION, SecretError,
                            read_json_secret, read_secret, unprotect_bytes,
                            write_json_secret, write_secret)

_NON_SECRET_STATE_KEYS = (
    "agent_id", "tenant_id", "site_id", "enrolled_at", "agent_version",
    "credential_store_version",
)


def data_dir() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog"


def secrets_dir() -> Path:
    return data_dir() / "Secrets"


def state_path() -> Path:
    return data_dir() / "agent_state.json"


def agent_key_path() -> Path:
    return secrets_dir() / "agent_key.dpapi"


def nvr_credential_path() -> Path:
    return secrets_dir() / "nvr_credential.dpapi"


def legacy_env_path() -> Path:
    return data_dir() / "watchlog.env"           # 0.3.3 plaintext (insecure)


def legacy_dpapi_path() -> Path:
    return data_dir() / "nvr_password.dpapi"      # 0.2-0.3.2 single DPAPI blob


# --- recorder credential (username + password stored atomically together) ---

def save_nvr_credential(username: str, password: str) -> None:
    write_json_secret(nvr_credential_path(), {
        "username": username,
        "password": password,
        "credential_version": CREDENTIAL_STORE_VERSION,
    })


def stored_nvr_username() -> str:
    """Recorder username from the credential blob if present (no migration side
    effect). '' if absent or unreadable — used only for setup-UI pre-fill."""
    path = nvr_credential_path()
    if not path.exists():
        return ""
    try:
        return read_json_secret(path).get("username", "") or ""
    except SecretError:
        return ""


def load_nvr_credential(config_ini_path: Path | None = None) -> dict | None:
    """Return {'username','password',...} or None if no credential exists.

    DPAPI is authoritative. A corrupt DPAPI blob raises SecretError (never falls
    back to plaintext). If no DPAPI credential exists, a legacy credential is
    migrated in idempotently.
    """
    path = nvr_credential_path()
    if path.exists():
        cred = read_json_secret(path)             # raises SecretError if corrupt -> NO fallback
        _cleanup_legacy(config_ini_path)          # authoritative -> remove stale plaintext
        return cred
    if migrate_legacy_if_needed(config_ini_path):
        return read_json_secret(path)
    return None


def migrate_legacy_if_needed(config_ini_path: Path | None) -> bool:
    """Migrate a legacy plaintext / old-DPAPI recorder credential into the split
    DPAPI store. Idempotent, fail-closed, crash-recoverable. Returns True if a
    migration was performed. If migration fails the legacy source is preserved
    (never delete-first) and SecretError propagates."""
    path = nvr_credential_path()
    if path.exists():
        # Authoritative DPAPI already present (possibly from a crashed prior
        # migration). Prove it is readable, then clean up any legacy remnants.
        read_json_secret(path)                    # raises if corrupt -> repair required
        _cleanup_legacy(config_ini_path)
        return False

    username, password = _read_legacy(config_ini_path)
    if not password:
        return False

    save_nvr_credential(username or "admin", password)   # publish + full verify (fail-closed)
    check = read_json_secret(path)               # verify the new authoritative credential
    if check.get("password") != password:
        raise SecretError("legacy credential migration verification failed")
    _cleanup_legacy(config_ini_path)             # only now delete legacy, then verify absent
    return True


def _read_legacy(config_ini_path: Path | None) -> tuple[str, str]:
    """Best-effort read of a legacy recorder credential. Returns (username, password);
    password is '' if none found. Never used once a DPAPI credential exists."""
    username = ""
    password = ""
    if config_ini_path and config_ini_path.exists():
        ini = configparser.ConfigParser()
        try:
            ini.read(config_ini_path, encoding="utf-8-sig")
            if ini.has_section("watchlog"):
                username = ini["watchlog"].get("nvr_username", "") or ""
                password = ini["watchlog"].get("nvr_password", "") or ""   # oldest plaintext
        except configparser.Error:
            pass
    if not password:
        env = _read_env_file(legacy_env_path())
        password = env.get("WATCHLOG_NVR_PASSWORD", "") or ""
    if not password and legacy_dpapi_path().exists():
        try:
            password = unprotect_bytes(legacy_dpapi_path().read_bytes()).decode("utf-8")
        except (SecretError, OSError, UnicodeDecodeError):
            password = ""
    return username, password


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw or raw.lstrip().startswith("#") or "=" not in raw:
                continue
            key, _, value = raw.partition("=")
            out[key.strip()] = value
    except OSError:
        pass
    return out


def _cleanup_legacy(config_ini_path: Path | None) -> None:
    """Remove legacy plaintext/old-DPAPI credential sources and verify absence.
    Only called once the DPAPI credential is authoritative."""
    for legacy in (legacy_env_path(), legacy_dpapi_path()):
        try:
            legacy.unlink(missing_ok=True)
        except OSError:
            pass
        if legacy.exists():
            raise SecretError(f"could not remove legacy credential file {legacy}")
    if config_ini_path and config_ini_path.exists():
        ini = configparser.ConfigParser()
        try:
            ini.read(config_ini_path, encoding="utf-8-sig")
            if ini.has_section("watchlog") and ini["watchlog"].get("nvr_password"):
                ini["watchlog"].pop("nvr_password", None)
                ini["watchlog"]["nvr_password_protected"] = "dpapi-secrets"
                tmp = config_ini_path.with_suffix(config_ini_path.suffix + ".tmp")
                with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                    ini.write(handle)
                tmp.replace(config_ini_path)
        except (configparser.Error, OSError):
            pass


# --- agent cloud key (bearer secret) ---

def save_agent_key(key: str) -> None:
    write_secret(agent_key_path(), key.encode("utf-8"))


def load_agent_key() -> str | None:
    """Decrypted agent key, or None if absent. Raises SecretError if present but
    corrupt (repair-required, no fallback)."""
    if not agent_key_path().exists():
        return None
    return read_secret(agent_key_path()).decode("utf-8")


# --- non-secret state ---

def save_state(state: dict) -> None:
    """Persist ONLY non-secret identity/config. The agent key (if present in the
    dict) is written separately to agent_key.dpapi, never into agent_state.json."""
    if state.get("agent_key"):
        save_agent_key(state["agent_key"])
    public = {k: state[k] for k in _NON_SECRET_STATE_KEYS if k in state}
    public.setdefault("credential_store_version", CREDENTIAL_STORE_VERSION)
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(public, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load_state(include_key: bool = True) -> dict | None:
    """Load the non-secret state, injecting the decrypted agent_key when present
    and requested. Returns None if there is no state at all."""
    path = state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if include_key:
        key = load_agent_key()          # may raise SecretError if corrupt -> surfaced
        if key:
            state["agent_key"] = key
    return state


def is_enrolled() -> bool:
    """Local identity is usable only when BOTH the non-secret state is valid AND
    the agent key decrypts. Prevents the half-installed state that caused earlier
    reinstall problems."""
    state = load_state(include_key=False)
    if not (state and state.get("agent_id") and state.get("site_id")):
        return False
    try:
        return bool(load_agent_key())
    except SecretError:
        return False                     # corrupt key -> not a usable identity


# --- interruptible circuit-breaker support ---

def credential_generation() -> str:
    """A cheap generation token that changes whenever Setup rewrites the recorder
    credential. The agent watches this to wake its auth breaker immediately on a
    credential change instead of waiting out a 30-minute backoff.

    The token folds in a hash of the (still-ENCRYPTED) blob bytes — no decryption,
    the file is tiny — so it changes whenever the credential CONTENT changes, even
    if two writes land in the same filesystem mtime tick at the same size (mtime+
    size alone is not guaranteed to differ). This is what makes 'the breaker wakes
    on credential replacement' reliable rather than timing-dependent."""
    path = nvr_credential_path()
    if not path.exists():
        return "absent"
    try:
        data = path.read_bytes()
        digest = hashlib.blake2b(data, digest_size=8).hexdigest()
        return f"{path.stat().st_mtime_ns}:{len(data)}:{digest}"
    except OSError:
        return "unknown"
