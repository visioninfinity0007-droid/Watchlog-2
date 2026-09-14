#!/usr/bin/env python3
"""Generate (and optionally Ed25519-sign) the WatchLog release manifest (0.4.4 §Phase 7).

The manifest is the SINGLE SOURCE OF TRUTH the self-updater consumes: website, portal and updater
must all point at the same version + build_sha + sha256. This tool takes the identity of an
already-built, immutable installer artifact and emits the manifest the Agent verifies with
updater.verify_manifest_signature / plan_update.

Signing: the Ed25519 PRIVATE key is provided out-of-band (env WATCHLOG_MANIFEST_PRIVKEY_B64 or
--privkey-b64) and NEVER committed. Without it, an UNSIGNED manifest is emitted and the Agent will
refuse it unless update_require_signature is false — which is correct: an unsigned manifest is not
production-trustable.

    python tools/make_release_manifest.py --channel pilot --version 0.4.4 \
        --build-sha <sha> --sha256 <hex> --size <bytes> \
        --url https://<watchlog-domain>/downloads/releases/0.4.4/WatchLog-Setup-0.4.4.exe \
        [--notes "..."] [--min-agent 0.4.0] [--mandatory] [--privkey-b64 <b64>]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prototype" / "agent"))
import updater  # noqa: E402


def build_manifest(*, channel, version, build_sha, sha256, size, url, notes="",
                   min_agent=None, mandatory=False, generated_at=None) -> dict:
    if channel not in updater.CHANNELS:
        raise ValueError(f"channel must be one of {updater.CHANNELS}")
    release = {"version": version, "build_sha": build_sha, "url": url,
               "sha256": (sha256 or "").lower(), "size": int(size),
               "notes": notes or "", "mandatory": bool(mandatory)}
    if min_agent:
        release["min_agent_version"] = min_agent
    return {
        "schema": updater.MANIFEST_SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "channels": {channel: release},
    }


def sign_manifest(manifest: dict, privkey_b64: str) -> dict:
    """Attach a detached Ed25519 signature over the canonical manifest bytes. Never logs the key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(privkey_b64))
    sig = priv.sign(updater.canonical_manifest_bytes(manifest))
    signed = dict(manifest)
    signed["signature"] = base64.b64encode(sig).decode("ascii")
    return signed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, choices=list(updater.CHANNELS))
    ap.add_argument("--version", required=True)
    ap.add_argument("--build-sha", required=True)
    ap.add_argument("--sha256", required=True)
    ap.add_argument("--size", required=True, type=int)
    ap.add_argument("--url", required=True)
    ap.add_argument("--notes", default="")
    ap.add_argument("--min-agent", default=None)
    ap.add_argument("--mandatory", action="store_true")
    ap.add_argument("--privkey-b64", default=None)
    ap.add_argument("--out", default="-")
    args = ap.parse_args(argv)

    if not args.url.lower().startswith("https://"):
        print("refusing: --url must be HTTPS (WatchLog-controlled infrastructure)", file=sys.stderr)
        return 2

    manifest = build_manifest(channel=args.channel, version=args.version, build_sha=args.build_sha,
                              sha256=args.sha256, size=args.size, url=args.url, notes=args.notes,
                              min_agent=args.min_agent, mandatory=args.mandatory)
    privkey = args.privkey_b64 or os.environ.get("WATCHLOG_MANIFEST_PRIVKEY_B64")
    if privkey:
        manifest = sign_manifest(manifest, privkey)
        note = "SIGNED"
    else:
        note = "UNSIGNED (no private key; Agent will refuse unless update_require_signature=false)"
    text = json.dumps(manifest, indent=2, sort_keys=True)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
    print(f"# manifest {note}: {args.channel} {args.version} ({args.build_sha[:7]})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
