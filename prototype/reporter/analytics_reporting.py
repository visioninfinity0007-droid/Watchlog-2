"""Analytics Studio additions to WatchLog daily reports.

The original security-event report remains authoritative. This module adds a
compact Site Intelligence chapter only when measurements exist. It intentionally
does not turn a visitor count into an incident and does not claim CCTV can count
completed till transactions.
"""
from __future__ import annotations

import html

VIOLET = "#5B21FF"
BLUE = "#1748D3"
ICE_BG = "#F3F7FF"
INK = "#0B0B0F"
MUTED = "#6B7280"
LINE = "#E1E7F0"


def has_measurements(report: dict) -> bool:
    a = report.get("analytics") or {}
    return int(a.get("measurements") or 0) > 0


def intelligence_lines(report: dict) -> list[str]:
    a = report.get("analytics") or {}
    if not has_measurements(report):
        return []
    rows = []
    vin, vout = int(a.get("visitor_in") or 0), int(a.get("visitor_out") or 0)
    cars_in, cars_out = int(a.get("vehicles_in") or 0), int(a.get("vehicles_out") or 0)
    zones = int(a.get("zone_entries") or 0)
    after = int(a.get("after_hours") or 0)
    checkout = int(a.get("checkout_peak") or 0)
    if vin or vout:
        rows.append(f"Visitor flow: {vin} in, {vout} out.")
    if cars_in or cars_out:
        rows.append(f"Vehicle flow: {cars_in} in, {cars_out} out.")
    if checkout:
        rows.append(f"Checkout activity: peak {checkout} people in the configured checkout zone.")
    if zones:
        rows.append(f"Zone activity: {zones} configured zone entr{'y' if zones == 1 else 'ies'}.")
    if after:
        rows.append(f"After-hours monitoring: {after} activit{'y' if after == 1 else 'ies'} recorded outside schedule.")
    return rows


def compose(base_compose, report: dict) -> str:
    base = base_compose(report)
    lines = intelligence_lines(report)
    if not lines:
        return base
    return base.rstrip() + "\n\n*Site intelligence*\n" + "\n".join(lines)


def _row(label: str, value: str) -> str:
    return (
        '<tr>'
        f'<td style="padding:6px 0;color:{MUTED};font-size:14px;">{html.escape(label)}</td>'
        f'<td style="padding:6px 0;color:{INK};font-size:14px;font-weight:600;text-align:right;">{html.escape(value)}</td>'
        '</tr>'
    )


def intelligence_html(report: dict) -> str:
    a = report.get("analytics") or {}
    if not has_measurements(report):
        return ""
    rows = ""
    vin, vout = int(a.get("visitor_in") or 0), int(a.get("visitor_out") or 0)
    ci, co = int(a.get("vehicles_in") or 0), int(a.get("vehicles_out") or 0)
    checkout = int(a.get("checkout_peak") or 0)
    zones = int(a.get("zone_entries") or 0)
    after = int(a.get("after_hours") or 0)
    if vin or vout:
        rows += _row("Visitor flow", f"{vin} in / {vout} out")
    if ci or co:
        rows += _row("Vehicle flow", f"{ci} in / {co} out")
    if checkout:
        rows += _row("Checkout peak", f"{checkout} people")
    if zones:
        rows += _row("Zone entries", str(zones))
    if after:
        rows += _row("After-hours activity", str(after))
    if not rows:
        rows = _row("Analytics measurements", str(int(a.get("measurements") or 0)))
    return (
        '<tr><td style="padding:18px 24px 0;">'
        f'<div style="background:{ICE_BG};border:1px solid {LINE};border-radius:12px;padding:14px 16px;">'
        f'<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{BLUE};font-weight:700;margin-bottom:8px;">Site intelligence</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        f'{rows}</table></div></td></tr>'
    )


def render_html(base_render, report: dict, portal_url: str = "#") -> str:
    """Insert analytics before the existing CTA without cloning email layout."""
    rendered = base_render(report, portal_url)
    block = intelligence_html(report)
    if not block:
        return rendered
    # The base template always has this exact CTA wrapper. If it changes, the
    # safe fallback is appending before the closing content table, not dropping
    # the report or failing delivery.
    marker = '<tr><td style="padding:24px;">'
    pos = rendered.find(marker)
    if pos >= 0:
        return rendered[:pos] + block + rendered[pos:]
    closing = '</table></td></tr></table></body></html>'
    pos = rendered.rfind(closing)
    if pos >= 0:
        return rendered[:pos] + block + rendered[pos:]
    return rendered
