#!/usr/bin/env python3
"""Daily-intelligence WhatsApp message (item 18).

The third render surface for the ONE canonical wl_daily_intelligence dataset (0067) — a
concise, scannable WhatsApp text. WhatsApp uses *single asterisks* for bold. This is
deliberately short (a phone message, not a report): the incidents that need attention, a
one-line activity summary, honest coverage, and the standing caveat. Bounded in length so
a busy day can't produce a wall of text.

Pure/deterministic given the report JSON, so it is fully testable without a database or a
live WhatsApp/Evolution connection (delivery stays the report-runner's job, entitlement- and
recipient-gated).
"""
from __future__ import annotations

MAX_CHARS = 3500          # WhatsApp hard limit is ~4096; leave headroom for the client's number footer.
_SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}


try:
    from report_metrics import headline_metrics
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from report_metrics import headline_metrics


def _incident_lines(report: dict) -> list[str]:
    incidents = report.get("incidents") or []
    total = headline_metrics(report)["incidents_total"]   # shared canonical count
    if total == 0:
        return ["*No incidents* — a quiet day. Routine activity summary below."]
    out = [f"*{total} incident{'s' if total != 1 else ''} need attention:*"]
    for i in sorted(incidents, key=lambda x: (_SEV_ORDER.get(str(x.get("severity")).lower(), 9),
                                              str(x.get("time")))):
        sev = str(i.get("severity", "")).upper()
        typ = str(i.get("type", "")).replace("_", " ")
        cam = i.get("camera") or "unassigned"
        out.append(f"• {sev} — {typ} at {cam} ({i.get('time')})")
    return out


def _activity_line(report: dict) -> "str | None":
    o = report.get("office") or {}
    cov = o.get("coverage") or {}
    first, last = cov.get("first"), cov.get("last")
    if not (first and last):
        return None
    tail = "" if cov.get("full_day") else " (partial day)"
    ah = int(o.get("after_hours_total") or 0)
    ah_txt = f"; {ah} after-hours detection{'s' if ah != 1 else ''}" if ah else "; no after-hours activity"
    return f"Active {first}–{last}{tail}{ah_txt}."


def _coverage_line(report: dict) -> "str | None":
    cov = report.get("coverage") or {}
    ratio = cov.get("coverage_ratio")
    if ratio is None:
        return None
    pct = round(float(ratio) * 100)
    if pct >= 100:
        return "Monitoring coverage: 100%."
    gaps = cov.get("gaps") or []
    return (f"Monitoring coverage: {pct}% — {len(gaps)} gap"
            f"{'s' if len(gaps) != 1 else ''}; activity during a gap is unobserved.")


def render_whatsapp(report: dict) -> str:
    """Concise WhatsApp-formatted daily intelligence message from the canonical dataset."""
    meta = report.get("meta") or {}
    lines: list[str] = [f"*WatchLog — {meta.get('site', 'Site')}*",
                        f"Daily intelligence · {meta.get('date', '')}"]
    if meta.get("partial_day"):
        lines.append("_Partial day — up to report time._")
    lines.append("")
    lines += _incident_lines(report)

    m = headline_metrics(report)
    if m["opening"] or m["closing"]:
        lines += ["", "*Activity*", f"Open {m['opening'] or '—'} → close {m['closing'] or '—'}."]
    act = _activity_line(report)
    if act:
        if "*Activity*" not in lines:
            lines += ["", "*Activity*"]
        lines.append(act)

    cov = _coverage_line(report)
    if cov:
        lines.append(cov)

    lines += ["", "_Figures are camera detections, not a unique headcount._"]
    msg = "\n".join(lines)
    if len(msg) > MAX_CHARS:
        msg = msg[:MAX_CHARS - 1].rstrip() + "…"
    return msg


__all__ = ["render_whatsapp", "MAX_CHARS"]
