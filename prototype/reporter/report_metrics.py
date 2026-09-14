#!/usr/bin/env python3
"""Canonical headline metrics — the SINGLE extraction every report surface shares (item 1).

Kept dependency-free so intelligence_whatsapp / intelligence_pdf / intelligence_report can all
import it without a cycle. No surface recomputes these numbers from raw data; they all read the
one frozen snapshot payload through here, which is why portal / WhatsApp / PDF agree.
"""
from __future__ import annotations


def _num(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def headline_metrics(payload: dict) -> dict:
    payload = payload or {}
    meta = payload.get("meta") or {}
    attn = payload.get("attention") or {}
    cov = payload.get("coverage") or {}
    people = (payload.get("people") or {}).get("summary") or {}
    db = payload.get("day_boundaries") or {}
    ah = payload.get("after_hours")
    if isinstance(ah, dict):
        after_hours, after_hours_verified = ah.get("count"), bool(ah.get("verified"))
    else:
        after_hours = payload.get("after_hours_episodes")
        after_hours_verified = after_hours is not None
    ratio = cov.get("coverage_ratio")
    classes = cov.get("classes") or {}

    def _hrs(secs):
        try:
            return round(float(secs) / 3600.0, 1)
        except (TypeError, ValueError):
            return None

    return {
        "site": meta.get("site"),
        "date": meta.get("date"),
        "schema": payload.get("schema"),
        # Three coverage classes — never blended (present only on v4+ payloads).
        "coverage_classes": ({
            "live_hours": _hrs(classes.get("live_seconds")),
            "recovered_hours": _hrs(classes.get("recovered_seconds")),
            "unverified_hours": _hrs(classes.get("unverified_seconds")),
            "live_ratio": classes.get("live_ratio"),
            "recovered_ratio": classes.get("recovered_ratio"),
            "unverified_ratio": classes.get("unverified_ratio"),
            "total_coverage_ratio": classes.get("total_coverage_ratio"),
        } if classes else None),
        "incidents_total": _num(attn.get("incidents_total")),
        "critical": _num(attn.get("critical")),
        "warning": _num(attn.get("warning")),
        "opening": db.get("opening_at"),
        "closing": db.get("closing_at"),
        "coverage_pct": (round(float(ratio) * 100) if ratio is not None else None),
        "probable_visitor": _num(people.get("probable_visitor")),
        "probable_regular_staff": _num(people.get("probable_regular_staff")),
        "unclassified": _num(people.get("unclassified")),
        "after_hours": after_hours,
        "after_hours_verified": after_hours_verified,
    }


__all__ = ["headline_metrics"]
