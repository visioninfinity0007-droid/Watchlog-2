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

Agent-only vs full-component releases (5.1.1). Every in-app path (remote update, Site Status)
replaces watchlog-agent.exe ONLY; the Setup UI, the launcher/updater scripts, the uninstaller and
the registry format stay as the last full installer or Repair/Upgrade package left them. A release
entry therefore says whether it may be delivered that way:

  update_class             AGENT_ONLY_COMPATIBLE | REQUIRES_REPAIR_PACKAGE
  min_installed_components oldest installed component set (full installer / Repair package
                           version, the ARP "ComponentsVersion", else "DisplayVersion") the new
                           Agent can run beside.

Absent update_class: a patch release (same major.minor as the running Agent) is treated as
AGENT_ONLY_COMPATIBLE; any major/minor change as REQUIRES_REPAIR_PACKAGE. An unknown value is
REQUIRES_REPAIR_PACKAGE. Absent min_installed_components defaults to the target's own
"major.minor.0", so an Agent-only update never runs beside components of an older minor line.
plan_update refuses (action 'blocked', reason 'requires_repair_package') instead of updating.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

CHANNELS = ("internal", "pilot", "beta", "production")
MANIFEST_SCHEMA = "watchlog.release_manifest.v1"
AGENT_ONLY_COMPATIBLE = "AGENT_ONLY_COMPATIBLE"
REQUIRES_REPAIR_PACKAGE = "REQUIRES_REPAIR_PACKAGE"
UPDATE_CLASSES = (AGENT_ONLY_COMPATIBLE, REQUIRES_REPAIR_PACKAGE)
# plan_update(installed_components=...) default: the caller did not check the installed
# components (a pure decision test). None means "checked, but unknown": that blocks.
NOT_CHECKED = object()
ARP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\WatchLog"


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


# --- installed component set -------------------------------------------------

def installed_components_version(_read=None) -> str | None:
    """Version of the component set the last full installer or Repair/Upgrade package wrote
    (Setup UI, launcher/updater scripts, uninstaller), or None when it cannot be read.

    Both installers write HKLM\\...\\Uninstall\\WatchLog only after a proven install. 5.1.1+
    writes ComponentsVersion; older installers wrote only DisplayVersion, which no in-app update
    ever changes, so it is the component version there too. NSIS is 32-bit: the key lives in the
    32-bit registry view (WOW6432Node)."""
    if _read is None:
        if sys.platform != "win32":
            return None

        def _read(name):
            import winreg
            access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ARP_KEY, 0, access) as key:
                return winreg.QueryValueEx(key, name)[0]
    for name in ("ComponentsVersion", "DisplayVersion"):
        try:
            value = str(_read(name) or "").strip()
        except Exception:  # noqa: BLE001 - missing value/key or no registry: try the next
            value = ""
        if value:
            return value
    return None


def _names_itself_minimum(rel: dict) -> bool:
    """A release whose min_agent_version is its own version is the signed marker for
    REQUIRES_REPAIR_PACKAGE. The deployed manifest builder signs min_agent_version (and not yet
    update_class), and every fielded Agent (5.0.17+) refuses such a release as agent_too_old."""
    version, min_agent = rel.get("version"), rel.get("min_agent_version")
    return bool(version and min_agent) and parse_version(min_agent) == parse_version(version)


def release_update_class(rel: dict, current_version: str) -> str:
    """The release's declared update class, or the conservative default when absent."""
    declared = str(rel.get("update_class") or "").strip().upper()
    if declared in UPDATE_CLASSES:
        return declared
    if declared:
        return REQUIRES_REPAIR_PACKAGE            # unknown value: never guess "agent only"
    if _names_itself_minimum(rel):
        return REQUIRES_REPAIR_PACKAGE
    same_line = parse_version(rel.get("version") or "")[:2] == parse_version(current_version)[:2]
    return AGENT_ONLY_COMPATIBLE if same_line else REQUIRES_REPAIR_PACKAGE


def agent_only_refusal(rel: dict, current_version: str, installed_components=NOT_CHECKED):
    """None when this release may be installed by replacing the Agent alone, else the reason."""
    update_class = release_update_class(rel, current_version)
    if update_class == REQUIRES_REPAIR_PACKAGE:
        return {"reason": "requires_repair_package", "update_class": update_class,
                "detail": "this release must be installed with the WatchLog Repair/Upgrade package"}
    if installed_components is NOT_CHECKED:
        return None
    target = parse_version(rel.get("version") or "")
    minimum = str(rel.get("min_installed_components") or f"{target[0]}.{target[1]}.0")
    if not installed_components:
        return {"reason": "requires_repair_package", "update_class": update_class,
                "required_components": minimum, "installed_components": None,
                "detail": "the installed WatchLog components could not be identified; "
                          "install this release with the Repair/Upgrade package"}
    if parse_version(installed_components) < parse_version(minimum):
        return {"reason": "requires_repair_package", "update_class": update_class,
                "required_components": minimum, "installed_components": installed_components,
                "detail": f"installed WatchLog components {installed_components} are older than "
                          f"{minimum}; install this release with the Repair/Upgrade package"}
    return None


# --- the decision -----------------------------------------------------------

def plan_update(manifest: dict, current_version: str, channel: str, *,
                signature_state=None, require_signature: bool = True,
                installed_components=NOT_CHECKED) -> dict:
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
        if _names_itself_minimum(rel):
            # Not "too old": this release must be installed with the Repair/Upgrade package.
            return {"action": "blocked", "target": target, "current": current_version,
                    "channel": channel, "required": min_agent, **agent_only_refusal(rel, current_version)}
        return {"action": "blocked", "reason": "agent_too_old", "required": min_agent,
                "current": current_version, "channel": channel}

    if not is_newer(target, current_version):
        return {"action": "up-to-date", "current": current_version, "target": target,
                "channel": channel}

    # Every in-app path replaces the Agent only: refuse a release that needs the rest of the
    # component set (scripts, Setup UI, uninstaller, registry format) to change with it.
    refusal = agent_only_refusal(rel, current_version, installed_components)
    if refusal:
        return {"action": "blocked", "target": target, "current": current_version,
                "channel": channel, **refusal}

    return {"action": "update", "target": target, "url": rel["url"], "sha256": rel["sha256"],
            "size": rel.get("size"), "notes": rel.get("notes"), "channel": channel,
            "update_class": release_update_class(rel, current_version),
            "build_sha": rel.get("build_sha") or None}


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
        return _rollback("commit", "post-start verification failed (version / task running)")

    log(f"update: committed {target_version}")
    return {"ok": True, "stage": "commit", "rolled_back": False, "detail": f"updated to {target_version}"}


__all__ = [
    "CHANNELS", "MANIFEST_SCHEMA", "AGENT_ONLY_COMPATIBLE", "REQUIRES_REPAIR_PACKAGE",
    "installed_components_version", "release_update_class", "agent_only_refusal",
    "parse_version", "is_newer", "parse_manifest",
    "select_release", "canonical_manifest_bytes", "verify_ed25519",
    "verify_manifest_signature", "sha256_file", "verify_payload", "plan_update", "apply_update",
]
