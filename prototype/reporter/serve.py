#!/usr/bin/env python3
"""WatchLog report-runner, the scheduled entry point for the daily report.

Architecture:

    n8n Schedule Trigger -> POST /run/<token> -> this service -> daily_report.run()

Analytics Studio extends the existing report payload. The core reporter remains
unchanged and is wrapped here so scheduled production delivery gains the Site
Intelligence section without creating a second delivery implementation.
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
import analytics_reporting  # noqa: E402

# Preserve the proven original renderers, then layer analytics on top.
_BASE_COMPOSE = daily_report.compose
_BASE_RENDER_HTML = daily_report.render_html
daily_report.compose = lambda report: analytics_reporting.compose(_BASE_COMPOSE, report)
daily_report.render_html = lambda report, portal_url="#": analytics_reporting.render_html(
    _BASE_RENDER_HTML, report, portal_url)

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

    def do_GET(self):
        self._json(200, {"service": "watchlog-report-runner", "ok": True,
                         "analytics": True,
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
            "analytics": True,
            "summary": summary[-1] if summary else "",
            "log_tail": log[-4000:],
        })

    def log_message(self, *a):
        pass


def main() -> None:
    print(f"watchlog-report-runner on :{PORT} "
          f"(analytics=on; mode={'send' if SEND else 'DRY-RUN'}; token={'set' if TOKEN else 'MISSING'})",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
