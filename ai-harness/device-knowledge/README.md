# device-knowledge — Dahua / Hikvision recorder capability registry

This is WatchLog's **recorder intelligence**: a structured, evidence-graded record of what each exact
recorder model can natively do, and how strongly we know it — read *before* the portal or an AI ever
offers or applies a configuration. It is the machine-readable expression of `WATCHLOG.md`
Non-negotiable #3 (**Capability truth**) and `core/truth.md` (**Capability truth: evidence classes**).

> What a recorder can do is read from here with an evidence class
> (`FIELD_VERIFIED > OFFICIAL_DOCUMENTED > IMPLEMENTED_UNVERIFIED > UNSUPPORTED > UNKNOWN`).
> Never claim a native analytic (line-crossing, ANPR, face, people-counting) a *model* could have but
> **this exact recorder** does not. Recorder-native AI is never conflated with WatchLog software AI.

## This registry is GENERATED — single source of truth

These files are transcribed from WatchLog's two authoritative capability sources; they are **not** a new
source and must not be hand-edited into disagreement with them:

1. **The DB capability KB** — migrations
   `prototype/supabase/migrations/0061_recorder_capability_model.sql` (schema + the field-verified seed),
   `0066_recorder_capability_kb_batch2.sql`, `0077_recorder_capability_kb_batch3.sql` (model rows).
   At runtime the same facts are served by the `wl_recorder_capability()` / `wl_recorder_profile()`
   SECURITY DEFINER RPCs. **The DB is authoritative; this registry mirrors it for the harness.**
2. **The research docs** — `docs/research/dahua_capability_research.md` and
   `docs/research/hikvision_capability_research.md` (normalized, source-backed datasheet tables).

Separately, `prototype/agent/vendor_capabilities.py` is a **different axis** — does *our driver code*
implement a capability (`proven`/`unverified`/`unsupported`) — and is the ONLY origin of `FIELD_VERIFIED`
here (via its `FIELD_PROVEN` set + `0061` field evidence). Product capability (this registry) and agent
capability (that matrix) are kept apart so an agent shipping a feature is never mistaken for a recorder
supporting it.

To regenerate after the KB or research docs change: re-run the DB→registry loader (the emitter used to
build these files is intentionally not kept in-tree, so there is no second copy of the facts to drift).

## Layout

```
device-knowledge/
├── README.md                     # this file
├── capability-labels.md          # the label semantics: evidence classes, verdicts, safety classes
├── dahua/
│   ├── protocols.yaml            # Dahua HTTP CGI read/write surface (+ snapshot note)
│   └── models/<model>.yaml       # 22 files, one per exact Dahua model
└── hikvision/
    ├── protocols.yaml            # Hikvision ISAPI read/write surface (+ snapshot note)
    └── models/<model>.yaml       # 25 files, one per exact Hikvision model
```

**47 model files** (22 Dahua + 25 Hikvision) — every model documented in the research docs and KB
batches, including the live client seed unit **`DH-XVR1B08-I`** in full (SMD human/vehicle
`FIELD_VERIFIED`; line-cross / intrusion / face / ANPR / people-counting `unsupported` on this model).
Each file matches the shape defined in `capability-labels.md`: `manufacturer, family, model,
firmware_range, capabilities[…], source[…]` (+ sourced `recorder_type`, `ai_processing_location`,
`hardware`, and a `provenance_caveat` where a source flagged a mirror / translation / substitution).

## How the harness routes to a model file

Per `CONTEXT.md`:
- **"What can this recorder support?"** → `playbooks/recorder-change.yaml` (inspect only) loads
  `device-knowledge/<vendor>/…` and the `search_device_capabilities` tool.
- **"Enable human/vehicle detection on camera X"** → same playbook → `propose_site_control_change` +
  `core/actions.md`; **never** raw CGI/ISAPI.

Resolution is deterministic and needs no model:
1. The app resolves the recorder's **vendor + exact model** (preserving regional / firmware suffix — e.g.
   `-UHK`, `/16P`, `/L`, `HW2.0`). The harness never invents a model.
2. Normalize to a filename: lowercase, `/` and separators → `-`, parentheses dropped
   (`DS-7616NXI-K2/16P` → `hikvision/models/ds-7616nxi-k2-16p.yaml`). Match the **most specific** exact
   model; do not fall back to a sibling in the same series — capacities and even which analytics are
   recorder-side differ **per model, not per family** (e.g. Perimeter is 1-ch on `DS-7616NXI-K1` but
   2-ch on `DS-7616NXI-K2/16P`; `DS-7616NI-K2/16P` (Pro K, camera-side) is a *different* model from
   `DS-7616NXI-K2/16P` (AcuSense, recorder-side)).
3. Read the capability, honor `verdict` + `evidence_class` + `write.safety_class`, and require live
   verification unless `FIELD_VERIFIED` (`verification.required`).
4. **No file / no row → honest `unknown`** (verdict `unknown`, evidence `UNKNOWN`), exactly like the DB
   resolver's default. `unknown` is never read as `unsupported`.

The write surface (which CGI/ISAPI, and how risky) lives in `<vendor>/protocols.yaml`, keyed by the same
capability names. Note the **snapshot caveat** carried there and on every `snapshot` record: Dahua
`snapshot.cgi` (and the Hikvision picture endpoint) returns the recorder's low snapshot-encode resolution
(often CIF 352×288); full resolution requires a main-stream RTSP keyframe (`subtype=0`) — field-confirmed
on the client XVR.

## Provenance notes

- `source.retrieved_at` is **2026-09-10** for research- and field-sourced facts. For KB batch-2/3 rows it
  is the **datasheet publication date** recorded in the migration (the KB stored that in its
  `retrieved_date`), so it dates the document, not the fetch.
- Nothing is `FIELD_VERIFIED` except capabilities of the client **DH-XVR1B08-I** actually exercised on the
  hardware (`FIELD-AKSS-001`). WatchLog Hikvision support is code-only: **no Hikvision row is field-verified.**
- Provenance caveats (mirror / translated / substitution / non-rendering product page) are preserved in
  the affected model's `provenance_caveat` and its `source` title — re-verify against an official PDF
  before gating a config on those.
