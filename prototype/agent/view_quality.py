#!/usr/bin/env python3
"""Camera view quality (Part A of docs/design/2026-11-06-sharper-views-and-known-people.md).

Field finding, 6 Nov 2026: Al-Khalid's Dahua XVR returned 352 x 288 stills (snapshot.cgi with no
stream choice gives the recorder's snapshot setting, CIF by default) and HASCO's Hikvision NVR
704 x 576. The cameras record far more than that.

Two modes, chosen per site and kept as local agent state:

  standard  today's still, exactly as before (the default);
  sharp     the recorder's main-stream still, capped at SHARP_MAX_WIDTH so each view stays about
            100-150 KB on the site uplink and in storage.

The mode is only ever set by the Site Control READ action ``probe_view_quality`` with
``apply: true``, and sharp is accepted only when that probe proved a larger still actually comes
back. Nothing here writes to the recorder: sharp changes which still the Agent asks for.

The five-minute camera checks and camera-health stills stay standard (``snapshot(...,
quality="standard")``); alarm and incident stills follow the site's mode.
"""
from __future__ import annotations

import inspect
import json
import os
import struct
import tempfile
import time
from pathlib import Path

MODES = ("standard", "sharp")
SHARP_MAX_WIDTH = 1280
SHARP_JPEG_QUALITY = 82
_RELOAD_SECONDS = 60.0
_cache: dict = {"at": 0.0, "mode": "standard"}


def _path() -> Path:
    override = os.environ.get("WATCHLOG_VIEW_QUALITY_FILE")
    if override:
        return Path(override)
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog" / "view_quality.json"


def current_mode(now: float | None = None) -> str:
    """The site's mode, re-read from disk at most once a minute. Any problem -> standard."""
    t = time.monotonic() if now is None else now
    if t - _cache["at"] < _RELOAD_SECONDS and _cache["at"] > 0:
        return _cache["mode"]
    mode = "standard"
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if data.get("mode") in MODES:
            mode = data["mode"]
    except Exception:                                   # noqa: BLE001 — missing/corrupt = standard
        mode = "standard"
    _cache.update(at=t, mode=mode)
    return mode


def save_mode(mode: str, summary: dict | None = None) -> str:
    """Persist the site's mode atomically and refresh the cache. Unknown modes -> standard."""
    mode = mode if mode in MODES else "standard"
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"mode": mode, "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "probe": summary or {}}, indent=1)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".view_quality.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _cache.update(at=time.monotonic(), mode=mode)
    return mode


def resolve(quality: str | None) -> str:
    """An explicit quality wins; otherwise the site's mode."""
    if quality in MODES:
        return quality
    return current_mode()


def jpeg_size(data: bytes | None) -> tuple[int, int] | None:
    """(width, height) from a JPEG's start-of-frame marker, without decoding it."""
    if not data or data[:2] != b"\xff\xd8":
        return None
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return (w, h)
        seg = struct.unpack(">H", data[i + 2:i + 4])[0]
        if seg < 2:
            return None
        i += 2 + seg
    return None


_resizer = None


def register_resizer(fn) -> None:
    """The Agent registers still_resize.cap_jpeg at start. The drivers are shared with the light
    Setup UI, which must not bundle Pillow (test_setup_import_graph), so this module never
    imports it; without a registered resizer a sharp still is kept as it came."""
    global _resizer
    _resizer = fn


def cap(data: bytes) -> bytes:
    """Shrink a sharp still to SHARP_MAX_WIDTH when the Agent registered a resizer. Fails open."""
    size = jpeg_size(data)
    if _resizer is None or not size or size[0] <= SHARP_MAX_WIDTH:
        return data
    try:
        out = _resizer(data, SHARP_MAX_WIDTH, SHARP_JPEG_QUALITY)
        return out if out and out[:2] == b"\xff\xd8" else data
    except Exception:                                       # noqa: BLE001
        return data


def choose_mode(probe: dict | None) -> str:
    """sharp only when, on at least one camera, the sharp still came back and is wider than today's."""
    for row in (probe or {}).get("channels") or []:
        today = (row.get("default") or {}).get("size")
        sharp = (row.get("sharp") or {}).get("size")
        if sharp and (not today or sharp[0] > today[0]):
            return "sharp"
    return "standard"


def snapshot(driver, channel: str, quality: str | None = None):
    """driver.get_snapshot with a quality when the driver understands one (mock/ONVIF do not)."""
    if quality is None:
        return driver.get_snapshot(channel)
    try:
        accepts = "quality" in inspect.signature(driver.get_snapshot).parameters
    except (TypeError, ValueError):
        accepts = False
    return driver.get_snapshot(channel, quality=quality) if accepts else driver.get_snapshot(channel)
