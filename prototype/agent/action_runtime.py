"""Evidence + notification action runtime for operations incidents.

Evidence actions (capture_still / request_footage) are SERVER-AUTHORIZED, not captured at frame
time. When an incident fires, the server emitter (0049 + 0058) creates a BOUNDED evidence task
bound to that exact incident / camera / timestamp, and the agent's evidence workers
(incident_evidence.py: stills_worker / footage_worker) claim and fulfil it. The agent never
captures arbitrary frame-time evidence and never chooses the target — the server stays
authoritative over what evidence is permitted.

This runtime therefore only ACKNOWLEDGES the authorized evidence actions (so the local dispatch
log is truthful) and applies the recorded-intent actions (mark_review / include_in_report /
escalate_severity / notify). Unknown action types are skipped, never stored, so a bad rule config
can never poison the audit trail.
"""

from __future__ import annotations

KNOWN_ACTIONS = ("capture_still", "request_footage", "mark_review", "include_in_report",
                 "escalate_severity", "notify")


class ActionRuntime:
    def __init__(self, *, log=lambda _m: None):
        self.log = log

    def run(self, actions, *, channel=None, camera_id=None, incident=None) -> list:
        results = []
        for raw in actions or []:
            atype = raw.get("type") if isinstance(raw, dict) else raw
            if atype not in KNOWN_ACTIONS:
                results.append({"type": atype, "result": "skipped_unknown"})
                continue
            try:
                out = self._dispatch(atype, raw if isinstance(raw, dict) else {})
            except Exception as e:                                   # noqa: BLE001
                out = {"result": "error", "detail": str(e).splitlines()[0][:120]}
            results.append({"type": atype, **out})
        return results

    def _dispatch(self, atype, spec):
        # Evidence actions are authorized + created SERVER-side and fulfilled by the evidence
        # workers; the agent acknowledges rather than captures at frame time.
        if atype == "capture_still":
            return {"result": "authorized",
                    "detail": "bounded still authorized by the server; captured by the evidence worker"}
        if atype == "request_footage":
            return {"result": "authorized",
                    "detail": "bounded clip authorized by the server; retrieved by the evidence worker"}
        # recorded-intent actions (the incident row + operations_incident_actions carry the audit)
        if atype == "mark_review":
            return {"result": "marked"}
        if atype == "include_in_report":
            return {"result": "flagged"}
        if atype == "escalate_severity":
            return {"result": "escalated", "to": spec.get("severity")}
        if atype == "notify":
            return {"result": "queued"}
        return {"result": "noop"}
