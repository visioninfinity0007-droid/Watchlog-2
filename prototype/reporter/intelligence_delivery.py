#!/usr/bin/env python3
"""Daily-intelligence delivery pipeline (items 1, 2, 8).

The default M1 path: wl_generate_daily_report (frozen snapshot, 0083) -> render WhatsApp + PDF
from THAT payload -> durable outbox (0084) with a provider idempotency key -> transport -> mark.

- One frozen snapshot per site/day; the PDF is generated and its reference saved to the snapshot.
- Delivery is durable + at-least-once: enqueue is idempotent, a crashed 'sending' attempt is
  reclaimed with the SAME idempotency key so a key-honoring provider dedups (effective-once).
- The transport is injected (mock in tests; Evolution WhatsApp in production).

Client delivery stays gated on a configured recipient — the pipeline never invents a destination.
"""
from __future__ import annotations

import hashlib

try:
    from intelligence_whatsapp import render_whatsapp
    import intelligence_pdf as ipdf
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from intelligence_whatsapp import render_whatsapp
    import intelligence_pdf as ipdf


class MockTransport:
    """Deterministic transport that HONORS the idempotency key (a key-honoring provider): the
    same key never sends twice, so a crashed-then-reclaimed attempt is effective-once."""
    name = "whatsapp"

    def __init__(self, fail_dests=None):
        self.sent = []
        self.fail = set(fail_dests or [])
        self._by_key = {}

    def send(self, destination, text, idempotency_key):
        if idempotency_key in self._by_key:
            return (True, self._by_key[idempotency_key], None)     # provider dedup
        if destination in self.fail:
            return (False, None, "mock transport failure")
        pid = f"mock-{len(self.sent) + 1}"
        self.sent.append((destination, text, pid, idempotency_key))
        self._by_key[idempotency_key] = pid
        return (True, pid, None)


def enqueue_site_day(cur, site_id, tenant_id, report_date):
    """Generate/fetch the frozen snapshot, render+save the PDF, and enqueue each enabled
    WhatsApp recipient into the durable outbox. Idempotent (re-enqueue is a no-op)."""
    gen = cur.execute("select wl_generate_daily_report(%s, %s::date)", (site_id, report_date)).fetchone()[0]
    rid, payload = gen["report_id"], gen["payload"]

    pdf = ipdf.render_pdf(payload)
    if pdf:
        cur.execute("select wl_set_report_pdf(%s, %s, %s)", (rid, hashlib.sha256(pdf).hexdigest(), len(pdf)))

    enabled = cur.execute("select wl_reporting_enabled(%s)", (tenant_id,)).fetchone()[0]
    if not enabled:
        cur.execute("select wl_set_report_delivery_status(%s, 'skipped')", (rid,))
        return {"report_id": rid, "enqueued": 0, "skipped": True, "pdf_saved": bool(pdf)}

    recips = cur.execute(
        """select destination from report_recipients where tenant_id = %s and enabled
            and (site_id is null or site_id = %s) and channel in ('whatsapp','both')""",
        (tenant_id, site_id)).fetchall()
    for (dest,) in recips:
        cur.execute("select wl_outbox_enqueue(%s, 'whatsapp', %s)", (rid, dest))
    return {"report_id": rid, "enqueued": len(recips), "skipped": False, "pdf_saved": bool(pdf)}


def drain_outbox(cur, channel, transport, limit=100, stale_seconds=300):
    """Claim due deliveries (incl. reclaimed crash-edge rows), render each from its FROZEN
    snapshot, send with the idempotency key, and record the outcome."""
    claimed = cur.execute("select wl_outbox_claim(%s, %s, %s)", (channel, limit, stale_seconds)).fetchone()[0]
    res = {"attempted": len(claimed), "sent": 0, "failed": 0}
    for row in claimed:
        snap = cur.execute("select wl_get_report_snapshot(%s)", (row["report_id"],)).fetchone()[0]
        text = render_whatsapp((snap or {}).get("payload") or {})
        ok, provider_id, err = transport.send(row["destination"], text, row["idempotency_key"])
        cur.execute("select wl_outbox_mark(%s, %s, %s, %s)", (row["id"], ok, provider_id, err))
        cur.execute("select wl_set_report_delivery_status(%s, %s)", (row["report_id"], "delivered" if ok else "failed"))
        res["sent" if ok else "failed"] += 1
    return res


def deliver_site_day(cur, site_id, tenant_id, report_date, *, transport, dry_run=False):
    """Convenience: enqueue this site's report then drain the WhatsApp outbox."""
    enq = enqueue_site_day(cur, site_id, tenant_id, report_date)
    if dry_run or enq["skipped"]:
        return {**enq, "attempted": 0, "sent": 0, "failed": 0}
    return {**enq, **drain_outbox(cur, "whatsapp", transport)}


__all__ = ["deliver_site_day", "enqueue_site_day", "drain_outbox", "MockTransport"]
