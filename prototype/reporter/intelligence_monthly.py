#!/usr/bin/env python3
"""Monthly reporting render/export (item 13) — from the same canonical intelligence model.

Renders the wl_monthly_rollup (0069) dataset into a branded, print-ready monthly report, and
provides the organizational-aggregation contract (roll several sites' months into one org view).
Same brand + honesty rules as the daily PDF; render_pdf reuses the daily module's engine hook.

Pure/deterministic given the rollup JSON — testable without a database.
"""
from __future__ import annotations

try:
    import intelligence_pdf as ipdf
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import intelligence_pdf as ipdf

BLUE, INK, MUTED, LINE = ipdf.BLUE, ipdf.INK, ipdf.MUTED, ipdf.LINE
_esc, _pct, _sev_badge = ipdf._esc, ipdf._pct, ipdf._sev_badge


def _trend_bars(daily: list) -> str:
    if not daily:
        return '<div class="sub">No activity recorded this month.</div>'
    mx = max((int(d.get("detections") or 0) for d in daily), default=1) or 1
    cells = ""
    for d in daily:
        n = int(d.get("detections") or 0)
        h = max(2, round(46 * n / mx))
        cells += (f'<div class="bar" title="{_esc(d.get("date"))}: {n}">'
                  f'<div class="fill" style="height:{h}px"></div>'
                  f'<div class="lbl">{_esc(str(d.get("date"))[-2:])}</div></div>')
    return f'<div class="bars">{cells}</div>'


def render_monthly_html(rollup: dict) -> str:
    """Standalone print-ready HTML for one site's monthly rollup."""
    meta = rollup.get("meta") or {}
    inc = rollup.get("incidents") or {}
    act = rollup.get("activity") or {}
    cov = rollup.get("coverage") or {}
    busiest = act.get("busiest_day") or {}
    sev_chips = "".join(
        f'<span class="chip s-{s}">{int(inc.get(s) or 0)} {s}</span>'
        for s in ("critical", "warning", "info") if int(inc.get(s) or 0))
    by_type = "".join(f"<tr><td>{_esc(k.replace('_',' '))}</td><td class='num'>{v}</td></tr>"
                      for k, v in sorted((inc.get("by_type") or {}).items(), key=lambda kv: -kv[1]))
    honesty = "".join(f"<li>{_esc(h)}</li>" for h in (rollup.get("honesty") or []))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>WatchLog Monthly — {_esc(meta.get('site'))} — {_esc(meta.get('month'))}</title>"
        f"<style>{ipdf._CSS}"
        ".bars{display:flex;gap:3px;align-items:flex-end;height:64px;margin:6px 0}"
        ".bar{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end}"
        f".fill{{width:100%;background:{BLUE};border-radius:2px 2px 0 0}}"
        f".lbl{{font-size:8px;color:{MUTED};margin-top:2px}}"
        f".chip{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;margin-right:6px;text-transform:uppercase}}"
        ".chip.s-critical{background:#FEF3F2;color:#B42318}.chip.s-warning{background:#FFFAEB;color:#B54708}.chip.s-info{background:#EFF8FF;color:#175CD3}"
        "</style></head><body><div class=\"wrap\">"
        '<header class="brand"><div><div class="name">Watch<span>Log</span></div>'
        '<div class="by">Monthly Site Intelligence · by Vision Infinity</div></div>'
        f'<div class="meta"><b>{_esc(meta.get("site"))}</b>{_esc(meta.get("month"))} · {_esc(meta.get("timezone"))}</div></header>'
        f'<section><h2>Incidents</h2><div class="hero {"alert" if int(inc.get("total") or 0) else "quiet"}">'
        f'<div class="hero-n">{int(inc.get("total") or 0)}</div><div class="hero-t">incident(s) this month'
        f'<div class="chips">{sev_chips}</div></div></div>'
        + (f'<table class="grid"><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>{by_type}</tbody></table>' if by_type else "")
        + "</section>"
        f'<section><h2>Activity</h2><table class="kv">'
        f'<tr><td class="k">Total detections</td><td class="v">{int(act.get("total_detections") or 0)}</td></tr>'
        f'<tr><td class="k">After-hours detections</td><td class="v">{int(act.get("after_hours_detections") or 0)}</td></tr>'
        f'<tr><td class="k">Restricted-area windows</td><td class="v">{int(act.get("restricted_access_windows") or 0)}</td></tr>'
        f'<tr><td class="k">Busiest day</td><td class="v">{_esc(busiest.get("date"))} ({int(busiest.get("detections") or 0)})</td></tr>'
        f'<tr><td class="k">Active days</td><td class="v">{int(meta.get("days_with_activity") or 0)}</td></tr></table>'
        f'{_trend_bars(act.get("daily") or [])}</section>'
        f'<section><h2>Monitoring coverage</h2><div class="cov"><span class="cov-n">{_pct(cov.get("coverage_ratio"))}</span>'
        '<span class="cov-t">of the month verified</span></div></section>'
        f'<div class="honesty"><ul>{honesty}</ul></div>'
        f'<footer class="foot">WatchLog {_esc(rollup.get("schema"))} · detections are events observed on site, not a headcount.</footer>'
        "</div></body></html>"
    )


def render_monthly_pdf(rollup: dict):
    """Real PDF bytes when an engine is installed; else None (never a fake file)."""
    doc = render_monthly_html(rollup)
    try:
        from weasyprint import HTML   # noqa: PLC0415
        return HTML(string=doc).write_pdf()
    except Exception:                 # noqa: BLE001
        return None


def aggregate_org(site_rollups: list) -> dict:
    """Organizational aggregation contract: roll several sites' monthly rollups into one org view.

    Input: a list of wl_monthly_rollup JSON objects (one per site, same month). Output: org totals,
    a per-site breakdown, and the busiest site. Sites with mismatched months are reported, not
    silently merged.
    """
    rollups = [r for r in (site_rollups or []) if r]
    months = {(r.get("meta") or {}).get("month") for r in rollups}
    per_site, tot_inc, tot_crit, tot_det = [], 0, 0, 0
    for r in rollups:
        meta, inc, act = r.get("meta") or {}, r.get("incidents") or {}, r.get("activity") or {}
        row = {"site": meta.get("site"), "site_id": meta.get("site_id"),
               "incidents": int(inc.get("total") or 0), "critical": int(inc.get("critical") or 0),
               "detections": int(act.get("total_detections") or 0),
               "coverage_ratio": (r.get("coverage") or {}).get("coverage_ratio")}
        per_site.append(row)
        tot_inc += row["incidents"]; tot_crit += row["critical"]; tot_det += row["detections"]
    busiest = max(per_site, key=lambda s: s["detections"], default=None)
    return {
        "schema": "org_monthly_rollup.v1",
        "month": (list(months)[0] if len(months) == 1 else None),
        "month_mismatch": len(months) > 1,
        "sites": len(rollups),
        "totals": {"incidents": tot_inc, "critical": tot_crit, "detections": tot_det},
        "per_site": sorted(per_site, key=lambda s: -s["incidents"]),
        "busiest_site": (busiest or {}).get("site"),
    }


__all__ = ["render_monthly_html", "render_monthly_pdf", "aggregate_org"]
