#!/usr/bin/env python3
"""
WatchLog report-runner — the scheduled entry point for the daily report.

Architecture (matches the SOW's "daily WhatsApp via n8n"):

    n8n Schedule Trigger  ->  POST /run/<token>  ->  this service  ->  daily_report.run()

One HTTP surface, token-gated, so the report has a single production trigger
that n8n (or cron, or a human) can call. The report LOGIC lives in
daily_report.py — this only schedules and reports the outcome, so there is
no second copy of the reporting rules to drift.

Config comes entirely from the process environment (set as Coolify env on
the deployed service — the same names as .env):
    SUPABASE_DB_*         to read the report + write delivery records
    EVOLUTION_API_* / WATCHLOG_WHATSAPP_INSTANCE   WhatsApp channel
    SENDGRID_API_KEY / SENDGRID_FROM               email channel
    WATCHLOG_PORTAL_URL                            links in the email
    REPORT_RUNNER_TOKEN   the shared secret in the /run/<token> path
    REPORT_SEND           "true" to deliver; anything else = dry-run (default)

DRY-RUN IS THE DEFAULT. The deployed service composes every site's report,
resolves recipients, and records what it WOULD send, without messaging
anyone, until REPORT_SEND=true is set deliberately (the CLIENT-BLOCKED
step: a real external send needs the customer's go-ahead).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report  # noqa: E402

TOKEN = os.environ.get("REPORT_RUNNER_TOKEN", "")
SEND = os.environ.get("REPORT_SEND", "false").strip().lower() in ("1", "true", "yes")
PORT = int(os.environ.get("PORT", "8630"))


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # health
        self._json(200, {"service": "watchlog-report-runner", "ok": True,
                         "mode": "send" if SEND else "dry-run"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        token = path[len("/run/"):] if path.startswith("/run/") else None
        if not TOKEN or token != TOKEN:
            self._json(403, {"ok": False, "error": "forbidden"})
            return
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = daily_report.run(SEND, None, None)
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"})
            return
        log = buf.getvalue()
        summary = [ln.strip() for ln in log.splitlines()
                   if "sent," in ln and "skipped" in ln]
        self._json(200, {
            "ok": rc == 0,
            "mode": "send" if SEND else "dry-run",
            "summary": summary[-1] if summary else "",
            "log_tail": log[-4000:],
        })

    def log_message(self, *a):  # quiet
        pass


def main() -> None:
    print(f"watchlog-report-runner on :{PORT} "
          f"(mode={'send' if SEND else 'DRY-RUN'}; token={'set' if TOKEN else 'MISSING'})",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
