#!/usr/bin/env python3
"""
WatchLog billing service - the provider boundary.

Two responsibilities, both server-side of the authorization line:

  * a hosted-checkout page per provider (the customer pays HERE, never in
    the portal), and
  * a signed webhook receiver that, after verifying the provider signature,
    calls wl_billing_apply_event() - the ONLY path to paid state. The
    customer cannot reach apply_event (it is granted to no client role); the
    customer cannot forge a webhook (they do not have BILLING_WEBHOOK_SECRET).

Providers:
  * mock   - a deterministic SANDBOX used for demo + E2E. Gated behind
             BILLING_ALLOW_MOCK (default OFF), so it can never activate a
             real tenant on a production deployment. Every mock page is
             loudly labelled TEST and every mock transaction is stored with
             provider='mock'.
  * switch - the real gateway. Its signature verification + event parsing
             need Switch's documented signing scheme + API contract, which
             is not available -> that adapter is CLIENT-BLOCKED and returns
             501 until the contract is provided.

Config from the environment (Coolify env; same names as .env):
  SUPABASE_DB_*          to call wl_billing_apply_event / read checkouts
  BILLING_WEBHOOK_SECRET HMAC secret for the mock webhook signature
  BILLING_ALLOW_MOCK     "true" to enable the sandbox provider (demo only)
  WATCHLOG_PORTAL_URL    where to send the browser after a sandbox payment
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

SECRET = os.environ.get("BILLING_WEBHOOK_SECRET", "")
ALLOW_MOCK = os.environ.get("BILLING_ALLOW_MOCK", "false").strip().lower() in ("1", "true", "yes")
PORTAL_URL = (os.environ.get("WATCHLOG_PORTAL_URL", "") or "").rstrip("/")
PORT = int(os.environ.get("PORT", "8640"))


def _dsn() -> str:
    g = lambda k: os.environ.get(k, "")
    return (f"postgresql://{g('SUPABASE_DB_USER')}:{g('SUPABASE_DB_PASSWORD')}"
            f"@{g('SUPABASE_DB_HOST')}:{g('SUPABASE_DB_PORT')}/{g('SUPABASE_DB_NAME')}")


def apply_event(provider: str, event_id: str, event_type: str, payload: dict) -> dict:
    with psycopg.connect(_dsn(), sslmode="require", connect_timeout=20, autocommit=True) as c:
        row = c.execute("select wl_billing_apply_event(%s,%s,%s,%s::jsonb)",
                        (provider, event_id, event_type, json.dumps(payload))).fetchone()
        return row[0]


def checkout(pcid: str):
    with psycopg.connect(_dsn(), sslmode="require", connect_timeout=20, autocommit=True) as c:
        return c.execute(
            "select provider, plan, amount_minor, currency, status "
            "from billing_checkouts where provider_checkout_id=%s", (pcid,)).fetchone()


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, "application/json", json.dumps(obj))

    # -- GET -----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/health":
            self._json(200, {"service": "watchlog-billing", "ok": True,
                             "mock_enabled": ALLOW_MOCK})
            return
        if path.startswith("/pay/mock/"):
            if not ALLOW_MOCK:
                self._json(404, {"error": "mock provider disabled"}); return
            pcid = path[len("/pay/mock/"):]
            row = checkout(pcid)
            if not row:
                self._send(404, "text/html", "<h1>Unknown checkout</h1>"); return
            _prov, plan, amt, cur, status = row
            amount = f"{cur} {amt/100:,.0f}"
            done = status == "completed"
            self._send(200, "text/html", self._pay_page(pcid, plan, amount, done))
            return
        self._json(404, {"error": "not found"})

    def _pay_page(self, pcid, plan, amount, done):
        e = html.escape
        body = "" if not done else (
            '<p style="color:#0F7A3D;font-weight:700">This checkout is already completed.</p>')
        action = "" if done else (
            f'<form method="POST" action="/pay/mock/{e(pcid)}/complete">'
            f'<button style="background:#5B21FF;color:#fff;border:0;border-radius:10px;'
            f'padding:14px 22px;font-size:16px;font-weight:600;cursor:pointer">'
            f'Pay {e(amount)} (test)</button></form>')
        return (
            f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>WatchLog sandbox checkout</title></head>'
            f'<body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
            f'max-width:460px;margin:40px auto;padding:0 16px">'
            f'<div style="background:#FDF6EC;border:1px solid #F0E2CC;color:#8F5300;'
            f'border-radius:10px;padding:10px 14px;font-weight:700;margin-bottom:18px">'
            f'SANDBOX - TEST PAYMENT. No money moves. Not a production charge.</div>'
            f'<h1 style="margin:0 0 4px">WatchLog</h1>'
            f'<p style="color:#6B7280;margin:0 0 20px">Subscription checkout (sandbox)</p>'
            f'<div style="border:1px solid #E7E7EE;border-radius:12px;padding:18px;margin-bottom:20px">'
            f'<div style="color:#6B7280;font-size:13px">Plan</div>'
            f'<div style="font-size:20px;font-weight:700;text-transform:capitalize">{e(plan)}</div>'
            f'<div style="color:#6B7280;font-size:13px;margin-top:10px">Amount</div>'
            f'<div style="font-size:20px;font-weight:700">{e(amount)} / month</div></div>'
            f'{body}{action}'
            f'<p style="color:#6B7280;font-size:12px;margin-top:24px">This sandbox stands in for '
            f'the Switch payment gateway during demos and testing. The real gateway replaces this '
            f'page unchanged in the flow.</p></body></html>')

    # -- POST ----------------------------------------------------------
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""

        # 1) Sandbox "pay" completion: the service acts as the provider - it
        #    signs a payment.succeeded event and delivers it to its own webhook.
        if path.startswith("/pay/mock/") and path.endswith("/complete"):
            if not ALLOW_MOCK:
                self._json(403, {"error": "mock provider disabled"}); return
            pcid = path[len("/pay/mock/"):-len("/complete")]
            row = checkout(pcid)
            if not row:
                self._json(404, {"error": "unknown checkout"}); return
            event = {"event_id": "evt_" + uuid.uuid4().hex,
                     "event_type": "checkout.completed",
                     "provider_checkout_id": pcid,
                     "txn_id": "txn_" + uuid.uuid4().hex}
            body = json.dumps(event).encode()
            res = self._handle_webhook("mock", body, sign(body))
            # send the browser back to the portal
            if PORTAL_URL:
                self.send_response(303)
                self.send_header("Location", f"{PORTAL_URL}/settings/")
                self.end_headers()
            else:
                self._json(200, {"paid": res.get("ok"), "result": res})
            return

        # 2) Provider webhooks.
        if path == "/billing/webhook/mock":
            if not ALLOW_MOCK:
                self._json(404, {"error": "mock provider disabled"}); return
            sig = self.headers.get("X-Webhook-Signature", "")
            if not SECRET or not hmac.compare_digest(sig, sign(raw)):
                self._json(401, {"error": "bad signature"}); return
            self._json(200, self._handle_webhook("mock", raw, sig))
            return

        if path == "/billing/webhook/switch":
            # Switch's real signing scheme + event schema are not documented
            # here. The surrounding billing product is complete; only this
            # adapter is CLIENT-BLOCKED.
            self._json(501, {"error": "switch adapter not configured",
                             "client_blocked": "needs Switch signing scheme + API contract"})
            return

        self._json(404, {"error": "not found"})

    def _handle_webhook(self, provider, raw, sig):
        try:
            ev = json.loads(raw or b"{}")
        except Exception:
            return {"ok": False, "error": "bad json"}
        return apply_event(provider, ev.get("event_id") or ("evt_" + uuid.uuid4().hex),
                           ev.get("event_type", ""), ev)

    def log_message(self, *a):
        pass


def main():
    print(f"watchlog-billing on :{PORT} (mock={'ON' if ALLOW_MOCK else 'off'}; "
          f"secret={'set' if SECRET else 'MISSING'})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
