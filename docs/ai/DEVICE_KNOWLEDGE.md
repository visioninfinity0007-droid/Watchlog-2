# WatchLog AI — Device Knowledge

`ai-harness/device-knowledge/` — a normalized, source-linked, evidence-graded registry of **47** Dahua &
Hikvision recorder models (22 Dahua + 25 Hikvision), so the AI knows exactly what each recorder can and
cannot do, and never fabricates a capability.

## Evidence grades (strict order)

`FIELD_VERIFIED > OFFICIAL_DOCUMENTED > IMPLEMENTED_UNVERIFIED > UNSUPPORTED > UNKNOWN`

- **FIELD_VERIFIED** — proven on the actual client unit by a WatchLog probe/agent run. Site-specific truth,
  not manufacturer marketing. (Today: the Al-Khalid `DH-XVR1B08-I` — SMD human/vehicle yes; line-cross /
  intrusion / face / ANPR / people-counting no.)
- **OFFICIAL_DOCUMENTED** — from a dated datasheet/ISAPI/CGI reference.
- **IMPLEMENTED_UNVERIFIED** — code exists, not field-proven → resolves to honest `unknown`.
- **UNKNOWN** — the source is silent. Never generalized from a sibling model.

Verdicts: `supported | unsupported | by_camera | unknown`. Write safety: `read | safe_write | high_risk |
prohibited | na`.

## Schema (per model YAML)

`manufacturer, model, family, firmware_range, recorder_type, ai_processing_location, hardware,
capabilities[] {capability, support{verdict, evidence_class}, ai_location, read, write{safety_class},
verification, notes}, source[] {url_or_doc, title, retrieved_at}`.

## Validation path (this is how field truth is protected)

`tools/validate_device_knowledge.py` (CI: `test_device_knowledge_validator.py`) enforces the schema,
the enum ranges (including honest `unknown` for AI location), dated+linked sources, and the core guard:

> A capability may be graded **FIELD_VERIFIED only if the model cites the probe/agent artifact** that
> produced it (`tools/*_probe.ps1`, `prototype/agent/drivers/*.py`). Datasheet data can never masquerade
> as earned field truth. Title text is deliberately not trusted.

Proven non-vacuous: a datasheet-cited FIELD_VERIFIED claim is rejected; a probe-cited one is accepted.

## Resolver

The DB capability KB (`0061` model + `0066`/`0077` batches, resolver `wl_recorder_capability`) is the
runtime source of truth the deterministic setup advisor and Site Control gate consult. The YAML registry
is the evidence-graded, source-linked knowledge layer generated from it plus `docs/research/*`.
