"""Honest vendor / recorder capability matrix — DERIVED from the drivers, never
hand-asserted. This is the machine-checkable form of "never fabricate capability".

For each registered driver and each capability we report exactly one status:

  proven       implemented AND validated against real hardware (documented evidence)
  unverified   implemented in code but NOT yet validated against that vendor's hardware
  unsupported  not implemented (the base no-op) — the honest "we do not do this"
  unknown      reserved for a RUNTIME probe that could not read a live device

The crucial rule: `proven` is NEVER inferred from code. It requires an explicit, dated
entry in FIELD_PROVEN — adding a cell there is a deliberate, auditable claim. Everything a
driver merely implements is `unverified`, never "supported/healthy". So a driver that has
never met its vendor's hardware can only ever read `unverified` / `unsupported` here.

The transport-hardening targets (HTTPS 443/8443, custom-port and SDK-vs-HTTP classification,
self-signed TLS, broader ONVIF/WS-Discovery, multi-NIC/subnet) are declared honestly as
`unsupported` with `requires future agent release`, so the gap is visible, not papered over.
"""
from __future__ import annotations

from drivers.base import NvrDriver
from drivers import DRIVERS

# capability key -> (label, the driver method that implements it, note)
CAPABILITIES = [
    ("inventory",              "list_channels",    "enumerate the recorder's channels"),
    ("snapshot",               "get_snapshot",     "a still JPEG on demand"),
    ("native_events",          "stream_events",    "recorder-pushed events (motion / native AI)"),
    ("smart_analytics",        "capabilities",     "read which recorder-side analytics exist / are on"),
    ("recording_verification", "recording_status", "per-channel recording state from the RECORD config"),
    ("storage_health",         "storage_status",   "recorder HDD / storage state"),
    ("footage_retrieval",      "get_clip",         "bounded on-demand recorded clip"),
]

# Declared capabilities that have no driver method yet — honestly unsupported until built.
DECLARED_FUTURE = [
    ("native_event_backfill", "query historical recorder events for archive reprocessing"),
]

# The ONLY source of `proven`. Each entry is documented field evidence; adding one is an
# auditable claim, never inferred from code. Keep it to capabilities actually exercised on
# real hardware (the Al-Khalid Dahua proved inventory/events/snapshot in production; its
# recording/storage/analytics were NOT exercised there, so they stay `unverified`).
FIELD_PROVEN = {
    ("dahua-cgi", "inventory"):     "Dahua DH-XVR1B08-I (Al-Khalid SM-HP): 8 channels enumerated in production",
    ("dahua-cgi", "native_events"): "Dahua DH-XVR1B08-I (Al-Khalid SM-HP): motion/AI events flowing in production",
    ("dahua-cgi", "snapshot"):      "Dahua DH-XVR1B08-I (Al-Khalid SM-HP): stills captured in production",
}

# Transport / discovery hardening — honest current state. These are NOT per-driver methods.
# Implemented in recorder_probe.py, but implementation != field-proven: each is "unverified"
# (works in logic tests; not yet validated against real recorder hardware).
TRANSPORT = {
    "custom_ports":         {"status": "unverified",
                             "note": "recorder_probe builds http/https base URLs for any configured port; not hardware-validated"},
    "https":               {"status": "unverified",
                             "note": "recorder_probe tries https on 443 and 8443 as candidates; not hardware-validated"},
    "https_self_signed":    {"status": "unverified",
                             "note": "recorder_probe.tls_context/requests_verify trust self-signed ONLY when explicitly configured; not hardware-validated"},
    "port_classification":  {"status": "unverified",
                             "note": "recorder_probe.classify_port distinguishes Hikvision SDK(8000)/Dahua(37777)/Xiongmai(34567) from HTTP; not hardware-validated"},
    "onvif_ws_discovery":   {"status": "unverified",
                             "note": "WS-Discovery probe (wsdiscovery.py) + recorder_probe onvif candidates; not hardware-validated"},
    "multi_nic_subnet":     {"status": "unverified",
                             "note": "recorder_probe.local_subnets enumerates private /24s across NICs for discovery; not hardware-validated"},
}

_STATUS_ORDER = {"proven": 0, "unverified": 1, "unsupported": 2, "unknown": 3}


def _implemented(cls: type, method: str) -> bool:
    """True if `cls` (or any base above NvrDriver) overrides the base no-op for `method`."""
    return getattr(cls, method, None) is not getattr(NvrDriver, method, None)


def matrix() -> dict:
    """The full honest matrix, derived from the live driver registry."""
    devices: dict[str, dict] = {}
    for name, cls in DRIVERS.items():
        simulator = getattr(cls, "name", "") == "mock"
        caps: dict[str, dict] = {}
        for key, method, _note in CAPABILITIES:
            if not _implemented(cls, method):
                caps[key] = {"status": "unsupported", "evidence": None}
            elif (name, key) in FIELD_PROVEN and not simulator:
                caps[key] = {"status": "proven", "evidence": FIELD_PROVEN[(name, key)]}
            else:
                caps[key] = {"status": "unverified", "evidence": None}
        for key, _note in DECLARED_FUTURE:
            caps[key] = {"status": "unsupported", "evidence": "requires future agent release"}
        devices[name] = {
            "simulator": simulator,
            "verified_against_hardware_flag": bool(getattr(cls, "verified_against_hardware", False)),
            "capabilities": caps,
        }
    return {"devices": devices, "transport": TRANSPORT}


def render_markdown() -> str:
    """A human-readable matrix for docs / the portal capability page."""
    m = matrix()
    cap_keys = [k for k, _, _ in CAPABILITIES] + [k for k, _ in DECLARED_FUTURE]
    lines = ["| vendor | " + " | ".join(cap_keys) + " |",
             "|" + "---|" * (len(cap_keys) + 1)]
    for name, d in m["devices"].items():
        tag = f"{name}" + (" (simulator)" if d["simulator"] else "")
        cells = [d["capabilities"][k]["status"] for k in cap_keys]
        lines.append("| " + tag + " | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Transport / discovery:")
    for k, v in m["transport"].items():
        lines.append(f"- {k}: {v['status']} — {v['note']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    print(render_markdown())
    print()
    print(json.dumps(matrix(), indent=2))
