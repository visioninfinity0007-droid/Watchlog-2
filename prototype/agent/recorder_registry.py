"""Local multi-recorder registry.

This module stores NON-SECRET recorder configuration only. Recorder credentials
remain in per-recorder DPAPI blobs managed by credential_store.

The first rollout is deliberately staged:
- legacy single-recorder config/credential stays authoritative for the 5.0.27 runtime;
- migrate_legacy_singleton() copies + verifies that state into recorders.json and a
  per-recorder credential blob;
- no legacy file is retired until a later runtime cutover explicitly proves the
  Agent can run from the registry.

That keeps Repair/Upgrade fail-safe while multi-recorder runtime is developed.
"""
from __future__ import annotations

import configparser
import ipaddress
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import credential_store
from windows_secret import SecretError

REGISTRY_SCHEMA = "watchlog.recorders.v1"

# Owners a privileged reader may trust: SYSTEM, BUILTIN\Administrators, TrustedInstaller.
_TRUSTED_OWNER_SIDS = frozenset({
    "S-1-5-18",
    "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
})


class RegistryUntrusted(ValueError):
    """recorders.json exists but a non-administrator could have written it."""


class DuplicateRecorder(ValueError):
    """The recorder being added or re-pointed is already in the registry."""


class RecorderIdentityConflict(ValueError):
    """A probe saw a different physical recorder than the one this row names."""


def data_dir() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog"


def registry_path() -> Path:
    return data_dir() / "recorders.json"


def _validate_record(rec: dict) -> dict:
    local_id = str(rec.get("local_id") or "").strip()
    if not local_id:
        raise ValueError("recorder local_id is required")
    try:
        uuid.UUID(local_id)
    except ValueError as exc:
        raise ValueError("recorder local_id must be a UUID") from exc

    display_name = str(rec.get("display_name") or "").strip()
    if not display_name:
        raise ValueError("recorder display_name is required")

    out = {
        "local_id": local_id,
        "cloud_recorder_id": (str(rec.get("cloud_recorder_id")).strip()
                              if rec.get("cloud_recorder_id") else None),
        "display_name": display_name,
        "url": str(rec.get("url") or "").strip(),
        "driver": str(rec.get("driver") or "auto").strip().lower() or "auto",
        "vendor": (str(rec.get("vendor")).strip() if rec.get("vendor") else None),
        "model": (str(rec.get("model")).strip() if rec.get("model") else None),
        "firmware": (str(rec.get("firmware")).strip() if rec.get("firmware") else None),
        "identity_fingerprint": (str(rec.get("identity_fingerprint")).strip()
                                 if rec.get("identity_fingerprint") else None),
        "is_primary": bool(rec.get("is_primary", False)),
        # Backward-compatible local backfill: before primary reassignment existed,
        # the original primary was necessarily the legacy continuity owner.
        "continuity_owner": bool(
            rec.get("continuity_owner", rec.get("is_primary", False))
        ),
        "is_configured": bool(rec.get("is_configured", True)),
    }
    return out


def validate_registry(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("recorder registry must be an object")
    if payload.get("schema") != REGISTRY_SCHEMA:
        raise ValueError("unsupported recorder registry schema")

    rows = payload.get("recorders")
    if not isinstance(rows, list):
        raise ValueError("recorder registry recorders must be an array")

    normalized = [_validate_record(row) for row in rows]
    ids = [row["local_id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate recorder local_id")

    # Continuity is checked first: it is the immutable 5.1 invariant, so a
    # registry that disabled its continuity owner reports that, not the
    # primary rule it also breaks as a consequence.
    continuity = [row for row in normalized if row["continuity_owner"]]
    if normalized and len(continuity) != 1:
        raise ValueError(
            "a non-empty recorder registry needs exactly one continuity owner"
        )
    if continuity and not continuity[0]["is_configured"]:
        raise ValueError(
            "the continuity owner must remain configured in WatchLog 5.1"
        )

    configured_primaries = [
        row for row in normalized if row["is_primary"] and row["is_configured"]
    ]
    if len(configured_primaries) > 1:
        raise ValueError("at most one configured recorder may be primary")
    if normalized and not configured_primaries:
        raise ValueError("a non-empty recorder registry needs one configured primary")

    return {"schema": REGISTRY_SCHEMA, "recorders": normalized}


# --- registry file trust ----------------------------------------------------
#
# %ProgramData%\WatchLog is not ACL-hardened, so a standard local user could plant
# recorders.json for the SYSTEM Agent to read. A file whose owner is neither a
# trusted principal, nor an administrator, nor the reading account itself is
# rejected. Each lookup returns None when it cannot be performed (non-Windows,
# API failure, unreachable domain): unknown is not treated as proof either way.

def _sid_string(advapi32, kernel32, psid) -> str | None:
    import ctypes
    out = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)):
        return None
    try:
        return out.value
    finally:
        kernel32.LocalFree(out)


def _win_apis():
    import ctypes
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.LookupAccountSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_int),
    ]
    advapi32.LookupAccountSidW.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return advapi32, kernel32


def _file_owner_sid(path: Path) -> str | None:
    """String SID of the file's owner, or None when it cannot be read."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        advapi32, kernel32 = _win_apis()
        owner, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
        # SE_FILE_OBJECT=1, OWNER_SECURITY_INFORMATION=1
        if advapi32.GetNamedSecurityInfoW(str(path), 1, 1, ctypes.byref(owner),
                                          None, None, None, ctypes.byref(descriptor)) != 0:
            return None
        try:
            return _sid_string(advapi32, kernel32, owner)
        finally:
            kernel32.LocalFree(descriptor)
    except Exception:  # noqa: BLE001 — unknown, not proof
        return None


def _current_user_sid() -> str | None:
    """String SID of the account this process runs as, or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        advapi32, kernel32 = _win_apis()
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            return None
        try:
            size = wintypes.DWORD(0)
            advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))   # TokenUser
            buf = ctypes.create_string_buffer(size.value or 256)
            if not advapi32.GetTokenInformation(token, 1, buf, ctypes.sizeof(buf), ctypes.byref(size)):
                return None
            psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]   # TOKEN_USER.User.Sid
            return _sid_string(advapi32, kernel32, psid)
        finally:
            kernel32.CloseHandle(token)
    except Exception:  # noqa: BLE001
        return None


def _account_name(advapi32, psid) -> tuple[str, str] | None:
    import ctypes
    from ctypes import wintypes
    name, domain = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
    n_len, d_len, use = wintypes.DWORD(256), wintypes.DWORD(256), ctypes.c_int()
    if not advapi32.LookupAccountSidW(None, psid, name, ctypes.byref(n_len),
                                      domain, ctypes.byref(d_len), ctypes.byref(use)):
        return None
    return name.value, domain.value


def _is_local_admin(sid: str) -> bool | None:
    """True/False when the account is (not) a local Administrators member, directly or
    through a group; None when that cannot be determined."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        advapi32, kernel32 = _win_apis()
        netapi32 = ctypes.WinDLL("netapi32", use_last_error=True)
        netapi32.NetUserGetLocalGroups.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ]
        netapi32.NetUserGetLocalGroups.restype = wintypes.DWORD
        netapi32.NetApiBufferFree.argtypes = [ctypes.c_void_p]

        def lookup(text):
            psid = ctypes.c_void_p()
            if not advapi32.ConvertStringSidToSidW(text, ctypes.byref(psid)):
                return None
            try:
                return _account_name(advapi32, psid)
            finally:
                kernel32.LocalFree(psid)

        admins = lookup("S-1-5-32-544")      # localized group name
        account = lookup(sid)
        if not admins or not account:
            return None
        buf = ctypes.c_void_p()
        read, total = wintypes.DWORD(), wintypes.DWORD()
        # level 0, LG_INCLUDE_INDIRECT=1, MAX_PREFERRED_LENGTH
        status = netapi32.NetUserGetLocalGroups(
            None, f"{account[1]}\\{account[0]}" if account[1] else account[0],
            0, 1, ctypes.byref(buf), 0xFFFFFFFF, ctypes.byref(read), ctypes.byref(total))
        if status != 0:
            return None
        try:
            names = ctypes.cast(buf, ctypes.POINTER(ctypes.c_wchar_p))
            groups = {str(names[i] or "").lower() for i in range(read.value)}
        finally:
            netapi32.NetApiBufferFree(buf)
        return admins[0].lower() in groups
    except Exception:  # noqa: BLE001
        return None


_TRUST_CACHE: dict = {}


def registry_owner_trusted(path: Path) -> bool | None:
    """False only when the owner is provably not trusted; None when unknown."""
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0))
    except OSError:
        return None
    if key in _TRUST_CACHE:
        return _TRUST_CACHE[key]
    owner = _file_owner_sid(path)
    if owner is None:
        verdict = None
    elif owner in _TRUSTED_OWNER_SIDS or owner == _current_user_sid():
        verdict = True
    else:
        verdict = _is_local_admin(owner)
    # Remember only non-negative verdicts for this exact file: an untrusted file
    # is re-checked every time, so an ownership repair is noticed at once.
    _TRUST_CACHE.clear()
    if verdict is not False:
        _TRUST_CACHE[key] = verdict
    return verdict


def load_registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {"schema": REGISTRY_SCHEMA, "recorders": []}
    if registry_owner_trusted(path) is False:
        raise RegistryUntrusted(
            "the recorder configuration on this PC is not owned by SYSTEM or an "
            "administrator; re-run WatchLog Setup to repair it"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read recorder registry: {exc}") from exc
    return validate_registry(raw)


def save_registry(payload: dict) -> dict:
    normalized = validate_registry(payload)
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusively created, unpredictable temp file: a pre-created
    # recorders.json.tmp (owned by whoever planted it) can never become the registry.
    fd, tmp_name = tempfile.mkstemp(prefix=".recorders.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(normalized, separators=(",", ":")))
        # Prove the exact staged bytes parse and satisfy the schema before publish.
        validate_registry(json.loads(tmp.read_text(encoding="utf-8")))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return normalized


def recorders() -> list[dict]:
    return list(load_registry()["recorders"])


def recorder(local_id: str) -> dict | None:
    wanted = str(local_id)
    for row in recorders():
        if row["local_id"] == wanted:
            return row
    return None


def primary_recorder() -> dict | None:
    for row in recorders():
        if row["is_primary"] and row["is_configured"]:
            return row
    return None


def continuity_recorder() -> dict | None:
    for row in recorders():
        if row.get("continuity_owner"):
            return row
    return None


def apply_cloud_mapping(mapping: dict) -> dict:
    """Persist wl_sync_recorders local_id -> cloud recorder UUID mapping.

    Mapping is fail-closed:
    - every key must already exist locally;
    - every value must be a UUID;
    - one cloud recorder cannot bind to two local recorders;
    - an already-bound local recorder cannot silently change cloud identity.
    """
    if not isinstance(mapping, dict):
        raise ValueError("recorder cloud mapping must be an object")

    current = load_registry()
    rows = list(current["recorders"])
    by_local = {row["local_id"]: row for row in rows}

    unknown = set(str(k) for k in mapping) - set(by_local)
    if unknown:
        raise ValueError("cloud mapping contains unknown local recorder")

    normalized: dict[str, str] = {}
    for local_id, cloud_id in mapping.items():
        local_id = str(local_id)
        cloud_id = str(cloud_id or "").strip()
        try:
            parsed = str(uuid.UUID(cloud_id))
        except ValueError as exc:
            raise ValueError("cloud recorder id must be a UUID") from exc
        normalized[local_id] = parsed

    if len(set(normalized.values())) != len(normalized):
        raise ValueError("duplicate cloud recorder id in mapping")

    for local_id, cloud_id in normalized.items():
        existing = by_local[local_id].get("cloud_recorder_id")
        if existing and str(existing) != cloud_id:
            raise ValueError("cloud recorder identity drift")
        by_local[local_id]["cloud_recorder_id"] = cloud_id

    all_cloud = [
        str(row["cloud_recorder_id"])
        for row in rows if row.get("cloud_recorder_id")
    ]
    if len(all_cloud) != len(set(all_cloud)):
        raise ValueError("duplicate cloud recorder id in registry")

    return save_registry({"schema": REGISTRY_SCHEMA, "recorders": rows})


def update_observed_identity(local_id: str, *, vendor=None, model=None,
                             firmware=None, driver=None,
                             identity_fingerprint=None,
                             verified_by_setup: bool = False) -> dict:
    """Update non-secret observed recorder facts for one local recorder.

    A runtime probe never replaces a known serial with a different one, and never gives
    this row another row's serial: the address is configuration, not identity, so another
    recorder answering at it after an address swap raises RecorderIdentityConflict and
    nothing is saved. Setup passes ``verified_by_setup`` after the operator tested this
    recorder again."""
    current = load_registry()
    found = False
    rows = []
    observed = _serial(identity_fingerprint)
    if observed and not verified_by_setup:
        for row in current["recorders"]:
            if row["local_id"] != str(local_id):
                if _serial(row.get("identity_fingerprint")) == observed:
                    raise RecorderIdentityConflict(
                        "the device at this recorder's address is another recorder of this site")
            elif (_serial(row.get("identity_fingerprint")) or observed) != observed:
                raise RecorderIdentityConflict(
                    "the device at this recorder's address reports a different serial number "
                    "than the saved recorder")
    for row in current["recorders"]:
        row = dict(row)
        if row["local_id"] == str(local_id):
            found = True
            if vendor:
                row["vendor"] = str(vendor).strip()
            if model:
                row["model"] = str(model).strip()
            if firmware:
                row["firmware"] = str(firmware).strip()
            if driver:
                row["driver"] = str(driver).strip().lower()
            if identity_fingerprint:
                row["identity_fingerprint"] = str(identity_fingerprint).strip()
        rows.append(row)
    if not found:
        raise ValueError("unknown local recorder")
    save_registry({"schema": REGISTRY_SCHEMA, "recorders": rows})
    return recorder(str(local_id))


def _read_legacy_public(config_path: Path) -> dict:
    section: dict[str, str] = {}
    if config_path.exists():
        ini = configparser.ConfigParser()
        ini.read(config_path, encoding="utf-8-sig")
        if ini.has_section("watchlog"):
            section = dict(ini.items("watchlog"))
    return section


def migrate_legacy_singleton(config_path: Path, *, local_id: str | None = None) -> dict | None:
    """Stage the current singleton recorder into the multi-recorder store.

    This is intentionally COPY-ONLY during the compatibility phase. The legacy
    singleton credential and watchlog.ini recorder keys are retained so 5.0.27
    continues to run unchanged. A later runtime-cutover migration may retire the
    legacy form only after the new runtime proves it can boot from this registry.

    ``local_id`` reuses a known local recorder id for the staged row (see
    reusable_continuity_id); otherwise a new one is minted.

    Returns the primary recorder row, or None when no legacy recorder exists.
    """
    existing = load_registry()
    if existing["recorders"]:
        primary = primary_recorder()
        if primary:
            # Fail closed if the registry says a credential exists but it cannot
            # be decrypted. Never fall back to the legacy singleton silently.
            credential_store.load_recorder_credential(primary["local_id"])
        return primary

    section = _read_legacy_public(config_path)
    url = str(section.get("nvr_url") or "").strip()
    driver = str(section.get("nvr_driver") or "auto").strip().lower() or "auto"

    # Ensure any older plaintext/old-DPAPI form has first reached the existing
    # authoritative singleton DPAPI blob.
    singleton = credential_store.load_nvr_credential(config_path)
    if not url and singleton is None:
        return None
    if singleton is None:
        raise SecretError("legacy recorder exists without a protected credential")

    local_id = str(uuid.UUID(str(local_id))) if local_id else str(uuid.uuid4())
    credential_store.save_recorder_credential(
        local_id,
        singleton.get("username") or "admin",
        singleton.get("password") or "",
    )
    check = credential_store.load_recorder_credential(local_id)
    if (check.get("username") != (singleton.get("username") or "admin")
            or check.get("password") != (singleton.get("password") or "")):
        raise SecretError("per-recorder credential verification failed")

    row = {
        "local_id": local_id,
        "cloud_recorder_id": None,
        "display_name": "Primary Recorder",
        "url": url,
        "driver": driver,
        "vendor": None,
        "model": None,
        "firmware": None,
        "identity_fingerprint": None,
        "is_primary": True,
        "continuity_owner": True,
        "is_configured": True,
    }
    save_registry({"schema": REGISTRY_SCHEMA, "recorders": [row]})

    # Re-read both artifacts after publish. If either is unreadable, surface a
    # repair-required failure. Legacy remains untouched so rollback is possible.
    loaded = primary_recorder()
    if loaded is None or loaded["local_id"] != local_id:
        raise ValueError("recorder registry publish verification failed")
    credential_store.load_recorder_credential(local_id)
    return loaded


def _endpoint(url) -> tuple[str, int] | None:
    """Normalised (host, port) of a recorder address.

    Scheme and host are case-insensitive, the port defaults by scheme, and any
    path, credentials or trailing slash are ignored, so "192.0.2.64",
    "HTTP://192.0.2.64:80/" and "http://192.0.2.64/ISAPI" are one endpoint."""
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").strip().lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if port is None:
        port = 443 if (parts.scheme or "").lower() == "https" else 80
    return host, port


def _serial(identity_fingerprint) -> str | None:
    text = str(identity_fingerprint or "").strip()
    if not text.lower().startswith("serial:"):
        return None
    return text[len("serial:"):].strip().upper() or None


def _duplicate_of(rows, url, identity_fingerprint=None, *, ignore_local_id=None):
    """The existing row that is the same physical recorder, if any.

    Same normalised endpoint, or the same serial number where both are known
    (a recorder that moved address after DHCP is still the same recorder)."""
    endpoint, serial = _endpoint(url), _serial(identity_fingerprint)
    for row in rows:
        if row["local_id"] == ignore_local_id:
            continue
        if endpoint and _endpoint(row.get("url")) == endpoint:
            return row
        if serial and _serial(row.get("identity_fingerprint")) == serial:
            return row
    return None


def _reject_duplicate(rows, url, identity_fingerprint=None, *, ignore_local_id=None) -> None:
    existing = _duplicate_of(rows, url, identity_fingerprint, ignore_local_id=ignore_local_id)
    if existing is None:
        return
    if not existing.get("is_configured"):
        raise DuplicateRecorder(
            "this recorder is already in WatchLog but disabled; re-enable it instead"
        )
    raise DuplicateRecorder("this recorder is already configured in WatchLog")


def reusable_continuity_id(url, identity_fingerprint=None) -> str | None:
    """The continuity recorder's local id in the registry about to be quarantined,
    for the freshly staged row of a reinstall whose site is unknown.

    WatchLog looks a recorder up by local id within one site, so on the same site
    the reused id re-attaches to the existing continuity recorder (its cameras and
    history) instead of creating a second one; on another site it is simply a new
    id. Its cloud id and credential are never carried over. Returns None for an
    unreadable or untrusted registry, or when the newly proven recorder is one of
    the registry's other recorders (that would graft it onto the continuity
    recorder's identity)."""
    try:
        rows = recorders()
    except Exception:  # noqa: BLE001 — never reuse ids from a file we cannot trust
        return None
    continuity = next((row for row in rows if row.get("continuity_owner")), None)
    if continuity is None:
        return None
    same = _duplicate_of(rows, url, identity_fingerprint)
    if same is not None and same["local_id"] != continuity["local_id"]:
        return None
    return continuity["local_id"]


def quarantine_registry() -> list[Path]:
    """Move the registry and the per-recorder state it owns aside; never delete.

    Used when the registry cannot belong to the installation being set up (left
    by an earlier install, another site, or unreadable). The secondary
    recorders' queued events move with it, so they can never drain into a
    different site. Returns the quarantined paths."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    moved = []
    for src in (registry_path(), data_dir() / "recorders"):
        if src.exists():
            dst, n = src.with_name(f"{src.name}.quarantine-{stamp}"), 0
            while dst.exists():                 # never overwrite an earlier quarantine
                n += 1
                dst = src.with_name(f"{src.name}.quarantine-{stamp}-{n}")
            os.replace(src, dst)
            moved.append(dst)
    return moved


def update_recorder_connection(local_id: str, *, url: str, driver: str,
                               username: str, password: str,
                               vendor: str | None = None, model: str | None = None,
                               firmware: str | None = None,
                               identity_fingerprint: str | None = None,
                               mirror_legacy: bool = False) -> dict:
    """Re-point one recorder at a freshly proven address and login.

    The registry is the authority for recorder address and credential. Local and
    cloud identity are kept; observed facts are replaced by what was just proven
    (unknown stays unknown). If the registry write fails, the credential (and,
    with mirror_legacy, the legacy singleton credential) is restored."""
    current = load_registry()
    wanted = str(local_id or "").strip()
    if not any(row["local_id"] == wanted for row in current["recorders"]):
        raise ValueError("recorder not found")
    address = str(url or "").strip()
    _reject_duplicate(current["recorders"], address, identity_fingerprint,
                      ignore_local_id=wanted)

    paths = [credential_store.recorder_credential_path(wanted)]
    if mirror_legacy:
        paths.append(credential_store.nvr_credential_path())
    snapshot = credential_store.snapshot_secret_files(paths)
    credential_store.replace_recorder_credential(
        wanted, username, password, mirror_legacy=mirror_legacy
    )

    def update(row):
        row["url"] = address
        row["driver"] = str(driver or "auto").strip().lower() or "auto"
        row["vendor"] = str(vendor).strip() if vendor else None
        row["model"] = str(model).strip() if model else None
        row["firmware"] = str(firmware).strip() if firmware else None
        row["identity_fingerprint"] = (
            str(identity_fingerprint).strip() if identity_fingerprint else None
        )

    try:
        return _replace_record(wanted, update)
    except Exception:
        credential_store.restore_secret_files(snapshot)
        raise


def add_recorder(*, display_name: str, url: str, driver: str,
                 username: str, password: str, is_primary: bool = False,
                 vendor: str | None = None, model: str | None = None,
                 firmware: str | None = None,
                 identity_fingerprint: str | None = None) -> dict:
    """Add a recorder locally without changing/removing existing history.

    Cloud recorder_id remains unset until the recorder-aware RPC sync assigns it.
    """
    current = load_registry()
    _reject_duplicate(current["recorders"], url, identity_fingerprint)

    if is_primary:
        for row in current["recorders"]:
            if row["is_primary"] and row["is_configured"]:
                raise ValueError("a configured primary recorder already exists")

    local_id = str(uuid.uuid4())
    credential_store.save_recorder_credential(local_id, username, password)
    credential_store.load_recorder_credential(local_id)  # prove before registry publish

    row = {
        "local_id": local_id,
        "cloud_recorder_id": None,
        "display_name": display_name,
        "url": str(url).strip(),
        "driver": str(driver or "auto").strip().lower() or "auto",
        "vendor": vendor,
        "model": model,
        "firmware": firmware,
        "identity_fingerprint": identity_fingerprint,
        "is_primary": is_primary,
        "continuity_owner": False,
        "is_configured": True,
    }

    next_rows = list(current["recorders"]) + [row]
    try:
        save_registry({"schema": REGISTRY_SCHEMA, "recorders": next_rows})
    except Exception:
        # Registry did not publish, so do not leave a credential that has no
        # non-secret identity record.
        try:
            credential_store.delete_recorder_credential(local_id)
        except OSError:
            pass
        raise
    return recorder(local_id) or row



def _replace_record(local_id: str, updater) -> dict:
    current = load_registry()
    wanted = str(local_id or "").strip()
    found = False
    rows = []
    for original in current["recorders"]:
        row = dict(original)
        if row["local_id"] == wanted:
            found = True
            updater(row)
        rows.append(row)
    if not found:
        raise ValueError("recorder not found")
    save_registry({"schema": REGISTRY_SCHEMA, "recorders": rows})
    result = recorder(wanted)
    if result is None:
        raise ValueError("recorder update did not persist")
    return result


def rename_recorder(local_id: str, display_name: str) -> dict:
    name = str(display_name or "").strip()
    if not name:
        raise ValueError("recorder display_name is required")
    return _replace_record(
        local_id,
        lambda row: row.__setitem__("display_name", name),
    )


def make_primary(local_id: str) -> dict:
    """Change operator-preferred primary without moving legacy continuity.

    Primary reassignment is allowed only after every configured recorder has a
    stable cloud identity. This prevents a pre-cutover primary switch from
    confusing adoption/stamping of the original singleton recorder.
    """
    current = load_registry()
    wanted = str(local_id or "").strip()
    target = next(
        (row for row in current["recorders"] if row["local_id"] == wanted),
        None,
    )
    if target is None or not target.get("is_configured"):
        raise ValueError("configured recorder not found")
    if not target.get("cloud_recorder_id"):
        raise ValueError(
            "this recorder must be linked to WatchLog before it can become primary"
        )
    unbound = [
        row for row in current["recorders"]
        if row.get("is_configured") and not row.get("cloud_recorder_id")
    ]
    if unbound:
        raise ValueError(
            "all configured recorders must be linked before changing primary"
        )

    rows = []
    for original in current["recorders"]:
        row = dict(original)
        row["is_primary"] = row["local_id"] == wanted
        # continuity_owner is intentionally untouched.
        rows.append(row)
    save_registry({"schema": REGISTRY_SCHEMA, "recorders": rows})
    result = recorder(wanted)
    if result is None:
        raise ValueError("primary recorder update did not persist")
    return result


def _check_can_disable(row: dict) -> None:
    if row.get("continuity_owner"):
        raise ValueError(
            "the original WatchLog recorder cannot be disabled in this release"
        )
    if row.get("is_primary"):
        raise ValueError(
            "choose another primary recorder before disabling this recorder"
        )
    if not row.get("cloud_recorder_id"):
        raise ValueError(
            "an unbound recorder must be rolled back instead of disabled"
        )


def planned_disable(local_id: str) -> dict:
    """Return the validated registry with this recorder disabled; nothing is saved.

    Setup sends this lifecycle change to WatchLog BEFORE committing it locally.
    """
    current = load_registry()
    wanted = str(local_id or "").strip()
    row = next((r for r in current["recorders"] if r["local_id"] == wanted), None)
    if row is None:
        raise ValueError("recorder not found")
    if row.get("is_configured"):
        _check_can_disable(row)
    rows = [
        dict(r, is_configured=False) if r["local_id"] == wanted else dict(r)
        for r in current["recorders"]
    ]
    return validate_registry({"schema": REGISTRY_SCHEMA, "recorders": rows})


def disable_recorder(local_id: str) -> dict:
    """Disable an ordinary recorder while preserving cloud/local history.

    5.1 never disables the immutable continuity owner. Retiring that recorder
    requires a separately designed quiesce + drain workflow so legacy
    spool/health evidence cannot be stranded.
    """
    row = recorder(local_id)
    if row is None:
        raise ValueError("recorder not found")
    if not row.get("is_configured"):
        return row
    _check_can_disable(row)
    return _replace_record(
        local_id,
        lambda item: item.__setitem__("is_configured", False),
    )


def enable_recorder(local_id: str) -> dict:
    """Re-enable a preserved recorder identity.

    Its own DPAPI credential must still decrypt; there is never a fallback to
    the primary/sibling credential.
    """
    row = recorder(local_id)
    if row is None:
        raise ValueError("recorder not found")
    if row.get("is_configured"):
        return row
    credential_store.load_recorder_credential(str(local_id))
    return _replace_record(
        local_id,
        lambda item: item.__setitem__("is_configured", True),
    )


def registry_cloud_descriptors(registry: dict | None = None) -> list[dict]:
    """Return the full non-secret registry state, including disabled recorders.

    This is the only descriptor surface suitable for lifecycle synchronization.
    Runtime RecorderContexts intentionally omit disabled recorders, so using
    context.cloud_descriptor() alone would leave a disabled cloud recorder
    incorrectly configured forever. ``registry`` describes a planned state
    (for example planned_disable()) instead of the saved one.
    """
    out = []
    rows = validate_registry(registry)["recorders"] if registry is not None else recorders()
    for row in rows:
        out.append({
            "local_key": row["local_id"],
            "display_name": row["display_name"],
            "vendor": row.get("vendor"),
            "model": row.get("model"),
            "driver": row.get("driver") or "auto",
            "firmware": row.get("firmware"),
            "identity_fingerprint": row.get("identity_fingerprint"),
            "is_primary": bool(row.get("is_primary")),
            "is_configured": bool(row.get("is_configured")),
        })
    return out


def remove_unbound_recorder(local_id: str) -> dict:
    """Rollback a recorder that has never acquired cloud identity.

    Bound recorders are historical entities and must be disabled through a
    governed cloud workflow instead of being deleted locally.
    """
    wanted = str(local_id or "").strip()
    current = load_registry()
    row = next((r for r in current["recorders"] if r["local_id"] == wanted), None)
    if row is None:
        raise ValueError("recorder not found")
    if row.get("cloud_recorder_id"):
        raise ValueError("cloud-bound recorder cannot be removed locally")
    if row.get("is_primary"):
        raise ValueError("primary recorder cannot be removed by rollback")

    remaining = [r for r in current["recorders"] if r["local_id"] != wanted]
    save_registry({"schema": REGISTRY_SCHEMA, "recorders": remaining})
    credential_store.delete_recorder_credential(wanted)
    return row
