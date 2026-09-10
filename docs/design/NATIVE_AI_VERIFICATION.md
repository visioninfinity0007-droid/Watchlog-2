# Native-AI secondary verification — the semantic change (item 15)

## What changed

Before item 8, the packaged collector's contract was blunt: **the `native_ai` branch must not
call the local classifier at all.** Its purpose was to protect recorder-native events —
especially geometry events (line crossing, intrusion) — from being thrown away because a single
still lacked an object at capture time.

Item 8 adds **secondary verification** inside that branch. The two contract tests
(`test_native_nvr_incident_evidence.py`, `test_capabilities.py`) were evolved to protect the
*actual* invariant precisely, rather than the blunt "no vision at all" proxy.

## The new invariant (stronger, not weaker)

1. **The raw recorder event is always retained.** Verification only ADDS a
   `native_verification` key (and the seen local classes); it never removes the event, clears
   its native fields, or makes the event's existence conditional. There is no false-alarm
   `continue` in the native branch.
2. **A simple person/vehicle classification MAY be secondarily verified** against the local
   YOLO model on a fresh still, producing `verified_human` / `verified_vehicle` /
   `classification_conflict` / `unverified_native`.
3. **A conflict prevents inappropriate customer-facing promotion.** The field regression — an
   indoor camera firing a native *Vehicle* false positive while the local model sees a person —
   resolves to `classification_conflict`, and `promoted_class()` returns `None`, so it is not
   promoted as a verified Vehicle incident. The recorder's event is still preserved and
   reported; only its *verified* status is withheld.
4. **Geometry/temporal events are never rejected from a single still.** `is_verifiable()` is
   False for line crossing, intrusion and dwell, so `annotate_event()` no-ops on them: no local
   pass, no annotation, no drop. The original concern the old contract protected is preserved
   exactly.

## Where it lives, and how it is tested

- The decision is a single reusable helper, `native_verification.annotate_event(payload,
  event_type, local_classes)`, called from `native_event_collector.py`'s native branch. One
  source of truth for the wiring, so it is unit-testable rather than only source-grepped.
- **Behavioral tests** (`test_native_verification.py::AnnotateEventWiring`) prove each clause
  above directly against payloads: raw event preserved, verified/conflict states, conflict not
  promoted, geometry not second-guessed.
- The two static contract tests still hold: the native branch never drops the event
  (`continue not in native_block`) and routes through `native_verification` /`is_verifiable`;
  the generic-motion path still runs the local false-alarm filter (`classify_event(raw)`).
