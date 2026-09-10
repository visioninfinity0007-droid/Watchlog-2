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

try:
    from report_metrics import headline_metrics
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from report_metrics import headline_metrics

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


def render_pdf_html(report: dict) -> str:
    """A PDF-optimized (table-based, no flexbox) branded document, rendered cleanly by the
    packaged xhtml2pdf engine. Headline numbers come from headline_metrics (shared source)."""
    m = headline_metrics(report)
    meta = report.get("meta") or {}
    db = report.get("day_boundaries") or {}
    incidents = report.get("incidents") or []
    restricted = report.get("restricted") or []
    people = (report.get("people") or {}).get("summary") or {}
    cov = report.get("coverage") or {}
    ratio = float(cov.get("coverage_ratio") or 1)
    honesty = report.get("honesty") or []

    inc_rows = "".join(
        f'<tr><td>{_sev_badge(i.get("severity"))}</td><td><b>{_esc(str(i.get("type","")).replace("_"," "))}</b></td>'
        f'<td>{_esc(i.get("camera"))}</td><td>{_esc(i.get("time"))}</td></tr>' for i in incidents)
    inc_block = (f'<h2>What needs attention</h2><table class="grid" repeat="1"><thead><tr><th>Severity</th>'
                 f'<th>Type</th><th>Camera</th><th>Time</th></tr></thead><tbody>{inc_rows}</tbody></table>'
                 if incidents else '<h2>What needs attention</h2><p class="quiet">No incidents promoted — a quiet day.</p>')
    restr_block = ("".join(f'<tr><td>{_esc(r.get("camera"))}</td><td>{_esc(r.get("purpose"))}</td>'
                           f'<td>{_esc(r.get("episodes"))}</td><td>{_esc(r.get("last"))}</td></tr>' for r in restricted))
    restr_block = (f'<h2>Restricted-area access</h2><table class="grid"><thead><tr><th>Camera</th><th>Purpose</th>'
                   f'<th>Windows</th><th>Last</th></tr></thead><tbody>{restr_block}</tbody></table>' if restricted else "")
    cov_warn = ("" if ratio >= 0.999 else
                f'<p class="warn">Monitoring was not continuous — {_esc(len(cov.get("gaps") or []))} gap(s); activity during a gap is unobserved.</p>')
    honesty_html = "".join(f"<li>{_esc(h)}</li>" for h in honesty)
    partial = ' &nbsp; <b>PARTIAL DAY</b>' if meta.get("partial_day") else ''

    css = ("@page { size: A4; margin: 1.5cm; }"
           "body { font-family: Helvetica, Arial, sans-serif; color:#0B0B0F; font-size:10pt; }"
           "h1 { color:#1748D3; font-size:17pt; margin:0; }"
           "h2 { color:#1748D3; font-size:11pt; border-bottom:1px solid #E1E7F0; padding-bottom:2px; margin:14px 0 6px; }"
           ".sub { color:#6B7280; font-size:9pt; }"
           "table { width:100%; border-collapse:collapse; }"
           "table.grid th { background:#F3F7FF; color:#6B7280; font-size:8pt; text-align:left; padding:4px 6px; }"
           "table.grid td { border-bottom:1px solid #E1E7F0; padding:4px 6px; }"
           "table.kv td { padding:3px 6px; border-bottom:1px solid #E1E7F0; }"
           ".hero { background:#F3F7FF; padding:8px; }"
           ".warn { background:#FFFAEB; color:#B54708; padding:6px; font-size:9pt; }"
           ".quiet { color:#6B7280; }"
           ".honesty { color:#6B7280; font-size:8pt; }")
    return (
        f'<html><head><meta charset="utf-8"><style>{css}</style></head><body>'
        f'<table><tr><td><h1>WatchLog</h1><div class="sub">Daily Site Intelligence &middot; by Vision Infinity</div></td>'
        f'<td align="right"><b>{_esc(m["site"])}</b><br/>{_esc(m["date"])} &middot; {_esc(meta.get("timezone"))}{partial}</td></tr></table>'
        f'<div class="hero"><table class="kv">'
        f'<tr><td>Opening</td><td><b>{_esc(m["opening"] or "—")}</b></td><td>Closing</td><td><b>{_esc(m["closing"] or "—")}</b></td>'
        f'<td>Coverage</td><td><b>{_esc((str(m["coverage_pct"])+"%") if m["coverage_pct"] is not None else "—")}</b></td></tr>'
        f'<tr><td>Incidents</td><td><b>{m["incidents_total"]}</b> ({m["critical"]} critical)</td>'
        f'<td>After-hours</td><td><b>{_esc(m["after_hours"] if m["after_hours_verified"] else "not verified")}</b></td>'
        f'<td>People</td><td class="sub">{m["probable_regular_staff"]} staff / {m["probable_visitor"]} visitor / {m["unclassified"]} unclassified (estimated)</td></tr>'
        f'</table></div>'
        f'{cov_warn}{inc_block}{restr_block}'
        f'<ul class="honesty">{honesty_html}</ul>'
        f'<div class="sub">Generated {_esc(meta.get("generated_at"))} &middot; {_esc(m["schema"])} &middot; detections are events observed on site, not a headcount.</div>'
        '</body></html>')


def render_pdf(report: dict) -> "bytes | None":
    """Real PDF bytes from a packaged production engine. Tries WeasyPrint (high fidelity, if
    installed) then xhtml2pdf (pure-Python, the shipped default). Returns None only when NO
    engine is available — never a fake/placeholder PDF."""
    try:
        from weasyprint import HTML   # noqa: PLC0415
        return HTML(string=render_html(report)).write_pdf()
    except Exception:                 # noqa: BLE001 — WeasyPrint not installed / failed; fall through
        pass
    try:
        import io
        from xhtml2pdf import pisa    # noqa: PLC0415
        buf = io.BytesIO()
        status = pisa.CreatePDF(render_pdf_html(report), dest=buf)
        data = buf.getvalue()
        if status.err or not data.startswith(b"%PDF"):
            return None
        return data
    except Exception:                 # noqa: BLE001
        return None


def pdf_engine_available() -> bool:
    for mod in ("weasyprint", "xhtml2pdf"):
        try:
            __import__(mod)
            return True
        except Exception:      # noqa: BLE001
            continue
    return False


__all__ = ["render_html", "render_pdf_html", "render_pdf", "pdf_engine_available"]
