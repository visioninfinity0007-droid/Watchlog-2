#!/usr/bin/env python3
"""MNVR-028 follow-up: a recorder-scoped event (channel None) serialises with a JSON null channel.

Event.to_json wrote "channel": str(self.channel), so every path that spools an Event through
to_json alone (the dormant watchlog_agent.collector, any future caller) sent the literal string
"None" as the channel of a recorder-scoped event. wl_ingest_events would then try to join a
camera called "None" and the dedupe key would carry "None" instead of the null marker. The
packaged collector worked around it in spool_row; the serialiser itself is now right, so no
caller can get it wrong.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.base import Event  # noqa: E402

WHEN = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)


def test_recorder_scoped_event_serialises_with_json_null_channel():
    ev = Event(channel=None, event_type="disk_error", device_ts=WHEN,
               payload={"vendor": "onvif", "recorder_scoped": True})
    row = ev.to_json(WHEN)
    assert row["channel"] is None
    assert '"channel": null' in json.dumps(row)


@pytest.mark.parametrize("channel, expected", [("3", "3"), (7, "7")])
def test_camera_channels_still_serialise_as_strings(channel, expected):
    row = Event(channel=channel, event_type="motion", device_ts=WHEN).to_json(WHEN)
    assert row["channel"] == expected


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
