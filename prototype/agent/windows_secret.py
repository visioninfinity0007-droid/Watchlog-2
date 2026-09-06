"""Windows-local secret store for WatchLog (0.3.4+).

The recorder credential and the agent's cloud key are stored as machine-scoped
DPAPI blobs (CryptProtectData, CRYPTPROTECT_LOCAL_MACHINE) inside a dedicated,
DACL-hardened directory: C:\\ProgramData\\WatchLog\\Secrets.

Threat model, stated plainly:
  * LocalMachine DPAPI lets EVERY elevated context on THIS Windows install
    decrypt the same blob (elevated Setup, the SYSTEM task, elevated Repair) —
    the deterministic multi-context read requirement — and nothing on a
    different install (machine binding).
  * BUT any user on the same computer who obtains a LocalMachine-DPAPI blob can
    decrypt it. The file/directory DACL is therefore the PRIMARY confidentiality
    boundary, not defense-in-depth. A single stray ``Users:Read`` ACE — of any
    right — breaks it.

Enforced here:
  * ACL hardening is REPLACEMENT-based: the DACL is rebuilt from scratch
    (inheritance disabled, all pre-existing ACEs purged, then exactly
    SYSTEM+Administrators FullControl). This repairs a previously-misconfigured
    directory instead of failing forever, and is applied+read-back-verified in
    one operation.
  * OWNERSHIP is set to Administrators and verified. A standard-user owner keeps
    implicit WRITE_DAC (the right to rewrite the DACL) and could re-grant itself,
    so a non-SYSTEM/Administrators owner is rejected — a perfect DACL is not
    enough without a trusted owner.
  * Verification fails closed unless the effective Allow set is EXACTLY SYSTEM +
    BUILTIN\\Administrators, each with FullControl, inheritance disabled — any
    other principal, of any right, is rejected.
  * The Secrets directory is hardened+verified BEFORE any blob is written, so a
    temp ciphertext inherits SYSTEM+Admins-only from its first byte.
  * Every write is transactional and FAILS CLOSED: encrypt -> temp -> secure+
    verify temp -> round-trip decrypt compare -> atomic replace -> secure+verify
    final. Any failure removes the temp and raises; an existing good secret is
    preserved; there is NO insecure fallback.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

CRYPTPROTECT_LOCAL_MACHINE = 0x4
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
_ALLOWED_SIDS = frozenset({SYSTEM_SID, ADMINISTRATORS_SID})

# Recorded in agent_state.json. 2 = split blobs in a hardened Secrets dir.
CREDENTIAL_STORE_VERSION = 2


class SecretError(RuntimeError):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _in_blob(payload: bytes):
    buf = ctypes.create_string_buffer(payload, len(payload))
    return _DATA_BLOB(len(payload), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def protect_bytes(payload: bytes) -> bytes:
    if os.name != "nt":
        raise SecretError("DPAPI is only available on Windows")
    if not payload:
        raise SecretError("refusing to protect an empty secret")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, _keep = _in_blob(payload)
    out = _DATA_BLOB()
    ok = crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None,
                                  CRYPTPROTECT_LOCAL_MACHINE, ctypes.byref(out))
    if not ok:
        raise SecretError(f"CryptProtectData failed ({kernel32.GetLastError()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        if out.pbData:
            kernel32.LocalFree(out.pbData)


def unprotect_bytes(blob: bytes) -> bytes:
    if os.name != "nt":
        raise SecretError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, _keep = _in_blob(blob)
    out = _DATA_BLOB()
    ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                    ctypes.byref(out))
    if not ok:
        raise SecretError(f"CryptUnprotectData failed ({kernel32.GetLastError()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        if out.pbData:
            kernel32.LocalFree(out.pbData)


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# Rebuild the DACL from scratch (purge everything, disable inheritance, grant
# ONLY SYSTEM + Administrators FullControl), then read it back — all in one call.
# Replacement-based so a previously-misconfigured object (e.g. a stray Users ACE)
# is repaired, not merely left in place for the verifier to reject forever.
_SECURE_PS = (
    "$ErrorActionPreference='Stop';"
    "$p=$env:WL_ACL_PATH; $container=($env:WL_ACL_CONTAINER -eq '1');"
    "$acl=Get-Acl -LiteralPath $p;"
    "$acl.SetOwner((New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')));"  # owner=Administrators (owner holds implicit WRITE_DAC)
    "$acl.SetAccessRuleProtection($true,$false);"                       # disable inheritance, drop inherited
    "foreach($r in @($acl.Access)){ [void]$acl.RemoveAccessRule($r) };" # purge every existing ACE
    "if($container){$inh='ContainerInherit,ObjectInherit'}else{$inh='None'};"
    "foreach($sid in @('S-1-5-18','S-1-5-32-544')){"
    "  $id=New-Object System.Security.Principal.SecurityIdentifier($sid);"
    "  $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($id,'FullControl',$inh,'None','Allow'))) };"
    "Set-Acl -LiteralPath $p -AclObject $acl;"
    "$v=Get-Acl -LiteralPath $p; $aces=@();"                            # read back for verification
    "$owner=$v.GetOwner([Security.Principal.SecurityIdentifier]).Value;"
    "foreach($r in $v.Access){ if($r.AccessControlType -eq 'Allow'){"
    "  $s=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value;"
    "  $f=(($r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl)"
    "     -eq [Security.AccessControl.FileSystemRights]::FullControl);"
    "  $aces+=[ordered]@{sid=$s; full=[bool]$f} } };"
    "[ordered]@{protected=[bool]$v.AreAccessRulesProtected; owner=$owner; aces=@($aces)} | ConvertTo-Json -Compress"
)


def _secure_and_verify(path: Path, container: bool) -> None:
    """Apply the exact SYSTEM+Admins DACL from scratch, then verify the read-back.
    Fail closed unless inheritance is disabled AND the effective Allow set is
    EXACTLY SYSTEM + BUILTIN\\Administrators, each FullControl (any other
    principal, any right, is rejected)."""
    env = dict(os.environ, WL_ACL_PATH=str(path), WL_ACL_CONTAINER=("1" if container else "0"))
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SECURE_PS],
        capture_output=True, text=True, timeout=30, env=env, creationflags=_no_window())
    if result.returncode != 0:
        raise SecretError(f"could not secure/verify DACL of {path}: {(result.stderr or result.stdout).strip()}")
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise SecretError(f"unreadable DACL read-back for {path}: {exc}") from exc
    if not data.get("protected"):
        raise SecretError(f"{path}: DACL inheritance is not disabled (not fail-closed)")
    owner = str(data.get("owner"))
    if owner not in _ALLOWED_SIDS:
        raise SecretError(
            f"{path}: owner is {owner}, must be SYSTEM or Administrators — the owner "
            f"holds implicit WRITE_DAC and could re-grant itself access")
    aces = data.get("aces") or []
    if isinstance(aces, dict):          # ConvertTo-Json emits a bare object for one ACE
        aces = [aces]
    allow_sids = {str(a.get("sid")) for a in aces}
    if allow_sids != _ALLOWED_SIDS:     # ANY other principal, ANY right, leaks the blob
        raise SecretError(
            f"{path}: DACL grants to unexpected principal(s) {sorted(allow_sids)}; "
            f"only {sorted(_ALLOWED_SIDS)} may appear")
    full = {str(a.get("sid")) for a in aces if a.get("full")}
    if not _ALLOWED_SIDS.issubset(full):
        raise SecretError(f"{path}: SYSTEM and Administrators must each hold FullControl (have {sorted(full)})")


def ensure_secure_dir(directory: Path) -> None:
    """Create and harden (replacement-based) the secrets directory, then verify.
    Idempotent and self-repairing. Must succeed before any secret is written."""
    if os.name != "nt":
        raise SecretError("the WatchLog secret store requires Windows")
    directory.mkdir(parents=True, exist_ok=True)
    _secure_and_verify(directory, container=True)


def write_secret(path: Path, payload: bytes) -> None:
    """Transactionally publish an encrypted, ACL-restricted secret. FAIL CLOSED.

    The parent directory is hardened+verified first, so the temp ciphertext is
    SYSTEM+Admins-only from creation. On any failure the temp is removed and
    SecretError is raised; the final path (an existing good secret) is untouched.
    """
    if os.name != "nt":
        raise SecretError("the WatchLog secret store requires Windows")
    ensure_secure_dir(path.parent)                     # 0. hardened+verified dir
    tmp = path.parent / (path.name + ".tmp")
    try:
        blob = protect_bytes(payload)                  # 1. encrypt in memory
        tmp.write_bytes(blob)                           # 2. temp (inherits SYSTEM+Admins)
        _secure_and_verify(tmp, container=False)        # 3+4. lock + verify temp
        if unprotect_bytes(tmp.read_bytes()) != payload:    # 5. round-trip compare
            raise SecretError("round-trip verification failed")
        os.replace(tmp, path)                           # 6. atomic publish
        _secure_and_verify(path, container=False)       # 7. lock + verify final
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    # CPython cannot reliably zero an immutable bytes buffer; the plaintext
    # reference is released when this frame returns. Callers must not retain it.


def read_secret(path: Path) -> bytes:
    """Decrypt a secret. Raises SecretError (a controlled error) if missing or
    corrupt — never returns a fallback value (no downgrade to plaintext)."""
    if not path.exists():
        raise SecretError(f"secret missing: {path}")
    try:
        return unprotect_bytes(path.read_bytes())
    except SecretError:
        raise
    except Exception as exc:                           # noqa: BLE001 — controlled error, no fallback
        raise SecretError(f"secret at {path} is unreadable: {exc}") from exc


# --- JSON convenience: the recorder credential is stored atomically together ---

def write_json_secret(path: Path, obj: dict) -> None:
    write_secret(path, json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def read_json_secret(path: Path) -> dict:
    return json.loads(read_secret(path).decode("utf-8"))
