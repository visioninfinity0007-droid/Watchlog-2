# Runbook — Webhook replay

WatchLog has two inbound POST surfaces. Both are designed so a replay is **safe** (idempotent) —
re-processing the same event produces the same end state, never a duplicate.

---

## 1. Recorder-push ingest (live today)

The push bridge receives a recorder's native alarm POST and turns it into events.

- **Endpoint:** `POST https://watchlog-push.<domain>/push/<token>` (token from
  `wl_issue_push_token`, stored in `push_sources`).
- **Idempotency:** events land through `wl_ingest_push`, which computes the same `dedupe_key` as the
  agent path; the unique index `events (tenant_id, dedupe_key)` collapses duplicates. Re-POSTing an
  identical alarm is a no-op.
- **Replay a captured alarm** (e.g. to reproduce a parsing issue or backfill a test):
  ```bash
  curl -sS -X POST "https://watchlog-push.<domain>/push/<token>" \
       -H 'Content-Type: application/xml' --data-binary @captured_alarm.xml
  ```
  Confirm it landed:
  ```sql
  select event_type, device_ts, received_at from events
   where site_id = (select site_id from push_sources where token = '<token>')
   order by received_at desc limit 10;
  ```
- **Note:** recorders do not store-and-forward, so there is no way to *recover* alarms that fired
  while the bridge/agent was down — replay only re-sends payloads you captured. See
  `NVR_TROUBLESHOOTING.md` for outage handling.

## 2. Billing webhook (Switch)  — lands with P8

The Switch payment webhook is the **only** authoritative writer of paid state (customers cannot mark
themselves paid; `wl_billing_set_subscription` is granted to no client role — enforced by
`test_billing_authz`).

Design (implemented in P8):
- **Endpoint:** `POST https://.../billing/switch/webhook`.
- **Signature:** every request is verified against the Switch signing secret before any DB write; an
  invalid signature is rejected with 401 and nothing changes.
- **Ledger:** each event is stored in `billing_webhook_events` keyed by the provider's event id, with
  a processed flag. Processing is wrapped so the same event id is applied **once**.
- **Replay procedure:**
  1. Find the event: `select id, provider_event_id, type, processed_at, payload
     from billing_webhook_events where provider_event_id = '<id>';`
  2. To re-drive it, either re-send from the Switch dashboard, or re-invoke the handler with the
     stored `payload` (the signature check + idempotency key make this safe):
     ```bash
     curl -sS -X POST https://.../billing/switch/webhook \
          -H 'X-Switch-Signature: <sig>' --data-binary @payload.json
     ```
  3. Verify the subscription reflects the intended state:
     `select plan, subscription_status from tenants where id = '<tenant>';`
  4. A replay of an already-processed event must leave `tenants` unchanged and must not insert a
     second `billing_webhook_events` row — that is the idempotency test.

**When P8 is not yet deployed,** this section is the specification the handler is built to; the
recorder-push section above is exercisable today.
