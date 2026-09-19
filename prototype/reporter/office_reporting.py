"""Office brief chapter for WatchLog daily reports.

Renders the 'office' payload produced by wl_office_brief (migration 0060) into
the daily report — plain text for WhatsApp and a compact card for the HTML
email. It sits between the security-event chapter and the (measurement-driven)
Site Intelligence chapter.

Voice matches the rest of the reporter: plain, specific, unexcited, numbers
over adjectives, no exclamation marks. Honesty is enforced in wording:
detections are called detections (never a certain headcount), a partial day is
labelled partial, and access "windows" approximate distinct visits rather than
claiming exact people.
"""
from __future__ import annotations

import html

# Agent-attributed / server-derived coverage-gap causes -> customer-facing wording.
_CAUSE = {
    # Honest: the Agent proves only that it was not running/observing, not the OS-level reason.
    "observation_gap": "site not monitored (agent not running)",
    "site_pc_suspend": "site not monitored (agent not running)",   # legacy alias
    "agent_restart": "agent restarting",
    "recorder_lan_lost": "recorder LAN lost",
    "cloud_link_lost": "cloud link lost",
    "agent_unreachable": "agent offline",
    "cloud_link_gap": "cloud link lost",
    "startup": "agent starting",
    "unknown": "not verified",
}


def _fmt_local(iso, tz: str) -> str:
    """An ISO timestamp -> HH:MM in the site's timezone (gaps arrive as UTC ISO)."""
    if not iso:
        return "?"
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(tz)).strftime("%H:%M")
    except Exception:                                  # noqa: BLE001
        return str(iso)[11:16]


def _coverage_line(report: dict) -> "str | None":
    """One honest monitoring-coverage line: what fraction of the period WatchLog actually
    verified, and (when < 100%) the largest not-monitored windows with their cause."""
    mc = (report.get("office") or {}).get("monitoring_coverage") or {}
    ratio = mc.get("coverage_ratio")
    if ratio is None:
        return None
    pct = round(float(ratio) * 100)
    if pct >= 100:
        return "Monitoring coverage: 100% of the reporting period."
    tz = report.get("timezone") or "UTC"
    gaps = mc.get("gaps") or []
    shown = "; ".join(f"{_fmt_local(g.get('start'), tz)}-{_fmt_local(g.get('end'), tz)} "
                      f"({_CAUSE.get(g.get('cause'), g.get('cause') or 'not verified')})"
                      for g in gaps[:2])
    tail = f" Not monitored: {shown}." if shown else ""
    return f"Monitoring coverage: {pct}% of the reporting period.{tail}"


def has_office(report: dict) -> bool:
    o = report.get("office") or {}
    cov = o.get("coverage") or {}
    return int(cov.get("person_events") or 0) > 0


def _windows(n: int) -> str:
    return f"{n} window" if n == 1 else f"{n} windows"


def office_lines(report: dict) -> list[str]:
    o = report.get("office") or {}
    if not has_office(report):
        return []
    cov = o.get("coverage") or {}
    lines: list[str] = ["*Office activity*"]

    first, last = cov.get("first"), cov.get("last")
    span = f"Active {first}–{last}." if first and last else "Activity recorded."
    if not cov.get("full_day", False):
        span += " Partial day — limited camera coverage, not a full working day."
    lines.append(span)

    ph = o.get("peak_hour") or {}
    if ph.get("count"):
        lines.append(f"Busiest around {int(ph['hour']):02d}:00 ({ph['count']} detections).")

    by_area = o.get("by_area") or []
    if by_area:
        parts = ", ".join(f"{a.get('camera')} {a.get('events')} ({_windows(int(a.get('episodes') or 0))})"
                          for a in by_area[:5])
        lines.append(f"By area: {parts}.")

    for r in (o.get("restricted") or []):
        ah = int(r.get("after_hours") or 0)
        tail = f"{ah} after-hours" if ah else "no after-hours access"
        lines.append(f"{r.get('camera')}: {_windows(int(r.get('episodes') or 0))} of access, "
                     f"last {r.get('last')}, {tail}.")

    after = int(o.get("after_hours_total") or 0)
    lines.append(f"After-hours activity: {after} detection{'s' if after != 1 else ''} outside 08:00–19:00."
                 if after else "No after-hours activity.")

    agent = o.get("agent") or {}
    if agent and not agent.get("online", True):
        lines.append("Note: the site agent is not currently reporting — today's coverage may be incomplete.")

    cov = _coverage_line(report)
    if cov:
        lines.append(cov)

    lines.append("(Figures are activity detections, not a unique headcount.)")
    return lines


def compose_append(base_text: str, report: dict) -> str:
    lines = office_lines(report)
    if not lines:
        return base_text
    return base_text.rstrip() + "\n\n" + "\n".join(lines)


# ---------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------

VIOLET = "#5B21FF"
BLUE = "#1748D3"
ICE_BG = "#F3F7FF"
INK = "#0B0B0F"
MUTED = "#6B7280"
LINE = "#E1E7F0"


def _row(label: str, value: str) -> str:
    return (
        '<tr>'
        f'<td style="padding:6px 0;color:{MUTED};font-size:14px;">{html.escape(label)}</td>'
        f'<td style="padding:6px 0;color:{INK};font-size:14px;font-weight:600;text-align:right;">{html.escape(value)}</td>'
        '</tr>'
    )


def office_html(report: dict) -> str:
    o = report.get("office") or {}
    if not has_office(report):
        return ""
    cov = o.get("coverage") or {}
    rows = ""
    first, last = cov.get("first"), cov.get("last")
    if first and last:
        span = f"{first}–{last}" + ("" if cov.get("full_day") else " (partial day)")
        rows += _row("Active", span)
    ph = o.get("peak_hour") or {}
    if ph.get("count"):
        rows += _row("Busiest hour", f"{int(ph['hour']):02d}:00 — {ph['count']} detections")
    for a in (o.get("by_area") or [])[:5]:
        rows += _row(str(a.get("camera")), f"{a.get('events')} ({_windows(int(a.get('episodes') or 0))})")
    for r in (o.get("restricted") or []):
        ah = int(r.get("after_hours") or 0)
        rows += _row(f"{r.get('camera')} (restricted)",
                     f"{_windows(int(r.get('episodes') or 0))}, last {r.get('last')}"
                     + (f", {ah} after-hours" if ah else ""))
    rows += _row("After-hours activity", str(int(o.get("after_hours_total") or 0)))
    mc = o.get("monitoring_coverage") or {}
    if mc.get("coverage_ratio") is not None:
        rows += _row("Monitoring coverage", f"{round(float(mc['coverage_ratio']) * 100)}%")
    if not rows:
        return ""
    return (
        '<tr><td style="padding:18px 24px 0;">'
        f'<div style="background:{ICE_BG};border:1px solid {LINE};border-radius:12px;padding:14px 16px;">'
        f'<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{BLUE};font-weight:700;margin-bottom:8px;">Office activity</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        f'{rows}</table>'
        f'<div style="color:{MUTED};font-size:11px;margin-top:8px;">Figures are activity detections, not a unique headcount.</div>'
        '</div></td></tr>'
    )


def insert_html(rendered: str, report: dict) -> str:
    block = office_html(report)
    if not block:
        return rendered
    marker = '<tr><td style="padding:24px;">'
    pos = rendered.find(marker)
    if pos >= 0:
        return rendered[:pos] + block + rendered[pos:]
    closing = '</table></td></tr></table></body></html>'
    pos = rendered.rfind(closing)
    if pos >= 0:
        return rendered[:pos] + block + rendered[pos:]
    return rendered
