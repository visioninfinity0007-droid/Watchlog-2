#!/usr/bin/env python3
"""Canonical report unification (item 1) + headline metrics.

One frozen snapshot payload (wl_generate_daily_report, 0083) is the single source. Every
surface — portal read, WhatsApp, PDF — renders from THIS payload and takes its headline numbers
from headline_metrics(), so no renderer independently recomputes a metric. That is what makes
the three surfaces agree by construction.

render_report(payload) returns the WhatsApp text, the browser-print HTML, the PDF-optimized HTML,
real PDF bytes (when an engine is packaged), and the headline metrics — all from one payload.
"""
from __future__ import annotations

try:
    from report_metrics import headline_metrics
    import intelligence_whatsapp as iw
    import intelligence_pdf as ipdf
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from report_metrics import headline_metrics
    import intelligence_whatsapp as iw
    import intelligence_pdf as ipdf


def render_report(payload: dict) -> dict:
    """Render every surface from one payload. `pdf_bytes` is real when an engine is packaged."""
    h = headline_metrics(payload)
    whatsapp = iw.render_whatsapp(payload)
    html = ipdf.render_html(payload)
    pdf_html = ipdf.render_pdf_html(payload)
    pdf_bytes = ipdf.render_pdf(payload)
    return {"headline": h, "whatsapp": whatsapp, "html": html, "pdf_html": pdf_html, "pdf_bytes": pdf_bytes}


__all__ = ["headline_metrics", "render_report"]
