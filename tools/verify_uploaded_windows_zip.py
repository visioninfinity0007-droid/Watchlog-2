#!/usr/bin/env python3
"""Fail closed when a downloaded GitHub Windows release ZIP is incomplete or altered."""
import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

EXPECTED = {"WatchLog-Setup.exe", "WatchLog-Setup.exe.sha256",
            "WatchLog-Repair-Upgrade.exe", "WatchLog-Repair-Upgrade.exe.sha256",
            "WatchLog-Release.hashes.txt", "WatchLog-Build-Manifest.json",
            "WatchLog-Payload-Proof.json", "WatchLog-Size-Report.json",
            "WatchLog-Update-Contract.json", "WatchLog-Perf-Report.json",
            "watchlog-update-payload.json"}

def digest(data):
    return hashlib.sha256(data).hexdigest().lower()

def check(path, expected_zip_digest=None):
    data = path.read_bytes()
    actual = digest(data)
    if expected_zip_digest and actual != expected_zip_digest.removeprefix("sha256:").lower():
        raise ValueError(f"Outer ZIP SHA-256 differs from GitHub artifact digest: {actual}")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or set(names) != EXPECTED:
            raise ValueError(f"Unexpected or missing files: {sorted(set(names)^EXPECTED)}")
        if any(i.is_dir() or i.flag_bits & 1 for i in z.infolist()):
            raise ValueError("Encrypted or directory entry not expected")
        if z.testzip() is not None:
            raise ValueError("CRC or decompression failure")
        files = {name: z.read(name) for name in EXPECTED}
    manifest = files["WatchLog-Release.hashes.txt"].decode("ascii")
    for exe in ("WatchLog-Setup.exe", "WatchLog-Repair-Upgrade.exe"):
        blob = files[exe]
        if not blob.startswith(b"MZ") or len(blob) < 10_000_000:
            raise ValueError(f"{exe}: invalid PE signature or suspicious size")
        declared = files[exe + ".sha256"].decode("ascii").strip().split()[0].lower()
        recorded = next((v.split('=', 1)[1] for v in manifest.splitlines() if v.startswith(exe + '.sha256=')), None)
        actual_exe = digest(blob)
        if declared != actual_exe or not recorded or recorded.lower() != actual_exe:
            raise ValueError(f"{exe}: SHA-256 mismatch")
    for name in ("WatchLog-Build-Manifest.json", "WatchLog-Payload-Proof.json", "WatchLog-Size-Report.json", "WatchLog-Update-Contract.json", "WatchLog-Perf-Report.json", "watchlog-update-payload.json"):
        json.loads(files[name])
    print(json.dumps({"result": "PASS", "zip": str(path), "zip_bytes": len(data),
                      "zip_sha256": actual, "files": len(files),
                      "installer_sha256": {k: digest(files[k]) for k in ("WatchLog-Setup.exe", "WatchLog-Repair-Upgrade.exe")}}, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("zip", type=Path)
    parser.add_argument("--expected-zip-digest")
    args = parser.parse_args()
    try:
        check(args.zip, args.expected_zip_digest)
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
