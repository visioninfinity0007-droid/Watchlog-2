#!/usr/bin/env python3
"""Professional daily-intelligence report (item 17).

Renders the ONE canonical dataset from wl_daily_intelligence (0067) into a branded,
print-ready A4 document. HTML is the always-available deliverable — it is self-contained
(embedded CSS, @page rules, print color-adjust) so "Save as PDF" in any browser, a headless
Chrome in the report-runner, or WeasyPrint produces an identical PDF. render_pdf() emits real
PDF bytes when an HTML->PDF engine is installed, and returns None otherwise — it never
fabricates a PDF file.

Pure/deterministic given the report JSON, so it is fully testable without a database.
"""
from __future__ import annotations

import html as _html

# Brand palette — identical to office_reporting so the PDF matches the WhatsApp/email surfaces.
VIOLET = "#5B21FF"
BLUE = "#1748D3"
ICE_BG = "#F3F7FF"
INK = "#0B0B0F"
MUTED = "#6B7280"
LINE = "#E1E7F0"
SEV = {"critical": ("#B42318", "#FEF3F2"),
       "warning": ("#B54708", "#FFFAEB"),
       "info": ("#175CD3", "#EFF8FF")}


def _esc(v) -> str:
    return _html.escape("" if v is None else str(v))


def _dur(seconds) -> str:
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _pct(ratio) -> str:
    try:
        return f"{round(float(ratio) * 100)}%"
    except (TypeError, ValueError):
        return "—"


def _sev_badge(sev: str) -> str:
    fg, bg = SEV.get(str(sev).lower(), (MUTED, "#F3F4F6"))
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
            f'background:{bg};color:{fg};font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:.04em;">{_esc(sev)}</span>')


def _attention_hero(attn: dict) -> str:
    total = int(attn.get("incidents_total") or 0)
    if total == 0:
        return (f'<div class="hero quiet"><div class="hero-n">0</div>'
                f'<div class="hero-t">No incidents promoted — a quiet day. Routine activity '
                f'is summarised below.</div></div>')
    cells = ""
    for sev in ("critical", "warning", "info"):
        n = int(attn.get(sev) or 0)
        if n:
            fg, bg = SEV[sev]
            cells += (f'<span class="chip" style="background:{bg};color:{fg};">'
                      f'{n} {_esc(sev)}</span>')
    return (f'<div class="hero alert"><div class="hero-n">{total}</div>'
            f'<div class="hero-t">incident{"s" if total != 1 else ""} need attention '
            f'today<div class="chips">{cells}</div></div></div>')


def _incidents_table(incidents: list) -> str:
    if not incidents:
        return ""
    rows = ""
    for i in incidents:
        rows += (
            "<tr>"
            f'<td>{_sev_badge(i.get("severity"))}</td>'
            f'<td><b>{_esc(str(i.get("type","")).replace("_"," "))}</b></td>'
            f'<td>{_esc(i.get("camera"))}</td>'
            f'<td class="num">{_esc(i.get("time"))}</td>'
            f'<td>{_esc(i.get("status"))}</td>'
            "</tr>")
    return _section("What needs attention",
                    '<table class="grid"><thead><tr><th>Severity</th><th>Type</th>'
                    '<th>Camera</th><th>Time</th><th>Status</th></tr></thead>'
                    f'<tbody>{rows}</tbody></table>')


def _access_table(windows: list) -> str:
    if not windows:
        return ""
    rows = ""
    for w in windows[:40]:
        rows += (
            "<tr>"
            f'<td><b>{_esc(w.get("camera"))}</b><span class="sub">{_esc(w.get("purpose"))}</span></td>'
            f'<td class="num">{_esc(w.get("start"))}–{_esc(w.get("end"))}</td>'
            f'<td class="num">{_dur(w.get("dwell_seconds"))}</td>'
            f'<td class="num">{_esc(w.get("detections"))}</td>'
            "</tr>")
    return _section("Access windows",
                    '<table class="grid"><thead><tr><th>Area</th><th>Window</th>'
                    '<th>Dwell</th><th>Detections</th></tr></thead>'
                    f'<tbody>{rows}</tbody></table>')


def _office_block(office: dict) -> str:
    if not office:
        return ""
    cov = office.get("coverage") or {}
    rows = ""

    def row(label, value):
        return (f'<tr><td class="k">{_esc(label)}</td>'
                f'<td class="v">{_esc(value)}</td></tr>')

    first, last = cov.get("first"), cov.get("last")
    if first and last:
        rows += row("Active", f"{first}–{last}" + ("" if cov.get("full_day") else " (partial day)"))
    ph = office.get("peak_hour") or {}
    if ph.get("count"):
        rows += row("Busiest hour", f"{int(ph['hour']):02d}:00 — {ph['count']} detections")
    for a in (office.get("by_area") or [])[:6]:
        rows += row(str(a.get("camera")), f"{a.get('events')} detections")
    rows += row("After-hours activity", str(int(office.get("after_hours_total") or 0)))
    if not rows:
        return ""
    return _section("Office activity",
                    f'<table class="kv">{rows}</table>')


def _coverage_block(coverage: dict) -> str:
    if not coverage or coverage.get("coverage_ratio") is None:
        return ""
    ratio = float(coverage.get("coverage_ratio") or 0)
    gaps = coverage.get("gaps") or []
    warn = "" if ratio >= 0.999 else (
        f'<div class="warn">Monitoring was not continuous — {len(gaps)} gap'
        f'{"s" if len(gaps) != 1 else ""} in this window. Activity during a gap is unobserved.</div>')
    return _section("Monitoring coverage",
                    f'<div class="cov"><span class="cov-n">{_pct(ratio)}</span>'
                    f'<span class="cov-t">of the reporting window verified</span></div>{warn}')


def _honesty(honesty: list) -> str:
    if not honesty:
        return ""
    items = "".join(f"<li>{_esc(h)}</li>" for h in honesty)
    return f'<div class="honesty"><ul>{items}</ul></div>'


def _section(title: str, body: str) -> str:
    return (f'<section><h2>{_esc(title)}</h2>{body}</section>')


_CSS = f"""
  * {{ box-sizing: border-box; }}
  @page {{ size: A4; margin: 15mm 14mm 18mm; }}
  html, body {{ margin:0; padding:0; }}
  body {{ font-family: -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
          color:{INK}; font-size:12.5px; line-height:1.45;
          -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
  .wrap {{ max-width: 760px; margin: 0 auto; padding: 8px 0 40px; }}
  header.brand {{ display:flex; justify-content:space-between; align-items:flex-end;
          border-bottom:3px solid {BLUE}; padding-bottom:12px; margin-bottom:6px; }}
  .brand .name {{ font-size:20px; font-weight:800; letter-spacing:-.01em; }}
  .brand .name span {{ color:{BLUE}; }}
  .brand .by {{ font-size:11px; color:{MUTED}; margin-top:2px; }}
  .brand .meta {{ text-align:right; font-size:12px; color:{MUTED}; }}
  .brand .meta b {{ color:{INK}; font-size:14px; display:block; }}
  .partial {{ display:inline-block; margin-top:4px; padding:2px 8px; border-radius:999px;
          background:{ICE_BG}; color:{BLUE}; font-size:10.5px; font-weight:700; }}
  .hero {{ display:flex; gap:14px; align-items:center; border-radius:12px;
          padding:14px 16px; margin:14px 0 6px; border:1px solid {LINE}; }}
  .hero.alert {{ background:{SEV['critical'][1]}; border-color:#FDA29B; }}
  .hero.quiet {{ background:{ICE_BG}; }}
  .hero-n {{ font-size:34px; font-weight:800; line-height:1; }}
  .hero.alert .hero-n {{ color:{SEV['critical'][0]}; }}
  .hero.quiet .hero-n {{ color:{BLUE}; }}
  .hero-t {{ font-size:13px; color:{INK}; }}
  .chips {{ margin-top:6px; }}
  .chip {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
          font-weight:700; margin-right:6px; text-transform:uppercase; letter-spacing:.03em; }}
  section {{ margin-top:16px; break-inside: avoid; }}
  h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:{BLUE};
          font-weight:800; margin:0 0 8px; }}
  table {{ width:100%; border-collapse:collapse; }}
  table.grid th {{ text-align:left; font-size:10.5px; text-transform:uppercase;
          letter-spacing:.04em; color:{MUTED}; padding:6px 8px; border-bottom:1px solid {LINE}; }}
  table.grid td {{ padding:8px; border-bottom:1px solid {LINE}; vertical-align:top; }}
  table.grid tr:nth-child(even) td {{ background:#FAFBFF; }}
  td.num {{ font-variant-numeric: tabular-nums; white-space:nowrap; }}
  .sub {{ display:block; color:{MUTED}; font-size:10.5px; }}
  table.kv td {{ padding:6px 0; border-bottom:1px solid {LINE}; }}
  table.kv .k {{ color:{MUTED}; }}
  table.kv .v {{ text-align:right; font-weight:600; }}
  .cov {{ display:flex; align-items:baseline; gap:10px; }}
  .cov-n {{ font-size:26px; font-weight:800; color:{BLUE}; }}
  .cov-t {{ color:{MUTED}; font-size:12px; }}
  .warn {{ margin-top:8px; padding:8px 10px; border-radius:8px; background:{SEV['warning'][1]};
          color:{SEV['warning'][0]}; font-size:11.5px; font-weight:600; }}
  .honesty {{ margin-top:20px; border-top:1px solid {LINE}; padding-top:10px; }}
  .honesty ul {{ margin:0; padding-left:16px; }}
  .honesty li {{ color:{MUTED}; font-size:10.5px; margin:2px 0; }}
  footer.foot {{ margin-top:14px; text-align:center; color:{MUTED}; font-size:10px; }}
"""


def render_html(report: dict) -> str:
    """Complete standalone print-ready A4 HTML document for the daily intelligence report."""
    meta = report.get("meta") or {}
    attn = report.get("attention") or {}
    partial = ('<span class="partial">PARTIAL DAY — up to report time</span>'
               if meta.get("partial_day") else "")
    body = (
        _attention_hero(attn)
        + _incidents_table(report.get("incidents") or [])
        + _access_table(report.get("access_windows") or [])
        + _office_block(report.get("office") or {})
        + _coverage_block(report.get("coverage") or {})
        + _honesty(report.get("honesty") or [])
    )
    generated = _esc(meta.get("generated_at"))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>WatchLog — {_esc(meta.get('site'))} — {_esc(meta.get('date'))}</title>"
        f"<style>{_CSS}</style></head><body><div class=\"wrap\">"
        '<header class="brand"><div>'
        '<div class="name">Watch<span>Log</span></div>'
        '<div class="by">Daily Site Intelligence · by Vision Infinity</div></div>'
        f'<div class="meta"><b>{_esc(meta.get("site"))}</b>{_esc(meta.get("date"))}'
        f' · {_esc(meta.get("timezone"))}<br>{partial}</div></header>'
        f"{body}"
        f'<footer class="foot">Generated {generated} · WatchLog {_esc(report.get("schema"))} · '
        'Detections are events observed on site, not a headcount.</footer>'
        "</div></body></html>"
    )


def render_pdf(report: dict) -> "bytes | None":
    """Real PDF bytes when an HTML->PDF engine (WeasyPrint) is installed; else None.

    Never returns a fake/placeholder PDF — callers fall back to shipping render_html() and
    letting a browser or the report-runner's headless converter produce the PDF.
    """
    doc = render_html(report)
    try:
        from weasyprint import HTML   # noqa: PLC0415
    except Exception:                 # noqa: BLE001 — engine not installed in this env
        return None
    try:
        return HTML(string=doc).write_pdf()
    except Exception:                 # noqa: BLE001
        return None


def pdf_engine_available() -> bool:
    try:
        import weasyprint  # noqa: F401,PLC0415
        return True
    except Exception:      # noqa: BLE001
        return False


__all__ = ["render_html", "render_pdf", "pdf_engine_available"]
