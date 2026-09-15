# Capability labels (invariant) — how to read a device-knowledge record

Every capability in this registry is graded on **two orthogonal axes** so provenance is never
laundered into a verdict (mirrors the DB KB, migration `0061`):

- **`verdict`** — *what the capability IS*.
- **`evidence_class`** — *how strongly we know it*.

A record is `support: {verdict, evidence_class}`. The two are independent: a strong evidence class
never upgrades a cautious verdict, and a confident verdict never invents evidence.

## Evidence classes (strength of knowledge, strongest → weakest)

```
FIELD_VERIFIED  >  OFFICIAL_DOCUMENTED  >  IMPLEMENTED_UNVERIFIED  >  UNSUPPORTED  >  UNKNOWN
```

| Class | Meaning | Assigned when |
|---|---|---|
| `FIELD_VERIFIED` | Proven on real hardware, dated field evidence. | ONLY from `0061` `recorder_field_evidence` / `vendor_capabilities.FIELD_PROVEN`. Currently: the client Dahua **DH-XVR1B08-I** (`FIELD-AKSS-001`, 2026-09-10). Never assigned from a datasheet or from code. |
| `OFFICIAL_DOCUMENTED` | Stated in an official vendor datasheet / manual / API guide. | The default for datasheet-sourced facts. Carries a `source` ref. |
| `IMPLEMENTED_UNVERIFIED` | WatchLog code implements the access path, but it has not met this vendor's hardware. | e.g. the seed's `storage_health` / `recording_mode` (code exists, data not proven). |
| `UNSUPPORTED` | A document (or field test) gives explicit/exhaustive evidence the feature is **absent**. | Modeled as `verdict: unsupported` + a `FIELD_VERIFIED`/`OFFICIAL_DOCUMENTED` evidence class (see mapping below). |
| `UNKNOWN` | No accessible source addresses it. Never guessed. | `verdict: unknown` + `evidence_class: UNKNOWN`. |

The composite ladder above (from `WATCHLOG.md` / `core/truth.md`) is a **reading order**, not a fifth
column: its `UNSUPPORTED` and `UNKNOWN` rungs are `(verdict, evidence_class)` pairs, as below.

## Verdict values (what the capability is)

| `verdict` | Meaning |
|---|---|
| `supported` | The recorder does this natively (see `ai_location` for recorder vs camera). |
| `unsupported` | Documented / field-proven absent on **this exact model**. Not a WatchLog-native analytic here. |
| `by_camera` | The analytic exists only via a capable IP camera; the recorder configures/records/ingests but **cannot originate** it. Never promise it from the recorder alone. |
| `unknown` | The source is silent. The honest default. **Never** read as `unsupported`. |

### The two hard rules (structural, never overridden by a model)
1. An `unknown` verdict can **never** be read as `unsupported` ("silent" ≠ "absent").
2. `OFFICIAL_DOCUMENTED` is **never** emitted as `FIELD_VERIFIED` (a datasheet is not a field test).

### Drive-taxonomy ↔ this-registry mapping
- Drive `UNSUPPORTED` = `verdict: unsupported` with a `FIELD_VERIFIED` or `OFFICIAL_DOCUMENTED` evidence class.
- Drive `UNKNOWN` = `verdict: unknown` + `evidence_class: UNKNOWN`.

## Safety class (risk of the write, under `write.safety_class`)

| `safety_class` | Meaning |
|---|---|
| `read` | Read-only capability; no write is performed (e.g. `video_loss`, `tamper`, `snapshot`, `storage_health`). |
| `safe_write` | A reversible, low-blast-radius write WatchLog may propose (e.g. `channel_title`, `time_ntp_config`, enabling `human_vehicle_classification`). Still gated by `core/actions.md` + a Site-Control proposal; **never** raw CGI/ISAPI from the model. |
| `high_risk` | A write that can degrade coverage or evidence (e.g. `recording_mode` — can stop recording; toggling an exclusive AI-Mode reconfigures a recorder's whole analytics plane). Requires explicit human approval. |
| `prohibited` | Must never be attempted by WatchLog (destructive / irreversible / out-of-scope). |
| `na` | Not applicable — the capability is `unsupported`, `by_camera`, or `unknown`, so there is no recorder-side write surface. |

## Supporting fields

- **`ai_location`** — `recorder` | `camera` | `both` | `na` | `unknown`. Distinguishes **recorder-native AI**
  from **AI-by-camera**; `unknown` is used only where a datasheet documents the analytic but is silent on
  where it runs (e.g. Hikvision `DS-7108NI-Q1/M`). This is *not* WatchLog software AI — never conflate them.
- **`read` / `write`** — `{supported: true|false|null, method: <CGI/ISAPI or na/UNKNOWN>}`. `null` = the
  source does not establish read/write support. A `method` of `UNKNOWN` means the capability may be present
  but the exact access path is not documented (e.g. Hikvision per-detector Smart write sub-paths).
- **`verification.required`** — `true` for everything not `FIELD_VERIFIED`: the fact is documented/implemented
  but must be confirmed against the live unit before it is relied on for a write or a customer promise.
- **`source`** — one or more `{url_or_doc, title, retrieved_at}`. Provenance travels with the record.
