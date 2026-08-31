#!/usr/bin/env python3
"""
Daily-report HTML email template tests.

Covers the cases that break email rendering in practice: a zero-event day,
a normal day, a high-volume day, equipment faults, very long site/camera
names, and — the one that matters for security — HTML escaping of
attacker-controlled strings (a camera named "<script>" must never reach the
inbox as live markup).

    python prototype/tests/test_email_template.py
    pytest -q prototype/tests/test_email_template.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reporter"))

from email_template import render_html, subject  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


NORMAL = {
    "site": "AKSS Head Office", "date": "2026-08-31", "timezone": "Asia/Karachi",
    "total_events": 7, "after_hours_events": 7,
    "by_camera": [{"camera": "Loading Bay", "count": 3},
                  {"camera": "Main Gate", "count": 2},
                  {"camera": "Rear Perimeter", "count": 2}],
    "by_type": {"motion": 1, "person": 2, "vehicle": 1, "intrusion": 1,
                "line_crossing": 1, "tamper": 1},
    "faults": {"tamper": 1},
    "first_event_at": "2026-08-31T16:40:00+00:00",
    "last_event_at": "2026-08-31T17:16:49+00:00",
}


def _well_formed(h: str):
    assert h.startswith("<!DOCTYPE html>"), "missing doctype"
    assert h.rstrip().endswith("</html>"), "not closed"
    assert h.count("<table") == h.count("</table>"), "unbalanced tables"


@case("normal day renders site, count, cameras, types, faults")
def t_normal():
    h = render_html(NORMAL, "https://app.example")
    _well_formed(h)
    for needle in ("AKSS Head Office", ">7<", "Loading Bay", "Main Gate",
                   "Person", "Tamper", "Needs attention", "after hours",
                   "Open WatchLog", "https://app.example"):
        assert needle in h, f"missing {needle!r}"
    assert subject(NORMAL) == "WatchLog — AKSS Head Office — 7 events, 31 Aug"
    return "all sections present"


@case("quiet night: no events")
def t_zero():
    r = dict(NORMAL, total_events=0, after_hours_events=0, by_camera=[],
             by_type={}, faults={})
    h = render_html(r, "https://app.example")
    _well_formed(h)
    assert "Nothing to report" in h and "quiet" in h.lower()
    assert "quiet night" in subject(r).lower()
    return "quiet-night hero shown"


@case("high-volume day formats the number with a separator")
def t_high():
    r = dict(NORMAL, total_events=1234)
    h = render_html(r, "#")
    _well_formed(h)
    assert "1,234" in h, "thousands separator missing"
    assert "1,234 events" in subject(r)
    return "1,234 rendered"


@case("faults surface in a Needs-attention block")
def t_faults():
    r = dict(NORMAL, by_type={"video_loss": 3}, faults={"video_loss": 3, "offline": 1})
    h = render_html(r, "#")
    assert "Needs attention" in h and "Video loss" in h and "Camera offline" in h
    return "faults highlighted"


@case("long site/camera names do not break layout")
def t_long():
    long = "Very Long Site Name " * 8
    r = dict(NORMAL, site=long,
             by_camera=[{"camera": "Camera " * 20, "count": 999}])
    h = render_html(r, "#")
    _well_formed(h)
    assert long in h
    return "rendered without error"


@case("HTML in a camera/site name is escaped, not injected")
def t_escaping():
    r = dict(NORMAL, site='<script>alert("x")</script>',
             by_camera=[{"camera": "<img src=x onerror=alert(1)>", "count": 1}],
             by_type={}, faults={})
    h = render_html(r, 'https://x/"><script>')
    # the raw dangerous markup must not appear verbatim
    assert "<script>alert" not in h, "site name injected as live script"
    assert "<img src=x onerror" not in h, "camera name injected as live html"
    assert "&lt;script&gt;" in h, "site name not escaped"
    # subject must not carry raw markup either (it is used as a header)
    assert "<script>" not in subject(r)
    return "escaped safely"


def run() -> int:
    print("Daily-report email template")
    print("=" * 62)
    p = f = 0
    for name, fn in CASES:
        try:
            print(f"  PASS  {name}\n          {fn()}"); p += 1
        except AssertionError as e:
            print(f"  FAIL  {name}\n          {e}"); f += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}"); f += 1
    print("=" * 62)
    print(f"  {p} passed, {f} failed")
    return 1 if f else 0


def test_email_template():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
