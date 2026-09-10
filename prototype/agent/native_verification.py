#!/usr/bin/env python3
"""Native-AI secondary verification.

Recorder classification (Dahua SMD Human/Vehicle, etc.) + local WatchLog evidence
(YOLOv8n on a fresh snapshot) -> a verification STATE. The ORIGINAL recorder event is
always preserved (this never rewrites source history); the verification state is
attached alongside so customer-facing incident promotion can prefer verified
classifications and refuse a conflicted one.

The failure class this guards is real and field-observed: an indoor camera firing a
native "Vehicle" false positive must NOT become a verified Vehicle incident when the
local model sees person-only (or nothing plausibly vehicle).

Pure logic (no I/O); the snapshot->classes step is injected by the caller (vision.py).
"""
from __future__ import annotations

VERIFIED_HUMAN = "verified_human"
VERIFIED_VEHICLE = "verified_vehicle"
CONFLICT = "classification_conflict"
UNVERIFIED = "unverified_native"          # no fresh usable local evidence -> keep native, flagged

# Coarse category mapping for local detector class names.
_PERSON = {"person", "human"}
_VEHICLE = {"car", "truck", "bus", "van", "motorcycle", "motorbike", "vehicle", "bicycle"}


def _cat(local_classes) -> tuple[bool, bool]:
    local = {str(c).lower() for c in (local_classes or [])}
    return bool(local & _PERSON), bool(local & _VEHICLE)


def verify(native_event_type: str, local_classes) -> str:
    """Return the verification state for a native classification against local evidence.

    local_classes: iterable of local-detector class names found in the fresh snapshot;
    None/empty means NO usable local evidence (a video-loss placeholder, a dropped snapshot,
    or a temporal/geometry event where a single still cannot confirm the subject).
    """
    if not local_classes:
        return UNVERIFIED
    has_person, has_vehicle = _cat(local_classes)
    if native_event_type == "person":
        if has_person:
            return VERIFIED_HUMAN
        return CONFLICT if has_vehicle else UNVERIFIED
    if native_event_type == "vehicle":
        if has_vehicle:
            return VERIFIED_VEHICLE
        # native Vehicle but the local model clearly sees a person and no vehicle -> conflict
        return CONFLICT if has_person else UNVERIFIED
    return UNVERIFIED                        # non-classified native event: nothing to verify


def promoted_class(native_event_type: str, state: str) -> "str | None":
    """The class an incident layer should treat this as. A CONFLICT native classification is
    NOT promoted (None); an UNVERIFIED one keeps the native class but stays flagged unverified."""
    if state == VERIFIED_VEHICLE:
        return "vehicle"
    if state == VERIFIED_HUMAN:
        return "person"
    if state == CONFLICT:
        return None
    return native_event_type


def is_verifiable(native_event_type: str) -> bool:
    """Only simple object classifications are secondarily verifiable from a still. Geometry/
    temporal events (line crossing, dwell) must NOT be invalidated by a single later snapshot."""
    return native_event_type in ("person", "vehicle")


__all__ = ["VERIFIED_HUMAN", "VERIFIED_VEHICLE", "CONFLICT", "UNVERIFIED",
           "verify", "promoted_class", "is_verifiable"]
