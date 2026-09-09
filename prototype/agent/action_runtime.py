"""Evidence action runtime (workstream 2).

Executes the ACTIONS a rule configured for an operations incident. It reuses existing
transports and never builds a parallel evidence system:

  * capture_still     -> a single bounded JPEG from the recorder (driver snapshot), uploaded
                         as incident evidence via the injected uploader;
  * request_footage   -> a single BOUNDED, on-demand incident-footage request through the
                         existing 0040/0041 transport (the injected requester). NEVER
                         continuous cloud video;
  * mark_review       -> human-review marker (sensitive incidents already open as
                         candidate/review; this records the explicit action);
  * include_in_report -> flag the incident for the executive report;
  * escalate_severity / notify -> recorded intents (notification delivery is a later concern).

Truthful UNSUPPORTED: if the recorder cannot produce a still, or no bounded window/source
is available for footage, the action records an 'unsupported' result — it NEVER fabricates
evidence, and it never turns a missing still into a claim that one exists.

Handlers are injected (snapshot / upload_still / request_footage), so this module is fully
unit-testable and does not hardcode any particular RPC or driver.
"""

from __future__ import annotations

import base64

KNOWN_ACTIONS = ("capture_still", "request_footage", "mark_review", "include_in_report",
                 "escalate_severity", "notify")
STILL_MAX_BYTES = 3_000_000


class ActionRuntime:
    def __init__(self, *, snapshot=None, upload_still=None, request_footage=None,
                 log=lambda _m: None, still_max_bytes=STILL_MAX_BYTES):
        # snapshot(channel) -> bytes|None
        # upload_still(camera_id, jpeg_bytes) -> evidence ref (any) / raises on failure
        # request_footage(incident) -> dict (e.g. {status, request_id}) reusing 0040/0041
        self.snapshot = snapshot
        self.upload_still = upload_still
        self.request_footage = request_footage
        self.log = log
        self.still_max_bytes = int(still_max_bytes)

    def run(self, actions, *, channel=None, camera_id=None, incident=None) -> list:
        results = []
        for raw in actions or []:
            atype = raw.get("type") if isinstance(raw, dict) else raw
            if atype not in KNOWN_ACTIONS:
                results.append({"type": atype, "result": "skipped_unknown"})
                continue
            try:
                out = self._dispatch(atype, raw if isinstance(raw, dict) else {},
                                     channel=channel, camera_id=camera_id, incident=incident)
            except Exception as e:                                   # noqa: BLE001
                out = {"result": "error", "detail": str(e).splitlines()[0][:120]}
            results.append({"type": atype, **out})
        return results

    def _dispatch(self, atype, spec, *, channel, camera_id, incident):
        if atype == "capture_still":
            if not self.snapshot or not self.upload_still:
                return {"result": "unsupported", "detail": "still capture not available on this agent"}
            raw = self.snapshot(channel)
            if not raw:
                return {"result": "unsupported", "detail": "recorder produced no still"}
            if len(raw) > self.still_max_bytes:
                return {"result": "skipped", "detail": f"still too large ({len(raw)} bytes)"}
            ref = self.upload_still(camera_id, base64.b64encode(raw).decode("ascii"))
            return {"result": "captured", "bytes": len(raw), "ref": ref}

        if atype == "request_footage":
            if not self.request_footage or not incident:
                return {"result": "unsupported", "detail": "no bounded footage source available"}
            res = self.request_footage(incident) or {}
            # bounded + on-demand only; we surface the transport's state truthfully
            return {"result": "requested", "status": res.get("status"),
                    "request_id": res.get("request_id")}

        if atype == "mark_review":
            return {"result": "marked"}
        if atype == "include_in_report":
            return {"result": "flagged"}
        if atype == "escalate_severity":
            return {"result": "escalated", "to": spec.get("severity")}
        if atype == "notify":
            return {"result": "queued"}
        return {"result": "noop"}
