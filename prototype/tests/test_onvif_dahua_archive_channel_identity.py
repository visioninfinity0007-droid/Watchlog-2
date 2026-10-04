#!/usr/bin/env python3
"""MNVR-029: an ONVIF camera channel is an enumeration ordinal, not a recorder channel.

``OnvifDriver.list_channels`` numbers cameras in the order GetProfiles lists their video sources.
``open_archive_driver`` may read recorded media for an ONVIF site through the vendor-native
transport (dahua-cgi / hikvision-isapi), which addresses the recorder's OWN channel numbers. So a
camera may only be served natively when its ONVIF-to-native channel is VERIFIED: every profile of
its source carries the recorder's own ``MediaProfile_Channel<N>`` label, no other camera carries N,
and the native transport lists channel N. Anything else is refused, never guessed, so evidence can
never come from the wrong camera. Hermetic: no recorder, no network.
"""
from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import dahua_archive  # noqa: E402
import hikvision_archive  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import Channel, DeviceInfo, DriverError  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402

T0 = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=2)


def _profiles(*rows):
    """GetProfiles body from (profile name, SourceToken) rows."""
    body = "".join(
        f'<Profiles token="tok{i}"><Name>{name}</Name>'
        f"<VideoSourceConfiguration><SourceToken>{src}</SourceToken></VideoSourceConfiguration>"
        f"</Profiles>"
        for i, (name, src) in enumerate(rows))
    return f"<Envelope>{body}</Envelope>"


# The recorder exposes inputs 1, 2 and 4 over ONVIF (no Channel3 profile). ONVIF therefore
# registers Channel4 as camera "3".
GAPPED = _profiles(
    ("MediaProfile_Channel1_MainStream", "000"), ("MediaProfile_Channel1_SubStream1", "000"),
    ("MediaProfile_Channel2_MainStream", "001"), ("MediaProfile_Channel2_SubStream1", "001"),
    ("MediaProfile_Channel4_MainStream", "003"), ("MediaProfile_Channel4_SubStream1", "003"),
)


def _onvif(xml):
    driver = OnvifDriver("http://192.0.2.10", "local-user", "local-password", timeout=1)
    driver.media_service = "http://192.0.2.10/onvif/media_service"
    driver._call = lambda *_a, **_k: ET.fromstring(xml)
    return driver


class FakeNative:
    """A vendor-native archive reader that records which native channel each call addressed."""

    def __init__(self, name="dahua-cgi", channels=("1", "2", "3", "4")):
        self.name = name
        self._channels = channels
        self.clips, self.searches = [], []
        self.closed = False

    def probe(self):
        return DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I", driver=self.name)

    def list_channels(self):
        return [Channel(channel=c, name=f"Channel {c}") for c in self._channels]

    def historical_capability(self):
        return {"events": "supported", "snapshots": "unsupported", "segments": "supported"}

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        self.searches.append(str(channel))
        return {"status": "supported", "next_cursor": None, "events": [{
            "ts": T0.isoformat(), "type": "recorded_segment", "channel": str(channel),
            "device_event_id": f"seg-{channel}",
            "segment": {"start": T0.isoformat(), "end": T1.isoformat()}}]}

    def get_clip(self, channel, start, end):
        self.clips.append(str(channel))
        return b"DHAV-native-" + str(channel).encode()

    def get_recorded_segment(self, channel, start, end):
        return {"status": "supported", "bytes": self.get_clip(channel, start, end)}

    def close(self):
        self.closed = True


def _open(onvif, native, vendor="Dahua"):
    cfg = SimpleNamespace(nvr_url="http://192.0.2.10", nvr_username="local-user",
                          nvr_password="local-password")
    info = DeviceInfo(vendor=vendor, model="XVR", driver="onvif")
    with mock.patch.object(core, "open_driver", lambda _cfg: (onvif, info)), \
            mock.patch.object(core, "build", lambda name, *_a, **_k: native), \
            mock.patch.object(core, "log", lambda *_a, **_k: None):
        return core.open_archive_driver(cfg)


class OnvifChannelIsAnOrdinal(unittest.TestCase):
    def test_gapped_profiles_register_channel4_as_camera_3(self):
        # The premise of MNVR-029: the ONVIF camera number is the enumeration order.
        channels = _onvif(GAPPED).list_channels()
        self.assertEqual([c.channel for c in channels], ["1", "2", "3"])


class VerifiedNativeChannelMap(unittest.TestCase):
    def test_camera_reads_the_recorders_own_channel(self):
        native = FakeNative()
        driver, _info = _open(_onvif(GAPPED), native)
        self.assertEqual(driver.name, "dahua-cgi")
        driver.get_clip("3", T0, T1)
        driver.get_clip("1", T0, T1)
        self.assertEqual(native.clips, ["4", "1"])   # camera 3 is recorder Channel4, never input 3

    def test_recovered_segments_stay_on_the_watchlog_camera(self):
        native = FakeNative()
        driver, _info = _open(_onvif(GAPPED), native)
        page = driver.enumerate_historical_events("3", T0, T1)
        self.assertEqual(native.searches, ["4"])
        self.assertEqual(page["status"], "supported")
        self.assertEqual(page["events"][0]["channel"], "3")

    def test_camera_without_a_verified_map_is_refused_not_guessed(self):
        native = FakeNative()
        driver, _info = _open(_onvif(GAPPED), native)
        with self.assertRaises(DriverError):
            driver.get_clip("4", T0, T1)            # no ONVIF camera 4: never read native input 4
        page = driver.enumerate_historical_events("4", T0, T1)
        self.assertEqual(page["status"], "unknown")
        self.assertEqual(page["events"], [])
        self.assertEqual(native.clips, [])
        self.assertEqual(native.searches, [])

    def test_label_missing_on_the_native_side_is_refused(self):
        native = FakeNative(channels=("1", "2"))
        driver, _info = _open(_onvif(GAPPED), native)
        with self.assertRaises(DriverError):
            driver.get_clip("3", T0, T1)
        driver.get_clip("2", T0, T1)
        self.assertEqual(native.clips, ["2"])

    def test_unlabelled_profiles_keep_onvif_and_refuse_the_native_reader(self):
        onvif = _onvif(_profiles(("Profile_1", "VideoSource_1"), ("Profile_2", "VideoSource_2")))
        native = FakeNative()
        driver, _info = _open(onvif, native)
        self.assertIs(driver, onvif)                 # recorded media stays honestly unsupported
        self.assertTrue(native.closed)

    def test_duplicate_labels_are_ambiguous(self):
        onvif = _onvif(_profiles(("MediaProfile_Channel1_MainStream", "000"),
                                 ("MediaProfile_Channel1_MainStream", "001"),
                                 ("MediaProfile_Channel2_MainStream", "002")))
        native = FakeNative()
        driver, _info = _open(onvif, native)
        for camera in ("1", "2"):
            with self.assertRaises(DriverError):
                driver.get_clip(camera, T0, T1)
        driver.get_clip("3", T0, T1)
        self.assertEqual(native.clips, ["2"])

    def test_duplicate_label_beside_an_unlabelled_profile_is_ambiguous(self):
        # Camera 2 also carries Channel1 (next to an unlabelled profile), so Channel1 is claimed
        # twice and camera 1 must not be mapped to it either.
        onvif = _onvif(_profiles(("MediaProfile_Channel1_MainStream", "000"),
                                 ("MediaProfile_Channel1_MainStream", "001"),
                                 ("Profile_X", "001"),
                                 ("MediaProfile_Channel3_MainStream", "002")))
        native = FakeNative()
        driver, _info = _open(onvif, native)
        for camera in ("1", "2"):
            with self.assertRaises(DriverError):
                driver.get_clip(camera, T0, T1)
        driver.get_clip("3", T0, T1)
        self.assertEqual(native.clips, ["3"])

    def test_duplicate_label_inside_a_conflicting_label_set_is_ambiguous(self):
        # Camera 1 carries Channel1 and Channel5 (refused on its own); camera 3 also claims
        # Channel5, so camera 3 must not be mapped to it either.
        onvif = _onvif(_profiles(("MediaProfile_Channel1_MainStream", "000"),
                                 ("MediaProfile_Channel5_SubStream1", "000"),
                                 ("MediaProfile_Channel2_MainStream", "001"),
                                 ("MediaProfile_Channel5_MainStream", "002")))
        native = FakeNative(channels=("1", "2", "3", "4", "5"))
        driver, _info = _open(onvif, native)
        for camera in ("1", "3"):
            with self.assertRaises(DriverError):
                driver.get_clip(camera, T0, T1)
        driver.get_clip("2", T0, T1)
        self.assertEqual(native.clips, ["2"])

    def test_conflicting_labels_on_one_source_are_refused(self):
        onvif = _onvif(_profiles(("MediaProfile_Channel1_MainStream", "000"),
                                 ("MediaProfile_Channel5_SubStream1", "000"),
                                 ("MediaProfile_Channel2_MainStream", "001")))
        native = FakeNative(channels=("1", "2", "3", "4", "5"))
        driver, _info = _open(onvif, native)
        with self.assertRaises(DriverError):
            driver.get_clip("1", T0, T1)
        driver.get_clip("2", T0, T1)
        self.assertEqual(native.clips, ["2"])

    def test_zero_based_labels_are_not_trusted(self):
        onvif = _onvif(_profiles(("MediaProfile_Channel0_MainStream", "000"),
                                 ("MediaProfile_Channel1_MainStream", "001")))
        native = FakeNative()
        driver, _info = _open(onvif, native)
        self.assertIs(driver, onvif)
        self.assertTrue(native.closed)


class NativeDahuaSearchIndex(unittest.TestCase):
    """End to end through dahua_archive: the CGI search names the mapped native index."""

    def test_mapped_camera_searches_native_index_of_its_own_channel(self):
        native = DahuaDriver("http://192.0.2.10", "local-user", "local-password", timeout=1)
        native.probe = lambda: DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I", driver="dahua-cgi")
        native.list_channels = lambda: [Channel(channel=str(i), name=None) for i in range(1, 9)]
        sent = []

        def fake_text(_driver, path, *, params=None, timeout=None):
            params = params or {}
            sent.append(params)
            if params.get("action") == "getCurrentTime":
                return "result=" + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if params.get("action") == "factory.create":
                return "result=77"
            if params.get("action") == "findFile":
                return "OK"
            if params.get("action") == "findNextFile":
                return "found=0"
            return "OK"

        driver, _info = _open(_onvif(GAPPED), native)
        try:
            with mock.patch.object(dahua_archive, "_text", fake_text):
                driver.enumerate_historical_events("3", T0, T1)
        finally:
            driver.close()
        searches = [p for p in sent if p.get("action") == "findFile"]
        self.assertEqual([p["condition.Channel"] for p in searches], [3])   # Channel4, 0-based


class MappedArchiveProof(unittest.TestCase):
    def test_dahua_proof_runs_on_the_mapped_native_channel(self):
        native = FakeNative()
        driver, _info = _open(_onvif(GAPPED), native)
        proof = core.prove_recorder_archive(driver, "3")
        self.assertEqual(proof["status"], "verified")
        self.assertEqual(native.searches, ["4"])

    def test_hikvision_proof_runs_on_the_native_driver_and_channel(self):
        native = FakeNative(name="hikvision-isapi")
        driver, _info = _open(_onvif(GAPPED), native, vendor="Hikvision")
        seen = []

        def fake_enumerate(drv, channel, start, end, cursor=None, limit=500):
            seen.append((drv, str(channel)))
            return {"status": "supported", "events": [], "next_cursor": None}

        with mock.patch.object(hikvision_archive, "enumerate_historical_events", fake_enumerate):
            proof = core.prove_recorder_archive(driver, "3")
        self.assertEqual(seen, [(native, "4")])
        self.assertEqual(proof["status"], "empty")
        self.assertEqual(proof["channel"], "3")

    def test_proof_for_an_unverified_camera_is_unknown(self):
        native = FakeNative()
        driver, _info = _open(_onvif(GAPPED), native)
        proof = core.prove_recorder_archive(driver, "4")
        self.assertEqual(proof["status"], "unknown")
        self.assertEqual(native.searches, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
