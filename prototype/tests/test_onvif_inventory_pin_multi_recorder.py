#!/usr/bin/env python3
"""The multi-recorder Agent pins each ONVIF recorder's synced camera numbering too.

5.0.28 pins the source -> channel binding the Agent syncs to the cloud (main() and the recovery
inventory enumeration, via watchlog_agent._synced_inventory), so a video source removed from
the middle of GetProfiles mid-run cannot move its neighbour onto its camera. The multi-recorder
Agent syncs each recorder's cameras elsewhere: the startup probe
(multi_recorder_orchestrator.probe_and_sync_recorder) and the health cycle's late binding
(watchlog_agent._retry_recorder_cloud_inventory). Neither pinned, so on a 5.1.0 site every
ONVIF recorder kept the positional numbering 5.0.28 fixed. Each now pins what WatchLog
accepted for THAT recorder; another recorder is not bound. Hermetic: no recorder, no network.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (keeps Agent state out of the real ProgramData)

import multi_recorder_orchestrator as mro  # noqa: E402
import nvr_health  # noqa: E402
import recorder_runtime  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers import onvif_driver  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402

URL_A, URL_B = "http://192.0.2.10", "http://192.0.2.11"


def _profiles(*sources):
    body = "".join(
        f'<Profiles token="{src}_main"><Name>MediaProfile_Channel{i}_MainStream</Name>'
        f"<VideoSourceConfiguration><SourceToken>{src}</SourceToken></VideoSourceConfiguration>"
        f"</Profiles>"
        for i, src in enumerate(sources, start=1))
    return f"<Envelope>{body}</Envelope>"


ABC = _profiles("VS_A", "VS_B", "VS_C")
AC = _profiles("VS_A", "VS_C")


def _onvif(xml, url):
    driver = OnvifDriver(url, "local-user", "local-password", timeout=1)
    driver.media_service = url + "/onvif/media_service"
    driver._call = lambda *_a, **_k: ET.fromstring(xml)
    driver.capabilities = lambda: None
    return driver


class _Cloud:
    def __init__(self, ok=True):
        self.ok = ok

    def call(self, name, **kw):
        if not self.ok:
            raise RuntimeError("cloud unavailable")
        if name in ("wl_sync_recorder_cameras",):
            return {c["channel"]: f"cam-{c['channel']}" for c in kw["p_cameras"]}
        return {}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    onvif_driver.unpin_inventory()
    monkeypatch.setattr(mro, "_save_observed_identity", lambda *_a, **_k: None)
    yield
    onvif_driver.unpin_inventory()


def _ctx(url):
    cfg = SimpleNamespace(nvr_url=url, nvr_username="local-user", recorder_cloud_id="rec-" + url[-2:])
    return recorder_runtime.RecorderContext(local_id="local-" + url[-2:],
                                            cloud_recorder_id=cfg.recorder_cloud_id,
                                            display_name="Recorder", config=cfg)


def _channels_after_drift(url):
    return [c.channel for c in _onvif(AC, url).list_channels()]


def test_startup_probe_pins_the_inventory_it_syncs():
    result = mro.probe_and_sync_recorder(_Cloud(), {"agent_id": "a", "agent_key": "k"},
                                         _ctx(URL_A), lambda _cfg: (_onvif(ABC, URL_A), None))
    assert result.error is None
    assert [c["channel"] for c in result.channels] == ["1", "2", "3"]
    assert _channels_after_drift(URL_A) == ["1", "3"], "camera C moved onto camera B's slot"
    assert _channels_after_drift(URL_B) == ["1", "2"], "another recorder must not be bound"


def test_startup_probe_does_not_pin_what_the_cloud_never_accepted():
    result = mro.probe_and_sync_recorder(_Cloud(ok=False), {"agent_id": "a", "agent_key": "k"},
                                         _ctx(URL_A), lambda _cfg: (_onvif(ABC, URL_A), None))
    assert result.error
    assert _channels_after_drift(URL_A) == ["1", "2"]


def test_late_binding_in_the_health_cycle_pins_the_inventory_it_syncs():
    driver = _onvif(ABC, URL_A)
    assessment = nvr_health.assess_nvr_health(driver)
    holder: dict = {}
    cfg = SimpleNamespace(recorder_cloud_id="rec-a")
    core._retry_recorder_cloud_inventory(_Cloud(), {"agent_id": "a", "agent_key": "k"},
                                         cfg, holder, assessment, driver)
    assert holder["camera_sync_signature"] == ("1", "2", "3")
    assert _channels_after_drift(URL_A) == ["1", "3"]
    assert _channels_after_drift(URL_B) == ["1", "2"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
