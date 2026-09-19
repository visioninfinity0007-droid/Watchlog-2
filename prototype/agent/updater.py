#!/usr/bin/env python3
"""In-app update decision + integrity engine (0.4.4 §13/§14).

This is the SAFE, testable core of the in-app updater — the part that decides whether to update
and refuses to install anything it cannot trust. It performs NO network or process work itself:
the caller fetches the manifest over HTTPS from the pinned WatchLog origin, downloads the payload,
and (on Windows) hands the verified binary to the transactional ``wl-upgrade.ps1`` sequence
(preflight -> stage -> verify-version -> commit, rollback on any failure). Keeping the decision
pure makes the security-critical logic — signature policy, version/channel gating and SHA-256
integrity — fully unit-testable with no cloud, recorder or database.

Trust model:
  * the release MANIFEST is authenticated by a detached Ed25519 signature verified against a
    public key baked into the agent (private key held by the release operator, never shipped);
  * each release names its payload SHA-256 and size; a download whose hash/size does not match is
    REFUSED — a corrupted or tampered binary can never be installed;
  * an unverifiable manifest blocks the update (trust nothing), governed by ``require_signature``.

Release channels (§14): internal -> pilot -> beta -> production. A site updates only on its own
channel; the manifest carries an independent release per channel.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

CHANNELS = ("internal", "pilot", "beta", "production")
MANIFEST_SCHEMA = "watchlog.release_manifest.v1"


# --- version comparison -----------------------------------------------------

def parse_version(value: str) -> tuple[int, int, int]:
    """Lenient semver parse: 'v0.4.5', '0.4.5+abc1234', '0.4.5-rc1' -> (0, 4, 5). Build/pre-release
    metadata after '+' or '-' is ignored; non-numeric parts degrade to 0 (deterministic, never raises)."""
    text = (value or "").strip().lstrip("vV").split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for chunk in text.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])  # type: ignore[return-value]


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


# --- manifest ---------------------------------------------------------------

def parse_manifest(data) -> dict:
    """Parse + minimally validate a release manifest (JSON text/bytes or an already-decoded dict).
    Raises ValueError on a structurally invalid manifest — the caller treats that as untrusted."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    manifest = json.loads(data) if isinstance(data, str) else data
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not an object")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"unexpected manifest schema: {manifest.get('schema')!r}")
    if not isinstance(manifest.get("channels"), dict) or not manifest["channels"]:
        raise ValueError("manifest has no channels")
    return manifest


def select_release(manifest: dict, channel: str) -> dict | None:
    """Return the release entry for ``channel`` (or None if the channel is absent)."""
    return (manifest.get("channels") or {}).get(channel)


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Deterministic bytes for signing/verifying: the manifest WITHOUT its own 'signature' field,
    serialized with sorted keys and no insignificant whitespace."""
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


# --- signature (Ed25519; cryptography imported lazily) -----------------------

def verify_ed25519(message: bytes, signature_b64: str, public_key_b64: str):
    """Verify a detached Ed25519 signature. Returns True (valid), False (invalid), or None when the
    cryptography backend is unavailable (so the caller's policy — not a crash — decides)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except Exception:  # noqa: BLE001 — backend not packaged: 'cannot verify', not 'invalid'
        return None
    if not signature_b64 or not public_key_b64:
        return None  # no signature and/or no configured key => cannot verify (unsigned posture)
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    except Exception:  # noqa: BLE001 — a malformed key cannot verify anything
        return None
    try:
        pub.verify(base64.b64decode(signature_b64), message)
        return True
    except InvalidSignature:
        return False
    except Exception:  # noqa: BLE001 — malformed signature bytes, etc.
        return False


def verify_manifest_signature(manifest: dict, public_key_b64: str):
    """Verify the manifest's own detached signature. True/False/None (see verify_ed25519)."""
    return verify_ed25519(canonical_manifest_bytes(manifest),
                          manifest.get("signature") or "", public_key_b64)


# --- payload integrity ------------------------------------------------------

def sha256_file(path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_payload(path, expected_sha256: str, expected_size=None) -> tuple[bool, str]:
    """Gate a downloaded binary before it is ever installed: it must exist, match the expected size
    (when given) and match the expected SHA-256. Any mismatch is a hard refusal."""
    p = Path(path)
    if not p.exists():
        return False, "downloaded file is missing"
    if expected_size is not None and p.stat().st_size != int(expected_size):
        return False, f"size mismatch (got {p.stat().st_size}, expected {expected_size})"
    got = sha256_file(p)
    if got.lower() != str(expected_sha256 or "").lower():
        return False, "sha256 mismatch — refusing to install"
    return True, "sha256 + size verified"


# --- the decision -----------------------------------------------------------

def plan_update(manifest: dict, current_version: str, channel: str, *,
                signature_state=None, require_signature: bool = True) -> dict:
    """Decide what to do for ``channel`` given the running ``current_version``.

    ``signature_state`` is the result of verify_manifest_signature (True/False/None). Returns a
    dict with ``action`` in {'up-to-date', 'update', 'blocked'} and a machine-readable ``reason``
    when blocked. Nothing is trusted from an unverifiable manifest — signature is checked FIRST.
    """
    if channel not in CHANNELS:
        return {"action": "blocked", "reason": "unknown_channel", "channel": channel}

    if require_signature and signature_state is not True:
        reason = "bad_signature" if signature_state is False else "manifest_unsigned"
        return {"action": "blocked", "reason": reason, "channel": channel}

    rel = select_release(manifest, channel)
    if not rel:
        return {"action": "blocked", "reason": "no_release_for_channel", "channel": channel}

    target = rel.get("version")
    if not target or not rel.get("url") or not rel.get("sha256"):
        return {"action": "blocked", "reason": "manifest_invalid", "channel": channel}

    min_agent = rel.get("min_agent_version")
    if min_agent and parse_version(current_version) < parse_version(min_agent):
        return {"action": "blocked", "reason": "agent_too_old", "required": min_agent,
                "current": current_version, "channel": channel}

    if not is_newer(target, current_version):
        return {"action": "up-to-date", "current": current_version, "target": target,
                "channel": channel}

    return {"action": "update", "target": target, "url": rel["url"], "sha256": rel["sha256"],
            "size": rel.get("size"), "notes": rel.get("notes"), "channel": channel}


def apply_update(target_version: str, package_url: str, sha256: str, *, install_dir: str,
                 size=None, download, run_stage, stage_binary, register,
                 verify=verify_payload, log=None) -> dict:
    """Transactionally APPLY a verified update, rolling back on ANY post-preflight failure (§13).

    The dangerous, order-sensitive Windows work lives in wl-upgrade.ps1 (stop -> backup ->
    verify-version -> commit / rollback); this sequences it and refuses to proceed on a bad
    download. Every side-effecting step is INJECTED so the whole flow — including every rollback
    branch — is unit-testable with no Windows, no network and no real package:

      download(url) -> local package path
      verify(path, sha256, size) -> (ok, detail)                 [default: SHA-256 + size gate]
      run_stage(stage, expected_version=None) -> exit code       [wl-upgrade.ps1 stage]
      stage_binary(package_path, install_dir) -> bool            [replace the binary]
      register() -> bool                                         [register-service.ps1]

    Identity, config and secrets are never touched — only the binary is swapped. Returns
    {ok, stage, rolled_back, detail}.
    """
    log = log or (lambda *a: None)

    # 1. Download + integrity BEFORE the running agent is touched. A bad package never reaches disk.
    try:
        pkg = download(package_url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "download", "rolled_back": False,
                "detail": f"download failed: {type(exc).__name__}"}
    ok, detail = verify(pkg, sha256, size)
    if not ok:
        return {"ok": False, "stage": "verify", "rolled_back": False, "detail": detail}

    # 2. Preflight: stop the agent + back up the current binary. A failure here means NOTHING was
    #    changed and the old runtime is preserved — no rollback needed.
    if run_stage("preflight") != 0:
        return {"ok": False, "stage": "preflight", "rolled_back": False,
                "detail": "preflight refused (agent still running / binary locked); no changes made"}

    # From here on, any failure MUST roll back to the previous working version.
    def _rollback(stage, why):
        rb = run_stage("rollback")
        log(f"update: {why}; rolled back={'ok' if rb == 0 else 'FAILED'}")
        return {"ok": False, "stage": stage, "rolled_back": rb == 0, "detail": why}

    try:
        if not stage_binary(pkg, install_dir):
            return _rollback("stage", "could not stage the new binary")
    except Exception as exc:  # noqa: BLE001
        return _rollback("stage", f"stage error: {type(exc).__name__}")

    if run_stage("verify-version", target_version) != 0:
        return _rollback("verify-version", f"installed binary is not {target_version}")

    try:
        if not register():
            return _rollback("register", "could not (re)register the service")
    except Exception as exc:  # noqa: BLE001
        return _rollback("register", f"register error: {type(exc).__name__}")

    if run_stage("commit", target_version) != 0:
        return _rollback("commit", "post-start verification failed (version / single-instance / liveness)")

    log(f"update: committed {target_version}")
    return {"ok": True, "stage": "commit", "rolled_back": False, "detail": f"updated to {target_version}"}


__all__ = [
    "CHANNELS", "MANIFEST_SCHEMA", "parse_version", "is_newer", "parse_manifest",
    "select_release", "canonical_manifest_bytes", "verify_ed25519",
    "verify_manifest_signature", "sha256_file", "verify_payload", "plan_update", "apply_update",
]
