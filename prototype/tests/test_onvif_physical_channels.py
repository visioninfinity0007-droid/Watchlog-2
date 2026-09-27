#!/usr/bin/env python3
"""Regression: ONVIF encoding profiles must collapse to physical cameras."""
from __future__ import annotations

import sys
from pathlib import Path
import xml.etree.ElementTree as ET

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

from drivers.onvif_driver import OnvifDriver  # noqa: E402


XML = """<Envelope>
  <Profiles token="cam1-main">
    <Name>MediaProfile_Channel1_MainStream</Name>
    <VideoSourceConfiguration><SourceToken>VideoSource_1</SourceToken></VideoSourceConfiguration>
  </Profiles>
  <Profiles token="cam1-sub">
    <Name>MediaProfile_Channel1_SubStream1</Name>
    <VideoSourceConfiguration><SourceToken>VideoSource_1</SourceToken></VideoSourceConfiguration>
  </Profiles>
  <Profiles token="cam2-main">
    <Name>MediaProfile_Channel2_MainStream</Name>
    <VideoSourceConfiguration><SourceToken>VideoSource_2</SourceToken></VideoSourceConfiguration>
  </Profiles>
  <Profiles token="cam2-sub">
    <Name>MediaProfile_Channel2_SubStream1</Name>
    <VideoSourceConfiguration><SourceToken>VideoSource_2</SourceToken></VideoSourceConfiguration>
  </Profiles>
</Envelope>"""


def test_profiles_collapse_by_source_token():
    d = OnvifDriver("http://127.0.0.1", "admin", "x", timeout=1)
    d.media_service = "http://127.0.0.1/onvif/media"
    d._call = lambda *_a, **_k: ET.fromstring(XML)
    channels = d.list_channels()
    assert [(c.channel, c.name) for c in channels] == [
        ("1", "Camera 1"),
        ("2", "Camera 2"),
    ]
    assert d._source_to_channel == {"VideoSource_1": "1", "VideoSource_2": "2"}
    assert d._profile_tokens == {"1": "cam1-main", "2": "cam2-main"}
    d.close()


def test_missing_source_token_is_not_merged():
    d = OnvifDriver("http://127.0.0.1", "admin", "x", timeout=1)
    d.media_service = "http://127.0.0.1/onvif/media"
    d._call = lambda *_a, **_k: ET.fromstring(
        '<Envelope><Profiles token="a"><Name>A</Name></Profiles>'
        '<Profiles token="b"><Name>B</Name></Profiles></Envelope>'
    )
    channels = d.list_channels()
    assert [(c.channel, c.name) for c in channels] == [("1", "A"), ("2", "B")]
    d.close()


if __name__ == "__main__":
    test_profiles_collapse_by_source_token()
    test_missing_source_token_is_not_merged()
    print("OK: ONVIF physical-camera profile de-duplication")
