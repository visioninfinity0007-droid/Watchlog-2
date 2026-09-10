# Offline resilience audit (item 7)

Which M1 record types survive an internet outage, how, and — where they intentionally do not
buffer — why that is correct rather than a gap. The product claim is: *a site survives its
internet link*. That claim only has to hold for **data that would be lost forever if dropped
between two polls**. Current-state and server-queued records re-establish themselves on
reconnect and need no client buffer.

## Verdict by record type

| Record type | Agent RPC | Resilience | Mechanism / why |
|---|---|---|---|
| **Raw events** | `wl_ingest_events` | **Spooled** | `agent/spool.py` (WAL SQLite) + `upload_once`; peek→POST→ack **only after the server commits** (at-least-once). Bounded (`MAX_ROWS`), oldest-dropped-loudly. |
| **Analytic events** | `wl_ingest_analytic_events` | **Spooled** | Separate `analytics_spool.sqlite` + `analytics_upload_once`, identical ack-after-commit pattern (`agent/analytics_agent.py`). |
| Health observations | `wl_report_camera_health`, `wl_report_health`, `wl_reconcile_health`, `wl_reconcile_recording_storage` | Current-state | Health is a **state**, re-reported every cycle. A cycle missed while offline is superseded by the next report of *current* state on reconnect — nothing to buffer, no loss. Transitions are recorded server-side only on state change (0045), so a persisting fault does not accumulate. |
| Monitoring coverage gap | `wl_report_coverage_gap` | Post-hoc + idempotent | A gap **describes a past window** (started/ended). It is emitted on resume and the RPC is idempotent (a duplicate returns `duplicate:true`). The outage reports *itself* once connectivity returns. |
| Heartbeat | `wl_heartbeat` | Ephemeral (by design) | Liveness only. Absence *is* the signal ("offline"); buffering stale heartbeats would be a lie. |
| Command / audit results | `wl_agent_claim_command`, `wl_agent_complete_command` | Server-queued | The command queue lives in Postgres. The agent claims and completes **only when online**; an in-flight command stays claimed (lease/fence) and is completed on reconnect. No client buffer needed. |
| Archive scan results | `wl_agent_claim_archive_scans`, `wl_agent_record_archive_result`, `wl_agent_set_archive_scan_status` | Server-queued | Same as commands — scans are server-owned work items reported when online. |
| Config snapshot / capabilities / camera sync | `wl_upload_config_snapshot`, `wl_sync_capabilities`, `wl_agent_report_capabilities`, `wl_sync_cameras` | Current-state (latest-wins) | Idempotent upserts of the *current* picture; re-sent on reconnect. |

**Conclusion:** the only data-loss-sensitive streams are the two event streams, and both are
spooled with at-least-once delivery. Everything else is current-state (re-asserted on resume)
or server-queued (no client buffering possible or needed). This is deliberate, not a shortfall.

## The full cycle, proven

`cloud unavailable → local processing continues → restart while offline → cloud returns → records sync idempotently`

- **Local processing continues while offline** — `prototype/tests/test_spool_offline.py::test_offline_keeps_everything_unacked`: a failed POST acks nothing; events keep accumulating in the spool.
- **Restart while offline loses nothing** — `test_spool_offline.py::test_survives_reopen`: closing and reopening the WAL spool preserves every queued event in order (a power cut is the normal case in the field, not the exception).
- **At-least-once on reconnect** — `test_spool_offline.py::test_recovery_after_outage`: the same buffered events go up intact once the link returns; nothing is dropped, nothing is size-wedged.
- **Idempotent sync at the server** — `prototype/tests/e2e_offline_sync_pg.py`: re-delivering the same batch to `wl_ingest_events` inserts 0 (deduped by key); a mixed old+new batch inserts only the new. So an at-least-once client + an idempotent server = exactly-once effect end to end.
