"""Site-bound runtime data never crosses to another site (tenant isolation).

%ProgramData%\\WatchLog holds queued events and analytics, recorder health and liveness, and
recorder auth back-off. They belong to the site that produced them: queued rows carry channels,
not cameras, and are ingested as the CURRENT identity's site. When this PC is set up for a
different site, the old site's queue would otherwise upload into the new site's cameras, and
its health/liveness would seed the new site's.

``site_runtime.json`` stamps that data with its site. At Agent start, before any queue is
opened, data stamped for another site is moved aside (never deleted). Unstamped data (written
by an Agent before 5.1.1) is adopted by the current site, so a same-site upgrade keeps its
backlog; Setup stamps it with the PRIOR site before enrolling a different one, so a site switch
of an older install is caught too.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STAMP_NAME = "site_runtime.json"
STAMP_SCHEMA = "watchlog.site_runtime.v1"

# Site-bound files in the data folder (SQLite companions included).
RUNTIME_FILES = (
    "spool.sqlite", "health.sqlite", "analytics_spool.sqlite",
    "last_live.json", "recorder_auth_backoff.json",
    "analytics_config.json", "analytics_bootstrap_sent.json", "analytics_status.json",
)
SQLITE_COMPANIONS = ("", "-wal", "-shm", "-journal")


def _read_stamp(state_dir: Path) -> dict | None:
    try:
        doc = json.loads((Path(state_dir) / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == STAMP_SCHEMA else None


def write_stamp(state_dir: Path, site_id: str, tenant_id: str | None) -> None:
    path = Path(state_dir) / STAMP_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": STAMP_SCHEMA, "site_id": str(site_id),
                               "tenant_id": str(tenant_id or ""),
                               "stamped_at": int(time.time())},
                              separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def present_files(state_dir: Path) -> list[Path]:
    out = []
    for name in RUNTIME_FILES:
        for suffix in SQLITE_COMPANIONS:
            path = Path(state_dir) / f"{name}{suffix}"
            if path.exists():
                out.append(path)
    return out


UNKNOWN_SITE = "unknown-origin"


def stamp_prior_site(state_dir: Path, prior_identity: dict | None) -> bool:
    """Setup, before enrolling: label unstamped runtime data with the site that wrote it.

    The prior identity's site when it loaded; else the plain site_id still readable in
    agent_state.json (an unreadable key does not hide which site the queue is from); else,
    with no identity at all (after an uninstall), "unknown-origin", so the new Agent sets the
    data aside rather than adopting rows it cannot attribute."""
    if _read_stamp(state_dir) is not None or not present_files(state_dir):
        return False
    site = str((prior_identity or {}).get("site_id") or "")
    tenant = (prior_identity or {}).get("tenant_id")
    if not site:
        try:
            raw = json.loads((Path(state_dir) / "agent_state.json").read_text(encoding="utf-8"))
            site = str(raw.get("site_id") or "") if isinstance(raw, dict) else ""
            tenant = raw.get("tenant_id") if isinstance(raw, dict) else None
        except (OSError, ValueError):
            site = ""
    write_stamp(state_dir, site or UNKNOWN_SITE, tenant)
    return True


def ensure_runtime_belongs(state_dir: Path, site_id: str, tenant_id: str | None,
                           log=lambda _m: None) -> list[Path]:
    """Agent start: quarantine another site's runtime data, then stamp it as this site's.

    Returns the moved paths. Raises OSError if another site's data cannot be moved: the Agent
    must not start on it (the launcher retries)."""
    state_dir = Path(state_dir)
    stamp = _read_stamp(state_dir)
    moved: list[Path] = []
    if stamp is not None and str(stamp.get("site_id") or "") != str(site_id):
        tag = time.strftime("%Y%m%d%H%M%S")
        for src in present_files(state_dir):
            dst, n = src.with_name(f"{src.name}.site-{str(stamp.get('site_id'))[:8]}-{tag}"), 0
            while dst.exists():
                n += 1
                dst = src.with_name(f"{src.name}.site-{str(stamp.get('site_id'))[:8]}-{tag}-{n}")
            os.replace(src, dst)
            moved.append(dst)
        log(f"set aside {len(moved)} file(s) of another site's queued data "
            f"(site {str(stamp.get('site_id'))[:8]}); they are kept, never uploaded here")
    if stamp is None or moved or str(stamp.get("site_id") or "") != str(site_id):
        write_stamp(state_dir, site_id, tenant_id)
    return moved
