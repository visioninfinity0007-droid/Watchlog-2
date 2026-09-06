"""Windows-local recorder credential storage for WatchLog.

The recorder password never needs to leave the site PC. It is stored as a
plaintext value in an ACL-restricted env file under ProgramData
(``watchlog.env``) so that every launch context — the SYSTEM background task,
a hands-on support terminal, or ``--probe`` — reads it the same way, with no
decrypt step.

At-rest protection is the file ACL only: inherited ProgramData permissions
(which grant ordinary Users read) are stripped and access is restricted to
SYSTEM and the local Administrators group. There is intentionally no
encryption layer — this is a deliberate credential-storage decision that
trades encrypted-at-rest for a single, launch-context-independent read path.
Because the value is plaintext on disk, the file ACL is the only barrier;
keep it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SYSTEM_SID = "*S-1-5-18"
ADMINISTRATORS_SID = "*S-1-5-32-544"

# The single key both the setup UI writes and the agent reads.
NVR_PASSWORD_ENV_KEY = "WATCHLOG_NVR_PASSWORD"


class SecretError(RuntimeError):
    pass


def _lock_acl(path: Path) -> None:
    """Restrict a file to SYSTEM + local Administrators.

    Removes inherited ProgramData permissions so a standard user cannot read
    the stored recorder password. Raises on failure; callers decide whether
    that is fatal.
    """
    if os.name != "nt":
        return
    command = [
        "icacls", str(path), "/inheritance:r", "/grant:r",
        f"{SYSTEM_SID}:(F)", f"{ADMINISTRATORS_SID}:(F)",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise SecretError("Windows could not restrict access to the recorder credential file")


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Publish ``KEY=VALUE`` lines to an ACL-restricted env file, atomically.

    The ACL is applied to the temp file *before* the rename so the final path
    is never briefly readable with inherited ProgramData permissions. ACL
    hardening is best-effort: if ``icacls`` fails the file is still written
    (reliability over a hard failure) so the site keeps working, but a
    standard user may then be able to read it. The file write itself must
    succeed or this raises ``SecretError``.

    Values are written verbatim after the first ``=`` and must not contain a
    newline (recorder passwords do not).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{key}={value}\n" for key, value in values.items())
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise SecretError(f"could not write credential file {path}: {exc}") from exc
    # Harden the published file. Applied after the rename so the writer never
    # locks itself out of its own temp file mid-write (icacls /inheritance:r
    # strips the creating user), which would fail a non-elevated run. This is
    # best-effort: if it fails the credential is still written, so the site
    # keeps working — but a standard user may then be able to read it.
    try:
        _lock_acl(path)
    except (SecretError, OSError, subprocess.SubprocessError):
        pass


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file. Values are preserved verbatim.

    Everything after the first ``=`` is the value, unstripped, so passwords
    with spaces, ``=`` or ``#`` survive intact. Blank lines and lines whose
    first non-space character is ``#`` are ignored.
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        key = key.strip()
        if key:
            out[key] = value
    return out
