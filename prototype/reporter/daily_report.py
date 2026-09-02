#!/usr/bin/env python3
"""
The daily summary — the thing customers actually receive.

Milestone 1 calls for a "daily WhatsApp event summary". This builds it,
sends it, and records that it was sent. Analytics Studio extends that same
canonical report with Site Intelligence when measurements exist; there is no
separate legacy/manual report shape.

    python reporter/daily_report.py                 # dry run: render only
    python reporter/daily_report.py --send          # actually deliver
    python reporter/daily_report.py --date 2026-08-28 --site <uuid>

DRY RUN IS THE DEFAULT, on purpose. This program's whole job is to
message real people on a schedule. A default that sends means every
accidental invocation - a test, a tab-complete, a cron entry pasted twice
- reaches a customer's phone. Sending has to be the deliberate choice.

Delivery rules
--------------
IDEMPOTENT. Every send is recorded in report_deliveries, which carries a
unique index on (site, date, channel, destination) for status='sent'. A
second run of the same day is a no-op. This matters more than it sounds:
schedules get retried by cron overlap, container restarts, and people
running it by hand to see if it works. Three copies of the same summary
and the customer stops reading all of them.

Failures are recorded too, but are NOT covered by the unique index, so a
retry can genuinely retry.

Per-site timezone. "Yesterday" is yesterday where the cameras are, not
where the server is. A site in Karachi and a server in UTC disagree for
five hours every night, which is exactly the window most incidents fall
in.

Voice
-----
Per BRAND_GUIDELINES: plain, specific, unexcited. Numbers over
adjectives. No exclamation marks, ever. A quiet night is reported as a
quiet night rather than dressed up - "nothing happened" is useful
information and the most common thing this will ever say.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# Project root (to find a local .env when run from source). In the deployed
# report-runner the file lives flat at /app, so parents[2] does not exist —
# fall back to the file's own dir; config there comes from the environment.
try:
    ROOT = Path(__file__).resolve().parents[2]
except IndexError:
    ROOT = Path(__file__).resolve().parent
TIMEOUT = 30

# The branded HTML body and Analytics Studio report extension live next to this
# file. Keep their base/wrapper distinction explicit so every entrypoint gets
# the same canonical composition exactly once.
try:
    from email_template import render_html as _render_base_html, subject as html_subject
    import analytics_reporting
except ImportError:  # running from a different cwd
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from email_template import render_html as _render_base_html, subject as html_subject
    import analytics_reporting


# ---------------------------------------------------------------------
# config
# ---------------------------------------------------------------------

def load_env() -> dict:
    # WatchLog-owned config only. The reporter no longer borrows another
    # project's .env — Evolution/SendGrid/portal config lives in this
    # project's own .env (see .env.example), or in the process environment
    # (how the deployed report-runner service is configured on Coolify).
    env: dict = {}
    f = ROOT / ".env"
    if f.exists():
        for k, v in re.findall(r"^([A-Za-z0-9_]+)\s*=\s*(.*)$",
                               f.read_text(errors="replace"), re.M):
            env.setdefault(k, v.strip().strip('"').strip("'"))
    env.update({k: v for k, v in os.environ.items()
                if k.startswith(("SUPABASE_", "EVOLUTION_", "SENDGRID_", "WATCHLOG_",
                                 "REPORT_"))})
    return env


ENV = load_env()


def dsn() -> str:
    g = lambda k: ENV.get(k, "")
    return (f"postgresql://{g('SUPABASE_DB_USER')}:{g('SUPABASE_DB_PASSWORD')}"
            f"@{g('SUPABASE_DB_HOST')}:{g('SUPABASE_DB_PORT')}/{g('SUPABASE_DB_NAME')}")


# ---------------------------------------------------------------------
# message
# ---------------------------------------------------------------------

DAY = "%A %-d %B" if os.name != "nt" else "%A %d %B"

FRIENDLY = {
    "motion": "motion", "person": "person", "vehicle": "vehicle",
    "intrusion": "intrusion", "line_crossing": "line crossing",
    "tamper": "tamper", "video_loss": "video loss",
    "disk_error": "disk error", "offline": "camera offline",
}

# Faults mean equipment, not intruders. They are called out separately
# because they need a different action from the reader: someone has to go
# and look at a box, not review footage.
FAULTS = {"tamper", "video_loss", "disk_error", "offline"}


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


def _compose_security(report: dict) -> str:
    """One site's security-event chapter, before optional Site Intelligence."""
    site = report.get("site") or "Site"
    total = int(report.get("total_events") or 0)
    when = report.get("date")
    try:
        pretty = datetime.strptime(str(when), "%Y-%m-%d").strftime(DAY).lstrip("0")
    except Exception:                                  # noqa: BLE001
        pretty = str(when)

    lines = [f"*WatchLog* — {site}", pretty, ""]

    if total == 0:
        lines.append("Nothing to report. No events.")
        return "\n".join(lines)

    after = int(report.get("after_hours_events") or 0)
    head = plural(total, "event")
    if after:
        head += f", {after} after hours"
    lines.append(head + ".")

    by_cam = report.get("by_camera") or []
    if by_cam:
        top = by_cam[0]
        lines.append(f"Busiest camera: {top.get('camera')} ({top.get('count')}).")
        if len(by_cam) > 1:
            rest = ", ".join(f"{c.get('camera')} {c.get('count')}"
                             for c in by_cam[1:5])
            lines.append(f"Also: {rest}.")

    by_type = report.get("by_type") or {}
    incidents = {k: v for k, v in by_type.items() if k not in FAULTS}
    if incidents:
        parts = ", ".join(f"{FRIENDLY.get(k, k)} {v}"
                          for k, v in sorted(incidents.items(),
                                             key=lambda kv: -kv[1]))
        lines.append("")
        lines.append(f"By type: {parts}.")

    faults = report.get("faults") or {}
    if faults:
        parts = ", ".join(f"{FRIENDLY.get(k, k)} {v}" for k, v in faults.items())
        lines.append("")
        lines.append(f"Needs attention: {parts}.")

    first, last = report.get("first_event_at"), report.get("last_event_at")
    if first and last:
        tz = report.get("timezone") or "UTC"
        lines.append("")
        lines.append(f"First {_hhmm(first, tz)}, last {_hhmm(last, tz)}.")

    return "\n".join(lines)


def compose(report: dict) -> str:
    """Canonical plain-text report, including Site Intelligence when present."""
    return analytics_reporting.compose(_compose_security, report)


def render_html(report: dict, portal_url: str = "#") -> str:
    """Canonical HTML report, including Site Intelligence when present."""
    return analytics_reporting.render_html(_render_base_html, report, portal_url)


def _hhmm(ts: str, tz: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(tz)).strftime("%H:%M")
    except Exception:                                  # noqa: BLE001
        return str(ts)[11:16]


# ---------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------

class WhatsApp:
    """Evolution API. The number must be digits only, no '+', no spaces."""

    name = "whatsapp"

    def __init__(self):
        self.url = (ENV.get("EVOLUTION_API_URL") or "").rstrip("/")
        self.key = ENV.get("EVOLUTION_API_KEY") or ""
        self.instance = (ENV.get("WATCHLOG_WHATSAPP_INSTANCE")
                         or ENV.get("EVOLUTION_INSTANCE_NAME") or "")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key and self.instance)

    def why_not(self) -> str:
        missing = [n for n, v in (("EVOLUTION_API_URL", self.url),
                                  ("EVOLUTION_API_KEY", self.key),
                                  ("instance name", self.instance)) if not v]
        return "not configured: " + ", ".join(missing)

    def check(self) -> tuple:
        """Is the instance actually connected to WhatsApp?"""
        try:
            r = requests.get(f"{self.url}/instance/fetchInstances",
                             headers={"apikey": self.key}, timeout=TIMEOUT)
            data = r.json()
        except Exception as e:                         # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e)[:100]}"
        items = data if isinstance(data, list) else (data.get("instances") or [])
        for it in items:
            inst = it.get("instance", it) if isinstance(it, dict) else {}
            name = inst.get("instanceName") or inst.get("name") or it.get("name")
            state = (inst.get("state") or inst.get("connectionStatus")
                     or it.get("connectionStatus"))
            if name == self.instance:
                return state == "open", f"instance '{name}' is '{state}'"
        return False, f"instance '{self.instance}' not found on this server"

    def send(self, destination: str, text: str, **_kw) -> tuple:
        number = re.sub(r"[^0-9]", "", destination)
        try:
            r = requests.post(
                f"{self.url}/message/sendText/{self.instance}",
                headers={"apikey": self.key, "Content-Type": "application/json"},
                data=json.dumps({"number": number, "text": text}),
                timeout=TIMEOUT)
        except Exception as e:                         # noqa: BLE001
            return False, None, f"{type(e).__name__}: {str(e)[:160]}"
        if r.status_code not in (200, 201):
            return False, None, f"HTTP {r.status_code}: {r.text[:160]}"
        try:
            mid = (r.json().get("key") or {}).get("id")
        except Exception:                              # noqa: BLE001
            mid = None
        return True, mid, None


class Email:
    """
    SendGrid. Milestone 3.

    Deliberately not stubbed into pretending it works: with no API key it
    reports itself unconfigured and the run records 'skipped' rather than
    'sent', so nobody later believes emails went out that never did.
    """

    name = "email"

    def __init__(self):
        self.key = ENV.get("SENDGRID_API_KEY") or ""
        self.sender = ENV.get("SENDGRID_FROM") or "reports@watchlog.pk"

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def why_not(self) -> str:
        return "not configured: SENDGRID_API_KEY is not set"

    def check(self) -> tuple:
        return self.configured, ("ready" if self.configured else self.why_not())

    def send(self, destination: str, text: str, **kw) -> tuple:
        # Branded HTML is the point of this channel (M3); the plain-text body
        # is sent alongside as the fallback part. SendGrid requires the
        # text/plain part to come before text/html.
        subject = kw.get("subject") or text.splitlines()[0].replace("*", "")
        html = kw.get("html")
        content = [{"type": "text/plain", "value": text.replace("*", "")}]
        if html:
            content.append({"type": "text/html", "value": html})
        body = {"personalizations": [{"to": [{"email": destination}]}],
                "from": {"email": self.sender, "name": "WatchLog"},
                "subject": subject,
                "content": content}
        try:
            r = requests.post("https://api.sendgrid.com/v3/mail/send",
                              headers={"Authorization": f"Bearer {self.key}",
                                       "Content-Type": "application/json"},
                              data=json.dumps(body), timeout=TIMEOUT)
        except Exception as e:                         # noqa: BLE001
            return False, None, f"{type(e).__name__}: {str(e)[:160]}"
        if r.status_code not in (200, 202):
            return False, None, f"HTTP {r.status_code}: {r.text[:160]}"
        return True, r.headers.get("X-Message-Id"), None


# ---------------------------------------------------------------------
# run
# ---------------------------------------------------------------------

def run(send: bool, only_site: str | None, on: str | None) -> int:
    try:
        import psycopg
    except ImportError:
        print("FATAL: pip install psycopg[binary]")
        return 2

    channels = {c.name: c for c in (WhatsApp(), Email())}
    for c in channels.values():
        ok, why = c.check() if c.configured else (False, c.why_not())
        print(f"  {c.name:9} {'ready' if ok else 'UNAVAILABLE'} — {why}")
    print()

    sent = skipped = failed = 0
    with psycopg.connect(dsn(), connect_timeout=30, sslmode="require") as conn:
        sites = conn.execute(
            "select id, tenant_id, name, timezone from sites"
            + (" where id = %s" if only_site else "") + " order by name",
            (only_site,) if only_site else ()).fetchall()

        for site_id, tenant_id, name, tz in sites:
            day = (on or conn.execute(
                "select ((now() at time zone %s)::date - 1)::text", (tz,)
            ).fetchone()[0])

            report = conn.execute("select wl_daily_report(%s, %s::date)",
                                  (site_id, day)).fetchone()[0]
            text = compose(report)
            # Config-driven; no hardcoded host. The deployed runner sets
            # WATCHLOG_PORTAL_URL; if unset the email simply omits the link.
            portal_url = ENV.get("WATCHLOG_PORTAL_URL") or ""
            html = render_html(report, portal_url or "#")
            subj = html_subject(report)
            total = int(report.get("total_events") or 0)

            # 0031 canonicalises recipients to one row per delivery endpoint.
            # There is therefore no transport ambiguity: every channel is paired
            # with the destination that belongs to that provider.
            people = conn.execute(
                """select id, channel, destination, name
                     from report_recipients
                    where tenant_id = %s and enabled
                      and (site_id is null or site_id = %s)""",
                (tenant_id, site_id)).fetchall()

            # Entitlement gate (central rule; same one the portal shows). The
            # marketing site promises reporting stops when the trial ends —
            # this enforces it. Events/data are untouched; only delivery stops.
            enabled = conn.execute("select wl_reporting_enabled(%s)",
                                   (tenant_id,)).fetchone()[0]

            print(f"  === {name} — {day} — {total} events, {len(people)} recipient(s)"
                  + ("" if enabled else "  [REPORTING DISABLED — trial/subscription]") + " ===")
            print("  " + text.replace("\n", "\n  "))
            print()

            if not enabled:
                print("    reporting disabled for this tenant (trial expired or not "
                      "subscribed) — not delivered\n")
                skipped += len(people)
                continue

            if not people:
                print("    no recipients configured for this site\n")
                continue

            for _rid, channel, dest, who in people:
                # Current canonical rows are whatsapp OR email. Retain `both`
                # defensively for a pre-0031 database, but release ordering
                # requires 0031 before this reporter is deployed.
                wanted = ("whatsapp", "email") if channel == "both" else (channel,)
                for ch_name in wanted:
                    ch = channels[ch_name]
                    label = f"{ch_name} -> {dest}" + (f" ({who})" if who else "")

                    already = conn.execute(
                        """select 1 from report_deliveries
                            where site_id = %s and report_date = %s::date
                              and channel = %s and destination = %s
                              and status = 'sent'""",
                        (site_id, day, ch_name, dest)).fetchone()
                    if already:
                        print(f"    {label}: already sent, skipping")
                        skipped += 1
                        continue

                    if not send:
                        print(f"    {label}: DRY RUN, not sent")
                        skipped += 1
                        continue

                    if not ch.configured:
                        conn.execute(
                            """insert into report_deliveries
                               (tenant_id, site_id, report_date, channel,
                                destination, status, error, events)
                               values (%s,%s,%s::date,%s,%s,'skipped',%s,%s)""",
                            (tenant_id, site_id, day, ch_name, dest,
                             ch.why_not(), total))
                        conn.commit()
                        print(f"    {label}: SKIPPED — {ch.why_not()}")
                        skipped += 1
                        continue

                    ok, provider_id, err = ch.send(dest, text, html=html, subject=subj)
                    conn.execute(
                        """insert into report_deliveries
                           (tenant_id, site_id, report_date, channel,
                            destination, status, provider_id, error, events)
                           values (%s,%s,%s::date,%s,%s,%s,%s,%s,%s)
                           on conflict do nothing""",
                        (tenant_id, site_id, day, ch_name, dest,
                         "sent" if ok else "failed", provider_id, err, total))
                    conn.commit()
                    if ok:
                        print(f"    {label}: sent ({provider_id})")
                        sent += 1
                    else:
                        print(f"    {label}: FAILED — {err}")
                        failed += 1
            print()

    print(f"  {sent} sent, {skipped} skipped, {failed} failed")
    if not send:
        print("  (dry run — nothing was delivered. Add --send to deliver.)")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true",
                    help="actually deliver. Without this it only renders.")
    ap.add_argument("--site", help="one site uuid instead of all")
    ap.add_argument("--date", help="YYYY-MM-DD, default is yesterday per site")
    a = ap.parse_args()
    return run(a.send, a.site, a.date)


if __name__ == "__main__":
    sys.exit(main())
