#!/usr/bin/env python3
"""
Branded HTML for the daily report email (Milestone 3).

The signed scope calls for a "branded HTML daily report". This renders one
site-day into an email-client-safe HTML document: table layout, everything
inline-styled (Gmail/Outlook strip <style> and <svg>), a system font stack,
one violet accent from the WatchLog brand, and a single call-to-action to
the portal. The plain-text body (composed elsewhere) is sent alongside as
the fallback part.

Voice matches BRAND_GUIDELINES and the WhatsApp copy: plain, specific,
unexcited, numbers over adjectives, a quiet night reported as a quiet night.

    from email_template import render_html, subject
    html = render_html(report, portal_url="https://app.watchlog.example")
    subj = subject(report)

`report` is the dict returned by wl_daily_report():
  site, date, timezone, total_events, after_hours_events,
  by_camera [{camera,count}], by_type {type:count}, faults {type:count},
  first_event_at, last_event_at.

Every dynamic value is HTML-escaped. No remote images or scripts.
"""

from __future__ import annotations

import html as _html
from datetime import datetime
from zoneinfo import ZoneInfo

# Brand (design-tokens/tokens/color.json — kept in sync by hand here because
# email needs literal hex, not CSS variables).
VIOLET = "#5B21FF"
INK = "#0B0B0F"
MUTED = "#6B7280"
CANVAS = "#F4F4F7"
CARD = "#FFFFFF"
LINE = "#E7E7EE"
AMBER = "#8F5300"      # fault text, light-surface variant (WCAG-checked)
AMBER_BG = "#FDF6EC"

FRIENDLY = {
    "motion": "Motion", "person": "Person", "vehicle": "Vehicle",
    "intrusion": "Intrusion", "line_crossing": "Line crossing",
    "tamper": "Tamper", "video_loss": "Video loss",
    "disk_error": "Disk error", "offline": "Camera offline",
}
FAULTS = {"tamper", "video_loss", "disk_error", "offline"}

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
        "Arial,sans-serif")


def _e(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=True)


def _pretty_date(when) -> str:
    try:
        return datetime.strptime(str(when), "%Y-%m-%d").strftime("%A %d %B").lstrip("0")
    except Exception:  # noqa: BLE001
        return _e(when)


def _hhmm(ts, tz: str) -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(tz or "UTC")).strftime("%H:%M")
    except Exception:  # noqa: BLE001
        return str(ts)[11:16]


def subject(report: dict) -> str:
    # The subject is a plain-text header. Strip angle brackets so a
    # hostile site/camera name can never carry markup into any surface that
    # later renders the subject (e.g. the <title>).
    site = (str(report.get("site") or "Site")
            .replace("<", " ").replace(">", " ").strip()) or "Site"
    total = int(report.get("total_events") or 0)
    try:
        d = datetime.strptime(str(report.get("date")), "%Y-%m-%d").strftime("%d %b").lstrip("0")
    except Exception:  # noqa: BLE001
        d = str(report.get("date"))
    if total == 0:
        return f"WatchLog — {site} — quiet night, {d}"
    tail = f"{total:,} event" + ("" if total == 1 else "s")
    return f"WatchLog — {site} — {tail}, {d}"


def _row(label_html: str, value_html: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:6px 0;color:{MUTED};font-size:14px;">{label_html}</td>'
        f'<td style="padding:6px 0;color:{INK};font-size:14px;font-weight:600;'
        f'text-align:right;">{value_html}</td></tr>')


def _section(title: str, inner_html: str, accent: str = INK) -> str:
    return (
        f'<tr><td style="padding:18px 24px 0;">'
        f'<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;'
        f'color:{MUTED};font-weight:700;margin-bottom:8px;">{_e(title)}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;">{inner_html}</table>'
        f'</td></tr>')


def render_html(report: dict, portal_url: str = "#") -> str:
    site = _e(report.get("site") or "Site")
    tz = report.get("timezone") or "UTC"
    total = int(report.get("total_events") or 0)
    after = int(report.get("after_hours_events") or 0)
    portal = _e(portal_url)
    date_h = _pretty_date(report.get("date"))

    # Hero
    if total == 0:
        hero = (
            f'<div style="font-size:30px;line-height:1.2;font-weight:700;color:{INK};">'
            f'Nothing to report</div>'
            f'<div style="margin-top:6px;color:{MUTED};font-size:15px;">'
            f'A quiet night. No events.</div>')
    else:
        after_h = (f' &middot; <span style="color:{VIOLET};font-weight:600;">'
                   f'{after} after hours</span>') if after else ""
        hero = (
            f'<div style="font-size:44px;line-height:1;font-weight:800;color:{INK};">'
            f'{total:,}</div>'
            f'<div style="margin-top:6px;color:{MUTED};font-size:15px;">'
            f'event{"" if total == 1 else "s"}{after_h}</div>')

    sections = ""

    by_cam = report.get("by_camera") or []
    if by_cam:
        rows = "".join(_row(_e(c.get("camera")), _e(c.get("count")))
                       for c in by_cam[:5])
        sections += _section("By camera", rows)

    by_type = report.get("by_type") or {}
    incidents = {k: v for k, v in by_type.items() if k not in FAULTS}
    if incidents:
        rows = "".join(_row(_e(FRIENDLY.get(k, k)), _e(v))
                       for k, v in sorted(incidents.items(), key=lambda kv: -kv[1]))
        sections += _section("By type", rows)

    faults = report.get("faults") or {}
    if faults:
        rows = "".join(_row(_e(FRIENDLY.get(k, k)), _e(v)) for k, v in faults.items())
        sections += (
            f'<tr><td style="padding:18px 24px 0;">'
            f'<div style="background:{AMBER_BG};border:1px solid #F0E2CC;border-radius:10px;'
            f'padding:12px 14px;">'
            f'<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;'
            f'color:{AMBER};font-weight:700;margin-bottom:6px;">Needs attention</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;">{rows}</table></div></td></tr>')

    first, last = report.get("first_event_at"), report.get("last_event_at")
    if total and first and last:
        sections += _section(
            "Window",
            _row("First event", _e(_hhmm(first, tz)))
            + _row("Last event", _e(_hhmm(last, tz))))

    cta = (
        f'<tr><td style="padding:24px;">'
        f'<a href="{portal}" style="display:inline-block;background:{VIOLET};color:#fff;'
        f'text-decoration:none;font-weight:600;font-size:15px;padding:12px 22px;'
        f'border-radius:10px;">Open WatchLog</a></td></tr>')

    return (
        f'<!DOCTYPE html><html lang="en"><head>'
        f'<meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light">'
        f'<title>{_e(subject(report))}</title></head>'
        f'<body style="margin:0;padding:0;background:{CANVAS};">'
        f'<span style="display:none!important;visibility:hidden;opacity:0;height:0;'
        f'width:0;overflow:hidden;">'
        f'{site}: {total} event{"" if total == 1 else "s"} on {date_h}.</span>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{CANVAS};padding:24px 12px;">'
        f'<tr><td align="center">'
        f'<table role="presentation" width="560" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;width:100%;background:{CARD};border:1px solid {LINE};'
        f'border-radius:16px;overflow:hidden;font-family:{FONT};">'
        # header
        f'<tr><td style="background:{VIOLET};padding:20px 24px;">'
        f'<div style="color:#fff;font-size:20px;font-weight:800;letter-spacing:-.02em;">'
        f'WatchLog</div>'
        f'<div style="color:#E9E1FF;font-size:13px;margin-top:2px;">Daily report</div>'
        f'</td></tr>'
        # site + date
        f'<tr><td style="padding:22px 24px 0;">'
        f'<div style="font-size:16px;font-weight:700;color:{INK};">{site}</div>'
        f'<div style="color:{MUTED};font-size:14px;margin-top:2px;">{date_h} '
        f'&middot; {_e(tz)}</div></td></tr>'
        # hero
        f'<tr><td style="padding:16px 24px 4px;">{hero}</td></tr>'
        f'{sections}'
        f'{cta}'
        # footer
        f'<tr><td style="padding:18px 24px;border-top:1px solid {LINE};">'
        f'<div style="color:{MUTED};font-size:12px;line-height:1.5;">'
        f'You are receiving this because you are a WatchLog report recipient for '
        f'{site}. Manage recipients in the '
        f'<a href="{portal}" style="color:{VIOLET};text-decoration:none;">portal</a>.'
        f'</div></td></tr>'
        f'</table></td></tr></table></body></html>')
