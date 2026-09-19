# WatchLog AI — Incident Ontology

`ai-harness/taxonomy/` + `ai-harness/core/`. A shared vocabulary so the AI reasons at the right semantic
level and never collapses a raw detection into a claimed incident.

## Semantic hierarchy (never collapsed)

```
Observation → Activity → Journey / Episode → Incident → Evidence → Report
```

- **Observation** — a single primitive detection (`taxonomy/observations.yaml`, 24 primitives, e.g.
  `PERSON_PRESENT`, `VEHICLE_PRESENT`, `MULTI_CAMERA_MOVEMENT`, `AFTER_HOURS_PRESENCE`). Camera-level truth.
- **Activity** — observations composed into a meaningful action (`taxonomy/activities.yaml`).
- **Journey / Episode** — activity linked across cameras/time (multi-camera movement paths).
- **Incident** — a graded, reviewable event belonging to one of **7 incident families**
  (`taxonomy/incident-families.yaml`), schema `schemas/incident.schema.json`.
- **Evidence** — the scoped stills/clips/findings backing an incident (see
  [PRIVACY_AND_RETENTION](PRIVACY_AND_RETENTION.md)).
- **Report** — a frozen snapshot; once generated it is authoritative and never silently rewritten.

## Grading

- **Severity** (`taxonomy/severity.yaml`) — `routine → notable → suspicious → critical`.
- **Confidence** (`core/confidence.md`) — `STRONG / MODERATE / WEAK / DETECTION_GAP`. Behavioural identity
  is uncertain unless an approved identity source proves it — prefer *estimated / probable / plausible*.
- **Coverage class** — `LIVE / RECOVERED / UNVERIFIED`, kept strictly separate. **Unverified monitoring
  time is never reported as "no activity."**

## Truth rules (`core/truth.md`)

Never invent a capability, camera state, incident, identity, count, time, health state or tool result.
`UNKNOWN` means unconfirmed, not unsupported. `OFFICIAL_DOCUMENTED` ≠ `FIELD_VERIFIED`. These rules are
also embedded in the edge function's production system prompt and graded by the eval set (see
[TEST_REPORT](TEST_REPORT.md)).

## Retention linkage

`core/retention.yaml` names the evidence lifecycle tiers that `0107` enforces (operational 7d / harness
30d / incident-video 72h / findings retained separately).
