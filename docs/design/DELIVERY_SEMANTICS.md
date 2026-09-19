# Report delivery semantics (item 8)

## What we guarantee

The daily report delivery is **at-least-once**, and **effective-once when the provider honors
the idempotency key**. We do not claim absolute exactly-once, because a downstream provider we
do not control can always be the weak link.

## The pipeline

```
wl_generate_daily_report  ->  report_snapshots (FROZEN payload + PDF ref + delivery status)
                          ->  wl_outbox_enqueue (one durable row per report+channel+destination,
                                                 unique idempotency_key)
                          ->  wl_outbox_claim   (atomic; also RECLAIMS stale 'sending' rows)
                          ->  transport.send(dest, text, idempotency_key)
                          ->  wl_outbox_mark    (sent | failed) + wl_set_report_delivery_status
```

## The crash edge, explicitly

The classic duplicate-delivery hazard is: **the provider ACCEPTS the message, then the process
or database dies before `sent` is persisted.** On the next run the row is still `sending`.

- `wl_outbox_claim` reclaims a `sending` row once it is older than `stale_seconds`, and returns
  the **same idempotency_key**.
- The sender re-sends with that same key.
- A provider that honors the key **dedups** the retry → the customer receives it once
  (effective-once). Proven in `e2e_intelligence_delivery_pg.py` with a key-honoring mock
  provider: two `send` calls for the reclaimed row, one actual delivery.

## The honest caveat

Evolution (the current WhatsApp provider) does **not** currently dedup on a client idempotency
key. So against Evolution today, the crash edge degrades to **at-least-once**: a reclaimed
attempt after a mid-send crash can produce a duplicate WhatsApp message. This is:

- bounded (only the rare crash-between-accept-and-persist window),
- visible (the outbox records `attempts`), and
- upgradeable to effective-once the moment the provider path carries a real idempotency key.

We record this rather than pretending the provider gives us exactly-once. The frozen snapshot
still guarantees that whatever is delivered — once or, in that rare window, twice — is the
**same** report content, never a silently recomputed one.
