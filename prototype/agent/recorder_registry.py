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
import json
import os
import uuid
from pathlib import Path

import credential_store
from windows_secret import SecretError

REGISTRY_SCHEMA = "watchlog.recorders.v1"


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

    configured_primaries = [
        row for row in normalized if row["is_primary"] and row["is_configured"]
    ]
    if len(configured_primaries) > 1:
        raise ValueError("at most one configured recorder may be primary")
    if normalized and not configured_primaries:
        raise ValueError("a non-empty recorder registry needs one configured primary")

    continuity = [row for row in normalized if row["continuity_owner"]]
    if normalized and len(continuity) != 1:
        raise ValueError(
            "a non-empty recorder registry needs exactly one continuity owner"
        )
    if continuity and not continuity[0]["is_configured"]:
        raise ValueError(
            "the continuity owner must remain configured in WatchLog 5.1"
        )

    return {"schema": REGISTRY_SCHEMA, "recorders": normalized}


def load_registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {"schema": REGISTRY_SCHEMA, "recorders": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read recorder registry: {exc}") from exc
    return validate_registry(raw)


def save_registry(payload: dict) -> dict:
    normalized = validate_registry(payload)
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, separators=(",", ":")), encoding="utf-8")
    # Prove the exact staged bytes parse and satisfy the schema before publish.
    validate_registry(json.loads(tmp.read_text(encoding="utf-8")))
    tmp.replace(path)
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
                             identity_fingerprint=None) -> dict:
    """Update non-secret observed recorder facts for one local recorder."""
    current = load_registry()
    found = False
    rows = []
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


def migrate_legacy_singleton(config_path: Path) -> dict | None:
    """Stage the current singleton recorder into the multi-recorder store.

    This is intentionally COPY-ONLY during the compatibility phase. The legacy
    singleton credential and watchlog.ini recorder keys are retained so 5.0.27
    continues to run unchanged. A later runtime-cutover migration may retire the
    legacy form only after the new runtime proves it can boot from this registry.

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

    local_id = str(uuid.uuid4())
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


def add_recorder(*, display_name: str, url: str, driver: str,
                 username: str, password: str, is_primary: bool = False,
                 vendor: str | None = None, model: str | None = None,
                 firmware: str | None = None,
                 identity_fingerprint: str | None = None) -> dict:
    """Add a recorder locally without changing/removing existing history.

    Cloud recorder_id remains unset until the recorder-aware RPC sync assigns it.
    """
    current = load_registry()
    if any(r["url"] and r["url"] == str(url).strip() for r in current["recorders"]):
        raise ValueError("a recorder with this local address already exists")

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


def registry_cloud_descriptors() -> list[dict]:
    """Return the full non-secret registry state, including disabled recorders.

    This is the only descriptor surface suitable for lifecycle synchronization.
    Runtime RecorderContexts intentionally omit disabled recorders, so using
    context.cloud_descriptor() alone would leave a disabled cloud recorder
    incorrectly configured forever.
    """
    out = []
    for row in recorders():
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
