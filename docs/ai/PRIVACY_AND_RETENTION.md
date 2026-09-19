# WatchLog AI — Privacy, Evidence & Retention

Evidence lives in the **canonical database**, never a filesystem-as-database. The AI never gets
unrestricted tenant storage — only server-authorized `tenant → site → date → camera → hour → event`
manifests. Migration `0107`.

## Two-stage retrieval

1. **`wl_ai_evidence_index(site, from, to, camera?)`** — compact metadata search. Tenant/site scoped
   (`wl_assert_my_site` → `42501` for a foreign site). Returns event ids, timestamps, camera, class,
   detection counts, coverage class — **no image bytes**.
2. **`wl_ai_evidence_bundle(site, event_ref)`** — the full bundle for ONE selected event: `event.json`,
   `snapshots` (decrypted), `detections`, `coverage`, `provenance`, `analysis`. A manipulated `event_ref`
   for another site returns an empty bundle; a foreign `site_id` is refused.

The router loads evidence **only on a model route for an evidence-intent prompt** — a NO_MODEL/status
question never touches the workspace.

## Encryption at rest

Payloads are stored encrypted (`pgp_sym_encrypt`) with a key from Supabase Vault
(`wl_ai_evidence_key`; a clearly-labelled non-secret fallback keeps the disposable CI database runnable).
Only `wl_ai_evidence_bundle`, for an authorized caller, decrypts. The raw `payload_enc` column is never
the plaintext.

## Data egress

Per-site policy `wl_ai_site_egress` (tenant-owned, **local-only by default**). Set only by the site's own
owner/admin (`wl_ai_set_site_egress`) — never a platform admin, never a model. The router applies it after
mode resolution; a local-only site's images are withheld from any external provider (`stripEvidenceImages`
+ the egress gate). See [PROVIDER_ROUTING](PROVIDER_ROUTING.md).

## Retention tiers (server-set, un-extendable)

| Class | TTL | Notes |
|---|---|---|
| `operational_snapshot` | **7 days** | operational/database stills |
| `harness_snapshot` | **30 days** | harness/evidence stills |
| `incident_video` | **72 hours** | NVR-copied video, 48–72h ceiling |
| `finding` | none here | structured findings/incidents/reports retained separately under WatchLog policy |

TTLs are computed by the server at write time (`wl_ai_evidence_put`) from the class. **There is no update
path for `expires_at`** — a model or tenant can never extend retention.

## Deletion — automatic, idempotent, auditable

`wl_evidence_enforce_retention()` (hourly cron) deletes expired evidence per class/site, records each
batch in **`evidence_deletion_audit`**, and drains the previously-orphaned `0058` incident-still pruner. A
re-run deletes nothing and errors nothing (retry-safe). **The deletion audit survives after the raw
evidence is gone** — you can always prove what was deleted and when.

## Proven (CI: `e2e_ai_evidence_pg.py`, 9 checks)

tenant A cannot enumerate/fetch tenant B evidence · site A cannot fetch another site via manipulated ids ·
compact index carries no bytes · encryption round-trips · 7-day operational snapshots expire · harness
snapshots survive to 30d then purge · incident video expires within the 72h ceiling · retention is
retry-safe · the audit persists after deletion.
