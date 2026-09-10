#!/usr/bin/env python3
"""AI Site Configuration Skill (item 2) — a versioned, DETERMINISTIC advisor.

Consumes: a recorder Capability Profile (the output of wl_recorder_profile / the 0061 KB),
a Site Context (type, goals, what's been mapped, operating hours), and the Site Control action
catalogue. Produces: a site diagnosis, per-analytic recommendation, SAFE Site Control write
proposals, WatchLog software-analytics recommendations, honest unsupported responses, and the
questions a human must answer first.

It respects the evidence hierarchy exactly:
    FIELD_VERIFIED > OFFICIAL_DOCUMENTED > IMPLEMENTED_UNVERIFIED > UNSUPPORTED > UNKNOWN
and enforces the same gate as the safe-write plane (0064): a recorder WRITE is only ever
proposed when a capability is verdict=supported AND write-capable AND safe_write AND
FIELD_VERIFIED. Everything softer becomes "verify first", "enable on the camera", "use WatchLog
software analytics", or an honest "not available" — never a fabricated assumption of support.

Pure logic, no I/O — the profile and context are injected, so it is fully testable offline.
"""
from __future__ import annotations

SKILL_VERSION = "site-config-advisor-v1"

# Evidence hierarchy rank (lower = stronger).
_EV_RANK = {"FIELD_VERIFIED": 0, "OFFICIAL_DOCUMENTED": 1, "IMPLEMENTED_UNVERIFIED": 2, "UNKNOWN": 4}

# Analytics WatchLog can deliver in SOFTWARE (on-site vision + server policies), independent of
# recorder support. Deliberately does NOT include people_counting (no reliable unique count) or
# face recognition (out of scope) — offering those would be dishonest.
WATCHLOG_SOFTWARE_ANALYTICS = {
    "human_vehicle_classification", "restricted_area", "after_hours", "dwell",
    "line_crossing", "intrusion", "people_presence",
}

# Default goals per site type (context.goals overrides).
SITE_TYPE_GOALS = {
    "office":     ["human_vehicle_classification", "restricted_area", "after_hours", "line_crossing"],
    "retail":     ["human_vehicle_classification", "people_counting", "line_crossing", "dwell", "after_hours"],
    "factory":    ["line_crossing", "intrusion", "restricted_area", "after_hours"],
    "restaurant": ["human_vehicle_classification", "people_counting", "dwell", "after_hours"],
    "warehouse":  ["line_crossing", "intrusion", "restricted_area", "after_hours"],
    "clinic":     ["human_vehicle_classification", "restricted_area", "after_hours"],
}

# Analytics that need placement/context questions before they can be configured.
_NEEDS_LINE_PLACEMENT = {"line_crossing", "intrusion"}
_NEEDS_RESTRICTED_MAP = {"restricted_area"}
_NEEDS_HOURS = {"after_hours"}

_HUMAN = "recommendation only — a human approves any recorder change"


def _cap(profile: dict, analytic: str) -> dict:
    caps = (profile or {}).get("capabilities", {}) or {}
    c = caps.get(analytic)
    if c:
        return c
    return {"verdict": "unknown", "evidence_class": "UNKNOWN", "ai_location": None, "safety_class": "na"}


def advise(profile: dict, context: dict) -> dict:
    """Produce the full recommendation package for one recorder + site context."""
    context = context or {}
    site_type = context.get("site_type")
    goals = context.get("goals") or SITE_TYPE_GOALS.get(site_type, ["human_vehicle_classification", "after_hours"])

    recommendations, proposals, software, unsupported, questions = [], [], [], [], []
    evidence_notes = []

    for a in goals:
        c = _cap(profile, a)
        verdict, ev = c.get("verdict"), c.get("evidence_class")
        safety = c.get("safety_class")

        if verdict == "supported":
            if ev == "FIELD_VERIFIED" and safety == "safe_write":
                recommendations.append(_rec(a, "recorder_configure", ev,
                    f"{a} is field-verified on this exact recorder and is a safe write — configure it via Site Control."))
                proposals.append({"analytic": a, "action": f"configure_{a}", "capability": a,
                                  "safety_class": "safe_write", "evidence_class": ev, "requires_approval": True})
            else:
                recommendations.append(_rec(a, "recorder_needs_verification", ev,
                    f"{a} is documented as supported ({ev}) but not field-verified on this device — run a Site Control READ to confirm before any write."))
                evidence_notes.append(f"{a}: supported per {ev}; not yet FIELD_VERIFIED — do not blind-write.")
        elif verdict == "by_camera":
            recommendations.append(_rec(a, "by_camera", ev,
                f"{a} is a camera-side capability on this recorder — enable it on the supporting camera, not the recorder."))
        else:  # unsupported OR unknown
            if a in WATCHLOG_SOFTWARE_ANALYTICS:
                why = ("the recorder does not support it" if verdict == "unsupported"
                       else "recorder support is unverified")
                recommendations.append(_rec(a, "watchlog_software", ev,
                    f"{a}: {why} — recommend WatchLog software-defined {a} analytics (on-site vision + policy)."))
                software.append(a)
            else:
                recommendations.append(_rec(a, "not_available", ev,
                    f"{a} is neither supported by this recorder nor offered by WatchLog software — not available."))
                unsupported.append({"analytic": a, "reason":
                    ("recorder unsupported and not a WatchLog software capability" if verdict == "unsupported"
                     else "recorder support unknown and not a WatchLog software capability")})
            if verdict == "unknown":
                questions.append(f"Recorder support for {a} is unknown — may I run a Site Control READ to verify it on your device?")

        # context questions
        if a in _NEEDS_HOURS and not context.get("operating_hours_known"):
            questions.append("What are the site's operating hours and working days?")
        if a in _NEEDS_RESTRICTED_MAP and not context.get("restricted_cameras_mapped"):
            questions.append("Which cameras view restricted areas (e.g. armory, vault, server room)?")
        if a in _NEEDS_LINE_PLACEMENT and not context.get("entrance_cameras_mapped"):
            questions.append("Which cameras cover the entrances/perimeter where a line or zone should be drawn?")

    diagnosis = {
        "recorder": {"vendor": (profile or {}).get("vendor"), "model": (profile or {}).get("model")},
        "site_type": site_type,
        "summary": _summary(recommendations),
        "evidence_notes": evidence_notes,
    }
    return {
        "version": SKILL_VERSION,
        "diagnosis": diagnosis,
        "recommendations": recommendations,
        "site_control_proposals": proposals,      # only field-verified safe writes reach here
        "software_analytics": sorted(set(software)),
        "unsupported": unsupported,
        "human_questions": _dedup(questions),
        "note": _HUMAN,
    }


def _rec(analytic, decision, evidence_class, rationale) -> dict:
    return {"analytic": analytic, "decision": decision, "evidence_class": evidence_class, "rationale": rationale}


def _summary(recs) -> str:
    by = {}
    for r in recs:
        by[r["decision"]] = by.get(r["decision"], 0) + 1
    parts = [f"{n} {k.replace('_',' ')}" for k, n in sorted(by.items())]
    return "; ".join(parts) if parts else "no goals evaluated"


def _dedup(seq):
    out, seen = [], set()
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


__all__ = ["advise", "SKILL_VERSION", "WATCHLOG_SOFTWARE_ANALYTICS", "SITE_TYPE_GOALS"]
