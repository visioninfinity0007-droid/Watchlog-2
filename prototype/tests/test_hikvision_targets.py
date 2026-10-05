#!/usr/bin/env python3
"""MNVR-054: Hikvision targetType parsing keeps recorder-native person/vehicle labels.

The split pattern used to be the raw string ``[,;|\\\\s]+``, a character class of
backslash and the LETTER s. "person" became ["per", "on"], "pedestrian" became
["pede", "trian"] and "human vehicle" was never split, so a recorder-classified human
event lost its native classification and fell through to the local false-alarm gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.native_recorder import NativeHikvisionDriver  # noqa: E402


def _vmd(target: str, channel: str = "2") -> bytes:
    return f"""<EventNotificationAlert>
      <eventType>VMD</eventType><eventState>active</eventState>
      <channelID>{channel}</channelID>
      <dateTime>2026-09-28T01:00:00+05:00</dateTime>
      <activePostCount>1</activePostCount>
      <targetType>{target}</targetType>
    </EventNotificationAlert>""".encode()


def _targets(target: str) -> list:
    driver = NativeHikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    try:
        event = driver._parse_alert(_vmd(target))
        assert event is not None
        return event.payload["targets"], event.payload["native_ai"]
    finally:
        driver.close()


def test_person_and_pedestrian_are_human():
    for word in ("person", "pedestrian", "Person"):
        targets, native = _targets(word)
        assert targets == ["human"], (word, targets)
        assert native is True


def test_whitespace_and_delimited_multi_targets():
    for text in ("human vehicle", "person  car", "human,vehicle", "pedestrian;motorVehicle",
                 "human|vehicle"):
        targets, native = _targets(text)
        assert targets == ["human", "vehicle"], (text, targets)
        assert native is True


def test_unrelated_words_are_not_split_into_targets():
    targets, native = _targets("animals")
    assert targets == []
    assert native is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
