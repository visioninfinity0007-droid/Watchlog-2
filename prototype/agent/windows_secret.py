"""Windows-local secret handling for the WatchLog installer.

The recorder password never needs to leave the site PC. Customer releases store
it as a machine-scoped DPAPI blob in ProgramData so both the elevated setup UI
and the SYSTEM background task can use it. The blob cannot be decrypted on a
different Windows machine. Because LOCAL_MACHINE DPAPI can be unwrapped by
other accounts on the same PC, the file ACL is restricted to SYSTEM and local
Administrators.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

CRYPTPROTECT_LOCAL_MACHINE = 0x4
SYSTEM_SID = "*S-1-5-18"
ADMINISTRATORS_SID = "*S-1-5-32-544"


class SecretError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _make_blob(payload: bytes):
    buffer = ctypes.create_string_buffer(payload)
    blob = DATA_BLOB(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def protect_bytes(payload: bytes) -> bytes:
    if os.name != "nt":
        raise SecretError("DPAPI is available only on Windows")
    if not payload:
        raise SecretError("refusing to protect an empty credential")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, source_buffer = _make_blob(payload)
    result = DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(source), "WatchLog recorder credential", None, None, None,
        CRYPTPROTECT_LOCAL_MACHINE, ctypes.byref(result))
    _ = source_buffer
    if not ok:
        raise SecretError(f"CryptProtectData failed ({kernel32.GetLastError()})")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        if result.pbData:
            kernel32.LocalFree(result.pbData)


def unprotect_bytes(payload: bytes) -> bytes:
    if os.name != "nt":
        raise SecretError("DPAPI is available only on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, source_buffer = _make_blob(payload)
    result = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result))
    _ = source_buffer
    if not ok:
        raise SecretError(f"CryptUnprotectData failed ({kernel32.GetLastError()})")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        if result.pbData:
            kernel32.LocalFree(result.pbData)


def protect_text(secret: str) -> bytes:
    return protect_bytes(secret.encode("utf-8"))


def unprotect_text(payload: bytes) -> str:
    try:
        return unprotect_bytes(payload).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretError("protected recorder credential is unreadable") from exc


def _lock_acl(path: Path) -> None:
    if os.name != "nt":
        return
    command = [
        "icacls", str(path), "/inheritance:r", "/grant:r",
        f"{SYSTEM_SID}:(F)", f"{ADMINISTRATORS_SID}:(F)",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise SecretError("Windows could not restrict access to the protected recorder credential")


def write_secret(path: Path, secret: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(protect_text(secret))
    tmp.replace(path)
    _lock_acl(path)


def read_secret(path: Path) -> str:
    if not path.exists():
        raise SecretError(f"protected recorder credential is missing at {path}")
    return unprotect_text(path.read_bytes())
