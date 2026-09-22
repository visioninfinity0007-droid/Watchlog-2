"""Runtime capability truth for the connectivity-first Site Connector.

This is deliberately separate from manufacturer/model capability truth. It answers only:
"What can THIS installed connector actually execute right now?"

Clip evidence is dynamic: code existence is not enough. The capability is advertised only
after a successful local clip export has been proven and recorded on this machine.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import requests

BASE_CAPABILITIES = (
    "site_control_runtime",
    "operations_evidence_still",
    "recorder_probe_v2",
)
REPORT_SECONDS = 60
PROOF_VERSION = 1


def proof_path(cfg) -> Path:
    return cfg.state_path.parent / "connector_capability_proofs.json"


def load_proofs(cfg) -> dict:
    try:
        data = json.loads(proof_path(cfg).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _recorder_identity(cfg) -> dict:
    try:
        import connector_rediscovery
        row = connector_rediscovery.load_identity(cfg) or {}
        return {
            "vendor": str(row.get("vendor") or ""),
            "model": str(row.get("model") or ""),
            "serial": str(row.get("serial") or ""),
        }
    except Exception:  # noqa: BLE001
        return {"vendor": "", "model": "", "serial": ""}


def mark_proof(cfg, capability: str, detail: dict | None = None) -> None:
    """Persist non-secret local evidence that a runtime capability actually worked."""
    data = load_proofs(cfg)
    data["version"] = PROOF_VERSION
    proofs = data.setdefault("proofs", {})
    proofs[str(capability)] = {
        "ok": True,
        "proved_at": int(time.time()),
        "recorder": _recorder_identity(cfg),
        "detail": detail or {},
    }
    path = proof_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def has_proof(cfg, capability: str) -> bool:
    row = (load_proofs(cfg).get("proofs") or {}).get(capability) or {}
    if not row.get("ok"):
        return False
    proved = row.get("recorder") or {}
    current = _recorder_identity(cfg)

    # A proof is valid only for the same recorder. Serial is authoritative when
    # available; otherwise require exact vendor+model. If identity is unavailable,
    # fail closed and re-advertise after the collector proves the recorder again.
    ps = str(proved.get("serial") or "").strip().lower()
    cs = str(current.get("serial") or "").strip().lower()
    if ps:
        return bool(cs and ps == cs)

    pv = str(proved.get("vendor") or "").strip().lower()
    pm = str(proved.get("model") or "").strip().lower()
    cv = str(current.get("vendor") or "").strip().lower()
    cm = str(current.get("model") or "").strip().lower()
    return bool(pv and pm and pv == cv and pm == cm)


def runtime_capabilities(cfg) -> list[str]:
    caps = list(BASE_CAPABILITIES)
    if has_proof(cfg, "operations_evidence_clip"):
        caps.append("operations_evidence_clip")
    return caps


def report_once(cloud, state: dict, cfg) -> list[str]:
    caps = runtime_capabilities(cfg)
    cloud.call(
        "wl_agent_report_capabilities",
        p_agent_id=state["agent_id"],
        p_agent_key=state["agent_key"],
        p_capabilities=caps,
    )
    return caps


def capability_worker(cfg, state: dict, cloud, stop: threading.Event) -> None:
    """Best-effort runtime advertisement. A server/RPC outage never affects monitoring."""
    next_report = 0.0
    last_caps = None
    while not stop.is_set():
        now = time.monotonic()
        caps = tuple(runtime_capabilities(cfg))
        if now >= next_report or caps != last_caps:
            try:
                report_once(cloud, state, cfg)
                if caps != last_caps:
                    import watchlog_agent as core
                    core.log("connector capabilities: " + ", ".join(caps))
                last_caps = caps
                next_report = now + REPORT_SECONDS
            except (RuntimeError, requests.RequestException):
                next_report = now + REPORT_SECONDS
            except Exception:  # noqa: BLE001
                next_report = now + REPORT_SECONDS
        stop.wait(2)


def wrap_cmd_run(original):
    """Run capability advertisement beside the normal connector runtime."""
    def wrapped(cfg, state, cloud, once, device=None, channels=None):
        if once:
            try:
                report_once(cloud, state, cfg)
            except Exception:  # noqa: BLE001
                pass
            return original(cfg, state, cloud, once, device, channels)

        stop = threading.Event()
        worker = threading.Thread(
            target=capability_worker,
            args=(cfg, state, cloud, stop),
            daemon=True,
            name="connector-capabilities",
        )
        worker.start()
        try:
            return original(cfg, state, cloud, once, device, channels)
        finally:
            stop.set()
            worker.join(timeout=5)
    return wrapped
