#!/usr/bin/env python3
"""Daily-intelligence delivery pipeline (item 8).

Connects the ONE canonical dataset (wl_daily_intelligence, 0074) to the report-delivery model
(report_recipients + report_deliveries, 0011) with the full production semantics: entitlement
gate, per-recipient idempotency (duplicate suppression via the sent-only unique index), status
recording (sent / failed / skipped), and retry (a failed send is re-attempted; a sent one is
never re-sent).

The transport is injected, so the whole pipeline is testable with a mock — the real runner
passes the Evolution WhatsApp transport. The DB handle is a psycopg cursor; the production
runner (reporter/daily_report.py) uses the same SQL over its Supabase connection.

Delivery to a real client stays gated on a configured recipient — this module never invents a
destination. Engineering-complete here means: given a recipient, the pipeline delivers, records,
dedups, and retries correctly. CLIENT RECIPIENT is a separate, per-site input.
"""
from __future__ import annotations

try:
    from intelligence_whatsapp import render_whatsapp
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from intelligence_whatsapp import render_whatsapp


class MockTransport:
    """Deterministic transport for tests. `fail_dests` always fail; everything else succeeds."""
    name = "whatsapp"

    def __init__(self, fail_dests=None):
        self.sent = []
        self.fail = set(fail_dests or [])

    def send(self, destination, text):
        if destination in self.fail:
            return (False, None, "mock transport failure")
        pid = f"mock-{len(self.sent) + 1}"
        self.sent.append((destination, text, pid))
        return (True, pid, None)


def _events_count(intel: dict) -> int:
    return int((((intel or {}).get("office") or {}).get("coverage") or {}).get("person_events") or 0)


def deliver_site_day(cur, site_id, tenant_id, report_date, *, transport, dry_run=False):
    """Render the canonical daily-intelligence WhatsApp message for one site/day and deliver it
    to every enabled WhatsApp recipient, idempotently. Returns a summary dict."""
    intel = cur.execute("select wl_daily_intelligence(%s, %s::date, true)", (site_id, report_date)).fetchone()[0]
    text = render_whatsapp(intel)
    events = _events_count(intel)
    enabled = cur.execute("select wl_reporting_enabled(%s)", (tenant_id,)).fetchone()[0]
    recipients = cur.execute(
        """select channel, destination, name from report_recipients
            where tenant_id = %s and enabled and (site_id is null or site_id = %s)
              and channel in ('whatsapp', 'both')""", (tenant_id, site_id)).fetchall()

    res = {"sent": 0, "skipped": 0, "failed": 0, "message": text, "outcomes": []}

    if not enabled:
        for _ch, dest, _who in recipients:
            cur.execute(
                """insert into report_deliveries (tenant_id, site_id, report_date, channel,
                       destination, status, error, events)
                   values (%s,%s,%s::date,'whatsapp',%s,'skipped','reporting disabled (trial/subscription)',%s)
                   on conflict do nothing""",
                (tenant_id, site_id, report_date, dest, events))
            res["skipped"] += 1
            res["outcomes"].append((dest, "skipped_disabled"))
        return res

    for _ch, dest, _who in recipients:
        already = cur.execute(
            """select 1 from report_deliveries where site_id = %s and report_date = %s::date
                and channel = 'whatsapp' and destination = %s and status = 'sent'""",
            (site_id, report_date, dest)).fetchone()
        if already:
            res["skipped"] += 1
            res["outcomes"].append((dest, "already_sent"))       # duplicate suppression
            continue
        if dry_run:
            res["skipped"] += 1
            res["outcomes"].append((dest, "dry_run"))
            continue
        ok, provider_id, err = transport.send(dest, text)
        cur.execute(
            """insert into report_deliveries (tenant_id, site_id, report_date, channel,
                   destination, status, provider_id, error, events)
               values (%s,%s,%s::date,'whatsapp',%s,%s,%s,%s,%s) on conflict do nothing""",
            (tenant_id, site_id, report_date, dest, "sent" if ok else "failed", provider_id, err, events))
        if ok:
            res["sent"] += 1
            res["outcomes"].append((dest, "sent"))
        else:
            res["failed"] += 1
            res["outcomes"].append((dest, "failed"))
    return res


__all__ = ["deliver_site_day", "MockTransport"]
