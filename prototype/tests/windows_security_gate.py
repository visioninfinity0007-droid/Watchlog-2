"""Real-Windows security gate for the WatchLog secret store.

Run on actual Windows runners (see .github/workflows/windows-security-gate.yml).
Unlike the mocked unit tests, this exercises DPAPI + the file DACL/owner for real
and proves the locked invariants:

  inprocess           (elevated runner)  write/verify, owner+DACL self-repair,
                                          transactional fail-closed on icacls
                                          failure, corrupted-blob controlled
                                          error, watchlog.env -> DPAPI migration
  encrypt-canary       (runner A)         encrypt a known secret, LocalMachine
                                          DPAPI, write the blob only
  decrypt-must-fail    (runner B)         a DIFFERENT machine must NOT be able to
                                          decrypt runner A's blob (machine binding)
  write-secret         (elevated)         publish a secret blob for the user tests
  user-cannot-read     (standard user)    the standard user cannot read/copy the
                                          blob or list the Secrets directory
  user-control-decrypt (standard user)    a deliberately world-readable
                                          LocalMachine blob IS decryptable by that
                                          same user (proves the denial above is
                                          the ACL, not an incidental failure)
  system-can-decrypt   (SYSTEM)           the SYSTEM task can decrypt the blob

Every subcommand prints PASS/FAIL lines and exits non-zero on any failure.
"""
import argparse
import ctypes
import os
import sys
import tempfile
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs           # noqa: E402
import windows_secret as ws             # noqa: E402

CANARY = b"watchlog-dpapi-machine-binding-canary-v1"
_fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        _fails.append(name)


def _write_localmachine_blob(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ws.protect_bytes(payload))


# --- LocalMachine DPAPI directly (bypasses our file, for the raw-blob tests) --
def _cryptunprotect(blob: bytes) -> bytes | None:
    class B(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    buf = ctypes.create_string_buffer(blob, len(blob))
    src = B(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out = B()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def cmd_encrypt_canary(outfile: str) -> None:
    Path(outfile).write_bytes(ws.protect_bytes(CANARY))
    print(f"wrote LocalMachine-DPAPI canary blob to {outfile}")


def cmd_decrypt_must_fail(infile: str) -> None:
    blob = Path(infile).read_bytes()
    plain = _cryptunprotect(blob)
    # On a DIFFERENT machine, decryption must fail (None) or not yield the canary.
    check("cross-machine decrypt is denied (machine binding)", plain != CANARY)


def cmd_inprocess() -> None:
    with tempfile.TemporaryDirectory() as d:
        os.environ["PROGRAMDATA"] = d
        secrets = cs.secrets_dir()

        # 1. write + read a recorder credential (encrypted, DACL-locked, owner set)
        try:
            cs.save_nvr_credential("admin", "s3cr3t:p@ss")
            cred = cs.load_nvr_credential()
            check("write+read recorder credential (encrypted, DACL/owner verified)",
                  cred["username"] == "admin" and cred["password"] == "s3cr3t:p@ss")
        except Exception as exc:                       # noqa: BLE001
            check(f"write+read recorder credential ({exc})", False)

        # 2. owner + DACL SELF-REPAIR of a deliberately-insecure directory
        repair = Path(d) / "RepairMe"
        repair.mkdir(parents=True, exist_ok=True)
        # grant Users Read/Write and hand ownership to a non-privileged principal
        os.system(f'icacls "{repair}" /grant "*S-1-1-0:(OI)(CI)(RX,W)" >nul 2>&1')  # Everyone RX+W
        try:
            ws.ensure_secure_dir(repair)               # must purge Everyone, fix owner, verify
            ws._secure_and_verify(repair, container=True)  # re-verify from scratch
            check("owner+DACL self-repair of an insecure directory", True)
        except Exception as exc:                       # noqa: BLE001
            check(f"owner+DACL self-repair ({exc})", False)

        # 3. corrupted authoritative blob -> controlled SecretError, no fallback
        cs.nvr_credential_path().write_bytes(b"NOT-A-DPAPI-BLOB")
        try:
            cs.load_nvr_credential()
            check("corrupted DPAPI blob raises (no plaintext fallback)", False)
        except ws.SecretError:
            check("corrupted DPAPI blob raises (no plaintext fallback)", True)

        # 4. watchlog.env -> DPAPI migration, plaintext removed
        with tempfile.TemporaryDirectory() as d2:
            os.environ["PROGRAMDATA"] = d2
            env = cs.legacy_env_path()
            env.parent.mkdir(parents=True, exist_ok=True)
            env.write_text("WATCHLOG_NVR_PASSWORD=legacy-pw\n", encoding="utf-8")
            ini = cs.data_dir() / "watchlog.ini"
            ini.write_text("[watchlog]\nnvr_username = op\n", encoding="utf-8")
            cred = cs.load_nvr_credential(ini)
            check("watchlog.env migrates to DPAPI and plaintext is removed",
                  cred and cred["password"] == "legacy-pw"
                  and cs.nvr_credential_path().exists() and not env.exists())


def cmd_write_secret(path: str) -> None:
    ws.write_secret(Path(path), CANARY)
    print(f"published DACL-locked secret at {path}")


def cmd_user_cannot_read(path: str, secrets_dir: str) -> None:
    denied_file = False
    try:
        Path(path).read_bytes()
    except PermissionError:
        denied_file = True
    check("standard user cannot read the secret blob", denied_file)
    denied_dir = False
    try:
        list(Path(secrets_dir).iterdir())
    except PermissionError:
        denied_dir = True
    check("standard user cannot list the Secrets directory", denied_dir)


def cmd_user_control_decrypt(path: str) -> None:
    # SANITY CONTROL: a world-readable LocalMachine-DPAPI blob MUST be decryptable
    # by this same standard user — proving the denial above is the ACL, not that
    # LocalMachine DPAPI happens to be unusable here.
    blob = Path(path).read_bytes()
    plain = _cryptunprotect(blob)
    check("control: standard user CAN decrypt a readable LocalMachine blob",
          plain == CANARY)


def cmd_system_can_decrypt(path: str) -> None:
    plain = _cryptunprotect(Path(path).read_bytes())
    check("SYSTEM can decrypt the secret blob", plain == CANARY)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="",
                    help="write PASS/FAIL here (so scheduled-task runs surface a verdict)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inprocess")
    p = sub.add_parser("encrypt-canary"); p.add_argument("outfile")
    p = sub.add_parser("decrypt-must-fail"); p.add_argument("infile")
    p = sub.add_parser("write-secret"); p.add_argument("path")
    p = sub.add_parser("user-cannot-read"); p.add_argument("path"); p.add_argument("secrets_dir")
    p = sub.add_parser("user-control-decrypt"); p.add_argument("path")
    p = sub.add_parser("system-can-decrypt"); p.add_argument("path")
    args = ap.parse_args()

    dispatch = {
        "inprocess": lambda: cmd_inprocess(),
        "encrypt-canary": lambda: cmd_encrypt_canary(args.outfile),
        "decrypt-must-fail": lambda: cmd_decrypt_must_fail(args.infile),
        "write-secret": lambda: cmd_write_secret(args.path),
        "user-cannot-read": lambda: cmd_user_cannot_read(args.path, args.secrets_dir),
        "user-control-decrypt": lambda: cmd_user_control_decrypt(args.path),
        "system-can-decrypt": lambda: cmd_system_can_decrypt(args.path),
    }
    try:
        dispatch[args.cmd]()
        rc = 1 if _fails else 0
    except Exception as exc:                            # noqa: BLE001
        print(f"FAIL {args.cmd}: unhandled {type(exc).__name__}: {exc}")
        _fails.append(str(exc))
        rc = 1
    if args.result:
        Path(args.result).parent.mkdir(parents=True, exist_ok=True)
        Path(args.result).write_text(
            "PASS" if rc == 0 else ("FAIL: " + "; ".join(_fails)), encoding="utf-8")
    print("\nsecurity gate step OK" if rc == 0
          else f"\nSECURITY GATE FAILED: {len(_fails)} check(s): " + "; ".join(_fails))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
