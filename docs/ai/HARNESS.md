# WatchLog AI — Harness

`ai-harness/` is the model-independent definition tree the AI reasons over — knowledge and rules, not
code. It is version-controlled, reviewable, and contains **no customer evidence**.

## Layout

```
ai-harness/
  WATCHLOG.md              product map + the core invariants
  CONTEXT.md               router: which file to read for which question
  taxonomy/
    observations.yaml      24 detection primitives
    activities.yaml        observations -> actions
    entities.yaml          people / vehicles / assets
    incident-families.yaml 7 incident families
    severity.yaml          routine / notable / suspicious / critical
  core/
    truth.md               never-invent rules; UNKNOWN != unsupported
    confidence.md          STRONG / MODERATE / WEAK / DETECTION_GAP
    retention.yaml         evidence lifecycle tiers (7d / 30d / 48-72h)
  schemas/
    incident.schema.json   the incident contract
  device-knowledge/        47-model recorder capability registry (see DEVICE_KNOWLEDGE.md)
```

## How it is used

- The edge function's **production system prompt** encodes the truth/safety invariants from `core/` and
  `WATCHLOG.md`. The eval set (`tools/ai_eval/`) grades a model against them.
- **Device knowledge** is consulted (via the DB resolver) by the deterministic setup advisor and the Site
  Control capability gate — the model is told what is supported; it never asserts it.
- The **incident schema/taxonomy** shape how evidence bundles and findings are interpreted and reported.

## Principles

- Knowledge is data, not prompt-stuffing: it is graded, sourced, and validated (`tools/validate_device_knowledge.py`).
- The harness is provider-agnostic — swapping the model changes nothing here.
- Honesty over coverage: an honest `unknown` always beats a confident guess.

## Related tooling

- `tools/validate_device_knowledge.py` — registry integrity + FIELD_VERIFIED guard (CI-gated).
- `tools/ai_eval/` — the model benchmark/eval set (see [TEST_REPORT](TEST_REPORT.md)).
- `tools/run_backend_job.py` — reproduce the CI backend job locally.
