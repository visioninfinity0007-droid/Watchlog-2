#!/usr/bin/env python3
"""Sharper camera views (Part A, 6 Nov 2026): view_quality.py, the drivers' sharp stills and the
read-only Site Control probe.

Field: Al-Khalid's Dahua XVR returned 352 x 288 stills (snapshot.cgi without subtype=0) and HASCO's
Hikvision NVR 704 x 576. Proves: JPEG sizes are read without decoding; big stills are capped;
sharp is chosen only when a larger still was proven; a sharp request that fails falls back to
today's still; the five-minute checks stay standard; the probe never writes to the recorder.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
import requests

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import view_quality  # noqa: E402
import still_resize  # noqa: E402
import site_control  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

PIL = pytest.importorskip("PIL.Image")


def jpeg(w: int, h: int) -> bytes:
    out = io.BytesIO()
    PIL.new("RGB", (w, h), (90, 120, 150)).save(out, format="JPEG", quality=80)
    return out.getvalue()


@pytest.fixture(autouse=True)
def mode_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHLOG_VIEW_QUALITY_FILE", str(tmp_path / "view_quality.json"))
    view_quality._cache.update(at=0.0, mode="standard")
    view_quality.register_resizer(still_resize.cap_jpeg)
    yield
    view_quality.register_resizer(None)


class Resp:
    def __init__(self, status=200, content=b"", text=""):
        self.status_code = status
        self.content = content
        self.text = text
        self.headers = {}

    def close(self):
        pass


class Session:
    """Answers by URL fragment; records every URL asked for."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []
        self.auth = None

    def get(self, url, **kw):
        self.urls.append(url)
        for frag, resp in self.routes:
            if frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return Resp(404)

    def request(self, method, url, **kw):
        assert method == "GET", "the probe and stills never write to the recorder"
        return self.get(url, **kw)


def dahua(routes):
    d = DahuaDriver.__new__(DahuaDriver)
    d.base_url = "http://nvr"
    d.timeout = 5
    d.username = d.password = "x"
    d.s = Session(routes)
    return d


def hik(routes):
    d = HikvisionDriver.__new__(HikvisionDriver)
    d.base_url = "http://nvr"
    d.timeout = 5
    d.username = d.password = "x"
    d.s = Session(routes)
    import threading
    d._lock_obj = threading.RLock()
    d._lock = lambda: d._lock_obj
    return d


def test_jpeg_size_and_cap():
    assert view_quality.jpeg_size(jpeg(352, 288)) == (352, 288)
    assert view_quality.jpeg_size(b"not a jpeg") is None
    big = jpeg(1920, 1080)
    assert view_quality.jpeg_size(view_quality.cap(big)) == (1280, 720)
    keep = jpeg(704, 576)
    assert view_quality.cap(keep) is keep
    view_quality.register_resizer(None)
    assert view_quality.cap(big) is big, "without the Agent's resizer (Setup UI) the still is kept"


def test_agent_registers_the_resizer_and_setup_never_reaches_pillow():
    src = (AGENT / "analytics_agent.py").read_text(encoding="utf-8")
    assert "view_quality.register_resizer(still_resize.cap_jpeg)" in src
    assert "PIL" not in (AGENT / "view_quality.py").read_text(encoding="utf-8")


def test_mode_is_standard_until_saved_and_survives_reload():
    assert view_quality.current_mode() == "standard"
    view_quality.save_mode("sharp", {"proven": "sharp"})
    view_quality._cache.update(at=0.0)
    assert view_quality.current_mode() == "sharp"
    view_quality.save_mode("bogus")
    view_quality._cache.update(at=0.0)
    assert view_quality.current_mode() == "standard"


def test_choose_mode_needs_a_proven_larger_still():
    small = {"size": (352, 288), "bytes": 9000}
    assert view_quality.choose_mode({"channels": [{"default": small, "sharp": {"size": (1920, 1080)}}]}) == "sharp"
    assert view_quality.choose_mode({"channels": [{"default": small, "sharp": {"size": (352, 288)}}]}) == "standard"
    assert view_quality.choose_mode({"channels": [{"default": small, "sharp": {"size": None}}]}) == "standard"
    assert view_quality.choose_mode(None) == "standard"


def test_dahua_sharp_asks_main_stream_and_caps():
    d = dahua([("subtype=0", Resp(200, jpeg(1920, 1080))), ("snapshot.cgi", Resp(200, jpeg(352, 288)))])
    img = d.get_snapshot("3", quality="sharp")
    assert view_quality.jpeg_size(img) == (1280, 720)
    assert d.s.urls[0].endswith("snapshot.cgi?channel=3&subtype=0")


def test_dahua_sharp_falls_back_to_todays_still():
    d = dahua([("subtype=0", Resp(400, b"", "Error")), ("snapshot.cgi", Resp(200, jpeg(352, 288)))])
    assert view_quality.jpeg_size(d.get_snapshot("3", quality="sharp")) == (352, 288)
    d = dahua([("subtype=0", requests.ConnectionError("x")), ("snapshot.cgi", Resp(200, jpeg(352, 288)))])
    assert view_quality.jpeg_size(d.get_snapshot("3", quality="sharp")) == (352, 288)


def test_dahua_standard_is_unchanged_and_follows_site_mode():
    d = dahua([("subtype=0", Resp(200, jpeg(1920, 1080))), ("snapshot.cgi", Resp(200, jpeg(352, 288)))])
    assert view_quality.jpeg_size(d.get_snapshot("1")) == (352, 288)
    assert d.s.urls == ["http://nvr/cgi-bin/snapshot.cgi?channel=1"]
    view_quality.save_mode("sharp")
    assert view_quality.jpeg_size(d.get_snapshot("1")) == (1280, 720)
    assert view_quality.jpeg_size(d.get_snapshot("1", quality="standard")) == (352, 288)


def test_hikvision_sharp_uses_main_stream_size_then_falls_back():
    xml = (b'<StreamingChannel xmlns="http://www.hikvision.com/ver20/XMLSchema"><Video>'
           b'<videoResolutionWidth>1920</videoResolutionWidth><videoResolutionHeight>1080</videoResolutionHeight>'
           b'</Video></StreamingChannel>')
    routes = [("picture?videoResolutionWidth=1920", Resp(200, jpeg(1920, 1080))),
              ("/picture", Resp(200, jpeg(704, 576))),
              ("/Streaming/channels/401", Resp(200, xml, xml.decode()))]
    d = hik(routes)
    assert view_quality.jpeg_size(d.get_snapshot("4", quality="sharp")) == (1280, 720)
    assert view_quality.jpeg_size(d.get_snapshot("4")) == (704, 576)
    d = hik([("picture?videoResolutionWidth", Resp(400)), ("/picture", Resp(200, jpeg(704, 576))),
             ("/Streaming/channels/401", Resp(200, xml, xml.decode()))])
    assert view_quality.jpeg_size(d.get_snapshot("4", quality="sharp")) == (704, 576)


def test_probe_reports_sizes_and_apply_only_switches_when_proven():
    enc = ("table.Encode[0].MainFormat[0].Video.resolution=1080N\n"
           "table.Encode[0].MainFormat[0].Video.Width=960\ntable.Encode[0].MainFormat[0].Video.Height=1080\n")
    d = dahua([("name=Encode", Resp(200, b"", enc)), ("subtype=0", Resp(200, jpeg(960, 1080))),
               ("subtype=1", Resp(200, jpeg(352, 288))), ("snapshot.cgi", Resp(200, jpeg(352, 288)))])
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"], "apply": True})
    assert out["ok"] is True, out
    row = out["data"]["probe"]["channels"][0]
    assert row["default"]["size"] == (352, 288) and row["sharp"]["size"] == (960, 1080)
    assert row["streams"]["MainFormat"]["resolution"] == "1080N"
    assert out["data"]["proven"] == "sharp" and out["data"]["mode"] == "sharp"

    view_quality.save_mode("standard")
    d = dahua([("name=Encode", Resp(200, b"", "")), ("subtype=0", Resp(400)), ("snapshot.cgi", Resp(200, jpeg(352, 288)))])
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"], "apply": True, "mode": "sharp"})
    assert out["data"]["proven"] == "standard" and out["data"]["mode"] == "standard", "never sharp without proof"
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"]})
    assert out["data"]["mode"] == "standard", "without apply the mode is only reported"


def test_five_minute_checks_stay_standard():
    view_quality.save_mode("sharp")
    d = dahua([("subtype=0", Resp(200, jpeg(1920, 1080))), ("snapshot.cgi", Resp(200, jpeg(352, 288)))])
    assert view_quality.jpeg_size(view_quality.snapshot(d, "2", quality="standard")) == (352, 288)

    class Plain:
        def get_snapshot(self, channel):
            return b"ok"
    assert view_quality.snapshot(Plain(), "2", quality="standard") == b"ok"
    for f in ("periodic_stills.py", "camera_health.py"):
        src = (AGENT / f).read_text(encoding="utf-8")
        assert 'view_quality.snapshot(' in src and 'quality="standard"' in src, f


def test_probe_is_in_the_read_catalog():
    assert "probe_view_quality" in site_control.READ_ACTIONS
    assert "probe_view_quality" not in site_control.WRITE_ACTIONS


# --- 5.1.13: raise the Dahua snapshot size through the safe write ---------------------------

class DahuaRecorder:
    """A Dahua XVR that serves every still at its SnapFormat size and ignores subtype=0, like
    Al-Khalid's DH-XVR1B08-I. setConfig changes its state unless it refuses that size."""

    def __init__(self, snap=(352, 288), main=(960, 576), refuse=False, ignore=False):
        self.snap = {0: snap, 1: snap}
        self.main = main
        self.refuse = refuse          # answers an error
        self.ignore = ignore          # answers OK but keeps its old value
        self.writes = []
        self.auth = None

    def _encode(self):
        lines = [f"table.Encode[0].MainFormat[0].Video.Width={self.main[0]}",
                 f"table.Encode[0].MainFormat[0].Video.Height={self.main[1]}"]
        for i, (w, h) in self.snap.items():
            lines += [f"table.Encode[0].SnapFormat[{i}].Video.Width={w}",
                      f"table.Encode[0].SnapFormat[{i}].Video.Height={h}"]
        return "\n".join(lines) + "\n"

    def get(self, url, **kw):
        import re as _re
        if "action=setConfig" in url:
            self.writes.append(url)
            if self.refuse:
                return Resp(400, b"", "Error")
            if not self.ignore:
                for i, key, val in _re.findall(r"Encode\[0\]\.SnapFormat\[(\d)\]\.Video\.(Width|Height)=(\d+)", url):
                    w, h = self.snap[int(i)]
                    self.snap[int(i)] = (int(val), h) if key == "Width" else (w, int(val))
            return Resp(200, b"", "OK")
        if "name=Encode" in url:
            return Resp(200, b"", self._encode())
        if "snapshot.cgi" in url:
            return Resp(200, jpeg(*self.snap[0]))
        return Resp(404)

    def request(self, method, url, **kw):
        return self.get(url, **kw)


def dahua_recorder(**kw):
    d = DahuaDriver.__new__(DahuaDriver)
    d.base_url = "http://nvr"
    d.timeout = 5
    d.username = d.password = "x"
    d.s = DahuaRecorder(**kw)
    return d


def test_raise_snapshot_sets_main_size_verifies_and_views_get_sharp():
    d = dahua_recorder()
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"], "apply": True, "raise_snapshot": True})
    assert out["ok"] is True, out
    r = out["data"]["snapshot_raised"][0]
    assert r["changed"] is True and r["verified"] is True, r
    assert r["before"] == {"snap0": "352x288", "snap1": "352x288"} and r["after"] == {"snap0": "960x576", "snap1": "960x576"}
    assert out["data"]["probe"]["channels"][0]["default"]["size"] == (960, 576), "today's still is now the main size"
    assert view_quality.jpeg_size(d.get_snapshot("1")) == (960, 576)


def test_raise_snapshot_rolls_back_when_the_recorder_does_not_take_it():
    d = dahua_recorder(ignore=True)
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"], "raise_snapshot": True})
    r = out["data"]["snapshot_raised"][0]
    assert r["ok"] is False and r["verified"] is False and r["rolled_back"] is True, r
    assert d.s.snap[0] == (352, 288)
    d = dahua_recorder(refuse=True)
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"], "raise_snapshot": True})
    r = out["data"]["snapshot_raised"][0]
    assert r["ok"] is False and d.s.snap[0] == (352, 288), r


def test_raise_snapshot_leaves_a_matching_still_and_never_runs_unasked():
    d = dahua_recorder(snap=(960, 576))
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"], "raise_snapshot": True})
    assert out["data"]["snapshot_raised"][0]["changed"] is False and d.s.writes == []
    d = dahua_recorder()
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"], "apply": True})
    assert d.s.writes == [] and "snapshot_raised" not in out["data"], "without raise_snapshot the probe never writes"


def test_raise_snapshot_skips_huge_main_streams_and_drivers_without_it():
    d = dahua_recorder(main=(3840, 2160))
    out = site_control.execute_read(d, "probe_view_quality", {"channels": ["1"], "raise_snapshot": True})
    assert out["data"]["snapshot_raised"][0]["changed"] is False and d.s.writes == []
    h = hik([("Streaming/channels/101/picture", Resp(200, jpeg(1920, 1080))), ("ISAPI/Streaming/channels", Resp(200, jpeg(704, 576)))])
    out = site_control.execute_read(h, "probe_view_quality", {"channels": ["1"], "raise_snapshot": True})
    assert out["data"]["snapshot_raised"] == [], "Hikvision already gives sharp stills; nothing to write"
    assert "configure_snapshot" not in site_control.WRITE_ACTIONS, "local only: the cloud catalog cannot ask for it"
