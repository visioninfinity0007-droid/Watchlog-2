#!/usr/bin/env python3
"""Phase A — increment 3: server-side inventory derivation (spec for wl_report_health).

The cloud owns the "should-have" channel set (its cameras table), so it decides
PRESENT / MISSING / DISABLED / UNKNOWN — the agent only reports what the recorder currently
says. Pure logic; wl_report_health (0044) mirrors this and the contract test pins them.

Kept string-valued and matched to health_model.Inventory (a test enforces equality), so the
Python spec, the SQL domain, and the agent all speak one vocabulary.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

PRESENT = "present"
MISSING = "missing"
DISABLED = "disabled"
UNKNOWN = "unknown"


def classify_inventory(known_channels: Iterable[str],
                       reported: Iterable[str],
                       disabled: Iterable[str],
                       determinable: bool) -> Tuple[Dict[str, str], Set[str]]:
    """Classify every KNOWN camera channel, plus report channels we saw but do not own.

    `determinable` is (recorder reachable AND channel enumeration succeeded). When it is
    False we make NO removal claim — every known channel is UNKNOWN, because absence we
    could not observe is not the same as a channel that was taken out.

    Returns (states_by_channel, unmapped_reported). `unmapped_reported` are channels the
    recorder reports that are not (yet) cameras — surfaced for visibility, never persisted
    as inventory here (camera creation stays with enrollment/sync).
    """
    known = [str(c) for c in known_channels]
    if not determinable:
        return {c: UNKNOWN for c in known}, set()

    reported_set = {str(c) for c in reported}
    disabled_set = {str(c) for c in disabled}
    states: Dict[str, str] = {}
    for c in known:
        if c in disabled_set:
            states[c] = DISABLED
        elif c in reported_set:
            states[c] = PRESENT
        else:
            states[c] = MISSING
    unmapped = reported_set - set(known)
    return states, unmapped


def diff_transitions(prev_states: Dict[str, Optional[str]],
                     new_states: Dict[str, str]) -> List[Tuple[str, Optional[str], str]]:
    """(channel, from, to) for channels whose state changed. Identical states -> [] (idempotent)."""
    out = []
    for ch, to in new_states.items():
        frm = prev_states.get(ch)
        if frm != to:
            out.append((ch, frm, to))
    return out


__all__ = ["classify_inventory", "diff_transitions",
           "PRESENT", "MISSING", "DISABLED", "UNKNOWN"]
