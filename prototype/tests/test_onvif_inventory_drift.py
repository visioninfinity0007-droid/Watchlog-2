#!/usr/bin/env python3
"""An ONVIF camera keeps the channel the Agent synced to the cloud while the Agent runs.

``OnvifDriver`` numbers cameras by the position of their video source in GetProfiles. The cloud
maps channel -> camera UUID once, from the inventory the Agent synced at startup, but 5.0.28 reads
GetProfiles again while running (every reconnect probe, a refused Renew, the stills worker's
re-enumeration). When a source in the middle of the list disappeared, the next one moved onto its
number: camera C's events, stills, snapshots and footage were filed under camera B in the cloud.

The Agent now pins the source -> channel binding it synced for the rest of the process. A later
read keeps every known source on its synced channel; a gone source leaves its channel empty
(no events, stills or snapshots for it), and a source the cloud has never seen is held back
(logged) until a restart re-syncs the inventory, never numbered into another camera's slot.
Hermetic: no recorder, no network.
"""
from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (keeps Agent state out of the real ProgramData)

import watchlog_agent as core  # noqa: E402
from drivers import onvif_driver  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402

URL = "http://192.0.2.10"


def _profiles(*sources):
    """GetProfiles body: one MainStream + SubStream profile pair per SourceToken."""
    body = "".join(
        f'<Profiles token="{src}_main"><Name>MediaProfile_Channel{i}_MainStream</Name>'
        f"<VideoSourceConfiguration><SourceToken>{src}</SourceToken></VideoSourceConfiguration>"
        f"</Profiles>"
        f'<Profiles token="{src}_sub"><Name>MediaProfile_Channel{i}_SubStream1</Name>'
        f"<VideoSourceConfiguration><SourceToken>{src}</SourceToken></VideoSourceConfiguration>"
        f"</Profiles>"
        for i, src in enumerate(sources, start=1))
    return f"<Envelope>{body}</Envelope>"


def _onvif(xml, url=URL):
    driver = OnvifDriver(url, "local-user", "local-password", timeout=1)
    driver.media_service = url + "/onvif/media_service"
    driver._call = lambda *_a, **_k: ET.fromstring(xml)
    return driver


ABC = _profiles("VS_A", "VS_B", "VS_C")
AC = _profiles("VS_A", "VS_C")
ABCD = _profiles("VS_A", "VS_B", "VS_C", "VS_D")


class InventoryDrift(unittest.TestCase):
    def setUp(self):
        onvif_driver.unpin_inventory()

    def tearDown(self):
        onvif_driver.unpin_inventory()

    def _synced(self, xml=ABC):
        driver = _onvif(xml)
        channels = core._synced_inventory(driver)       # what main() sends to wl_sync_cameras
        self.assertEqual([c.channel for c in channels], ["1", "2", "3"])
        return driver

    def test_a_middle_source_removed_mid_run_does_not_move_its_neighbour(self):
        self._synced()
        lines = []
        reopened = _onvif(AC)                            # the reconnect after B was removed
        reopened.log = lines.append
        self.assertEqual([c.channel for c in reopened.list_channels()], ["1", "3"])
        self.assertEqual(reopened._source_to_channel, {"VS_A": "1", "VS_C": "3"})
        self.assertEqual(reopened._profile_tokens, {"1": "VS_A_main", "3": "VS_C_main"})
        self.assertNotIn("2", reopened._profile_tokens, "camera B's slot must stay empty")
        self.assertTrue(any("changed since" in line for line in lines), lines)

    def test_the_running_driver_keeps_its_numbering_on_a_refresh(self):
        driver = self._synced()
        driver._call = lambda *_a, **_k: ET.fromstring(AC)
        driver._require_profiles(refresh=True)          # the collector after a refused Renew
        self.assertEqual(driver._source_to_channel, {"VS_A": "1", "VS_C": "3"})
        self.assertEqual(driver._profile_tokens.get("3"), "VS_C_main")
        self.assertIsNone(driver._profile_tokens.get("2"))

    def test_a_source_the_cloud_has_not_seen_is_held_back_not_numbered(self):
        self._synced()
        reopened = _onvif(ABCD)
        self.assertEqual([c.channel for c in reopened.list_channels()], ["1", "2", "3"])
        self.assertNotIn("VS_D", reopened._source_to_channel)

    def test_a_returning_source_gets_its_synced_channel_back(self):
        self._synced()
        _onvif(AC).list_channels()
        back = _onvif(ABC)
        self.assertEqual(back._load_profiles() and back._source_to_channel,
                         {"VS_A": "1", "VS_B": "2", "VS_C": "3"})

    def test_another_recorder_is_not_bound_by_the_pin(self):
        self._synced()
        other = _onvif(AC, url="http://192.0.2.11")
        self.assertEqual([c.channel for c in other.list_channels()], ["1", "2"])

    def test_without_a_synced_inventory_numbering_is_positional(self):
        driver = _onvif(AC)
        self.assertEqual([c.channel for c in driver.list_channels()], ["1", "2"])
        self.assertEqual(driver._source_to_channel, {"VS_A": "1", "VS_C": "2"})

    def test_the_first_synced_inventory_is_kept_for_the_process(self):
        self._synced()
        core._synced_inventory(_onvif(AC))              # a later enumeration does not re-pin
        self.assertEqual(_onvif(ABC)._load_profiles()[2].channel, "3")
        self.assertEqual(_onvif(AC)._load_profiles()[1].channel, "3")

    def test_recovery_inventory_enumeration_pins_what_it_syncs(self):
        driver = _onvif(ABC)
        cloud = SimpleNamespace(call=lambda fn, **kw: {c["channel"]: f"cam-{c['channel']}"
                                                       for c in kw["p_cameras"]})
        with mock.patch.object(core, "open_driver", lambda _cfg: (driver, None)):
            cfg = SimpleNamespace(nvr_url=URL, nvr_username="local-user")
            ids = core._recovery_camera_ids(cfg, {"agent_id": "a", "agent_key": "k"}, cloud, [])
        self.assertEqual(ids, {"1": "cam-1", "2": "cam-2", "3": "cam-3"})
        self.assertEqual([c.channel for c in _onvif(AC).list_channels()], ["1", "3"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
