#!/usr/bin/env python3
"""Historical AI recovery — run WatchLog's on-site detector over RECOVERED NVR footage (§1 deep).

Recorder-native event replay (backfill.backfill_events) recovers the NVR's OWN recorded events.
This module goes further, delivering WatchLog's required recovery promise: for each recorded
segment in the missed interval, obtain a representative HISTORICAL frame, run the SAME packaged
detector used for live monitoring, and emit RECOVERED intelligence — with the segment's historical
timestamp, a representative snapshot where retrievable, detections/labels, and recovered provenance.

Honesty rules (never fabricate):
  * source='recovered' and recovered=True — never presented as a live observation;
  * the event timestamp is the FOOTAGE time (segment start), never the recovery time;
  * no frame source (driver cannot serve a recorded frame and no decoder is available) -> the
    segment yields status 'no_frame' and NO derived event (recorder-native replay still stands);
  * detector unavailable -> the frame is kept without labels, exactly like the live fail-open path;
  * detector says 'discard' (junk frame, no objects) -> a 'quiet' segment, no event emitted.

Pure orchestration: driver, detector, frame provider and sink are injected, so the whole pipeline
(locate recording -> bounded retrieval -> frame -> AI -> recovered intelligence + snapshot) is
fully testable with fakes and no hardware or video codec.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

try:
    import backfill
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import backfill

SUPPORTED, UNSUPPORTED, UNKNOWN = backfill.SUPPORTED, backfill.UNSUPPORTED, backfill.UNKNOWN
RECOVERED_SOURCE = "recovered"                        # AI over recovered footage (stronger than event replay)
PROVENANCE_LINE = "Recovered from recorder archive (WatchLog analysis of historical footage)"
DEFAULT_FRAME_CLIP_SECONDS = 6                        # bounded clip length to sample one frame from
DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 300              # restore one visual checkpoint every 5 min


def _as_dt(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _iso(v) -> str:
    return _as_dt(v).astimezone(timezone.utc).isoformat()


def _ffmpeg_exe() -> str | None:
    """Locate the bundled FFmpeg binary used for historical-frame recovery."""
    # imageio-ffmpeg ships a known-good static binary and exposes its resolved path.
    try:
        import imageio_ffmpeg  # type: ignore
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.exists(path):
            return path
    except Exception:
        pass

    # Development/support fallback only. Production release tests require the
    # bundled imageio-ffmpeg path, so PATH is not the release dependency.
    path = shutil.which("ffmpeg")
    return path if path and os.path.exists(path) else None


def _ffmpeg_first_frame(clip_bytes: bytes) -> bytes | None:
    """Decode one representative JPEG with bounded CPU/time/memory behavior."""
    exe = _ffmpeg_exe()
    if not exe or not clip_bytes:
        return None

    src = out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(clip_bytes)
            src = tmp.name
        out = src + ".jpg"
        cmd = [
            exe, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-y", "-i", src,
            "-frames:v", "1",
            "-vf", "scale=1280:-2:force_original_aspect_ratio=decrease",
            "-q:v", "4",
            out,
        ]
        cp = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=12,
            check=False,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
        if cp.returncode != 0 or not os.path.exists(out):
            return None
        data = open(out, "rb").read(3 * 1024 * 1024 + 1)
        if len(data) > 3 * 1024 * 1024 or not data.startswith(b"\xff\xd8\xff"):
            return None
        return data
    except Exception:
        return None
    finally:
        for path in (src, out):
            if path:
                try:
                    os.unlink(path)
                except Exception:
                    pass


def _opencv_first_frame(clip_bytes):  # dev-only fallback; not required by release
    try:
        import cv2  # type: ignore
    except Exception:
        return None
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(clip_bytes)
            path = tmp.name
        cap = cv2.VideoCapture(path)
        try:
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
            ok, buf = cv2.imencode(".jpg", frame)
            return buf.tobytes() if ok else None
        finally:
            cap.release()
    except Exception:
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass


def decode_jpeg_frame(clip_bytes, *, decoder=None):
    """Return a representative JPEG frame from recorder-native clip bytes.

    Production order: injected test decoder -> bundled FFmpeg -> optional OpenCV
    dev fallback. Failure is always honest None; no fabricated frame.
    """
    if not clip_bytes:
        return None
    if decoder is not None:
        try:
            return decoder(clip_bytes)
        except Exception:
            return None
    return _ffmpeg_first_frame(clip_bytes) or _opencv_first_frame(clip_bytes)


def decoder_selftest() -> dict:
    """Prove the packaged FFmpeg can both create and decode a tiny video."""
    exe = _ffmpeg_exe()
    if not exe:
        return {"ok": False, "reason": "bundled ffmpeg not found"}
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        cp = subprocess.run([
            exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:d=0.2",
            "-frames:v", "2", "-pix_fmt", "yuv420p", path,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=12, check=False,
           creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0))
        if cp.returncode != 0:
            return {"ok": False, "reason": "ffmpeg synth failed"}
        raw = open(path, "rb").read()
        frame = _ffmpeg_first_frame(raw)
        return {"ok": bool(frame and frame.startswith(b"\xff\xd8\xff")),
                "reason": "" if frame else "ffmpeg decode failed"}
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__}
    finally:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass


def recovered_frame(driver, channel, ts, *, decoder=None, clip_seconds=DEFAULT_FRAME_CLIP_SECONDS):
    """Obtain a representative historical frame (JPEG) for ``channel`` at footage time ``ts``.

    Order of preference, honest about capability:
      1. a driver that can serve a recorded frame directly (``get_recorded_frame``);
      2. otherwise a bounded recovered clip (``get_recorded_segment`` / ``get_clip``) decoded to one
         frame via ``decode_jpeg_frame``.
    Returns JPEG bytes, or None when no frame source is available.
    """
    start = _as_dt(ts)
    end = start + timedelta(seconds=max(1, int(clip_seconds)))

    getter = getattr(driver, "get_recorded_frame", None)
    if callable(getter):
        try:
            frame = getter(channel, start)
            if frame:
                return frame
        except Exception:  # noqa: BLE001 — fall through to clip decode
            pass

    clip = None
    seg_getter = getattr(driver, "get_recorded_segment", None)
    if callable(seg_getter):
        try:
            res = seg_getter(channel, start, end) or {}
            if isinstance(res, dict):
                clip = res.get("bytes") if res.get("status") == SUPPORTED else None
            else:
                clip = res
        except Exception:  # noqa: BLE001
            clip = None
    if not clip:
        clip_getter = getattr(driver, "get_clip", None)
        if callable(clip_getter):
            try:
                clip = clip_getter(channel, start, end)
            except Exception:  # noqa: BLE001
                clip = None
    return decode_jpeg_frame(clip, decoder=decoder) if clip else None


def media_kind(head: bytes) -> str:
    """Classify recorder media by magic bytes only (never inspects/logs content). Dahua archive is
    DHAV/DAV; some units serve MP4 or JPEG stills."""
    if not head:
        return "empty"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"DHAV" or b"DHAV" in head[:16]:
        return "dav"
    return "unknown"


def inspect_and_decode(driver, channel, ts, *, decoder=None, clip_seconds=DEFAULT_FRAME_CLIP_SECONDS):
    """P2 media path: fetch a bounded historical clip (or a recorder-native frame), record safe
    DIAGNOSTICS (media size + magic + selected decoder + result — never image contents or secrets),
    and try to decode one representative JPEG frame. Returns (frame_jpeg | None, diagnostics).
    Never raises; a missing frame source or codec is an honest 'not decoded'.
    """
    diag = {"media_size": 0, "kind": "none", "magic_hex": "", "decoder": "none", "decoded": False}
    start = _as_dt(ts)
    end = start + timedelta(seconds=max(1, int(clip_seconds)))

    getter = getattr(driver, "get_recorded_frame", None)
    if callable(getter):
        try:
            frame = getter(channel, start)
            if frame:
                diag.update(media_size=len(frame), kind=media_kind(frame[:16]),
                            magic_hex=frame[:8].hex(), decoder="recorder_frame", decoded=True)
                return frame, diag
        except Exception:  # noqa: BLE001
            pass

    clip = None
    seg = getattr(driver, "get_recorded_segment", None)
    if callable(seg):
        try:
            res = seg(channel, start, end) or {}
            if isinstance(res, dict):
                clip = res.get("bytes") if res.get("status") == SUPPORTED else None
            else:
                clip = res
        except Exception:  # noqa: BLE001
            clip = None
    if not clip:
        cg = getattr(driver, "get_clip", None)
        if callable(cg):
            try:
                clip = cg(channel, start, end)
            except Exception:  # noqa: BLE001
                clip = None
    if not clip:
        return None, diag

    diag.update(media_size=len(clip), magic_hex=clip[:8].hex(), kind=media_kind(clip[:16]))
    frame = decode_jpeg_frame(clip, decoder=decoder)
    diag["decoder"] = "injected" if decoder else ("ffmpeg" if _ffmpeg_exe() else "opencv")
    diag["decoded"] = bool(frame)
    return frame, diag


def analyze_segment(detector, frame_jpeg, *, channel, ts, device_event_id=None, segment=None,
                    now=None):
    """Build a recovered visual event from one historical frame.

    Every successfully decoded historical frame is preserved. Interesting frames
    become recovered_activity; detector-discarded/quiet frames become
    recovered_snapshot so the missed visual timeline is actually backfilled.
    """
    if not frame_jpeg:
        return "no_frame", None

    keep, detections = (True, None)
    detector_name = None
    if detector is not None:
        keep, detections = detector.classify_event(frame_jpeg)
        detector_name = getattr(detector, "model_name", None)

    objects = [d.as_dict() for d in (detections or [])]
    device_ts = _iso(ts)
    activity = keep is not False and bool(objects)
    event_type = "recovered_activity" if activity else "recovered_snapshot"
    dev_id = device_event_id or f"{event_type}:{channel}:{device_ts}"
    event = {
        "channel": str(channel),
        "event_type": event_type,
        "device_event_id": dev_id,
        "device_ts": device_ts,
        "agent_ts": _iso(now or datetime.now(timezone.utc)),
        "snapshot_b64": base64.b64encode(frame_jpeg).decode("ascii"),
        "payload": {
            "objects": objects,
            "detector": detector_name,
            "source": RECOVERED_SOURCE,
            "recovered": True,
            "provenance": PROVENANCE_LINE,
            "segment": segment,
            "kind": "recovered_intelligence" if activity else "recovered_snapshot",
            "activity_detected": activity,
        },
        "source": RECOVERED_SOURCE,
        "recovered": True,
        "provenance": PROVENANCE_LINE,
    }
    return ("recovered" if activity else "snapshot"), event


def _segment_ref(raw: dict, channel, fallback_ts) -> str:
    """Stable id of one recorded segment, for dedupe keys and recovered event ids. A recorder
    playback URI or file path carries the recorder's address and storage layout, so only a
    digest of it leaves the site."""
    ref = str(raw.get("device_event_id") or f"{channel}:{fallback_ts}")
    if "://" in ref or "/" in ref:
        return "seg-" + hashlib.sha256(ref.encode("utf-8")).hexdigest()[:20]
    return ref


def _segment_sample_times(segment: dict, fallback_ts, interval_seconds: int):
    """Yield bounded historical sample timestamps across one recorded segment."""
    start = _as_dt(segment.get("start") or fallback_ts)
    end_raw = segment.get("end")
    if not end_raw:
        return [start]
    end = _as_dt(end_raw)
    if end <= start:
        return [start]
    step = max(30, int(interval_seconds))
    out = []
    cur = start
    while cur < end:
        out.append(cur)
        cur += timedelta(seconds=step)
    return out or [start]


def backfill_intelligence(driver, detector, channel, start, end, *, seen=None, on_event=None,
                          frame_provider=None, decoder=None, window_seconds=3600, page_limit=500,
                          max_frames=None, clip_seconds=DEFAULT_FRAME_CLIP_SECONDS,
                          snapshot_interval_seconds=DEFAULT_SNAPSHOT_INTERVAL_SECONDS) -> dict:
    """Recover WatchLog intelligence from recorded FOOTAGE for ``channel`` over [start, end).

    Enumerates recorded segments (bounded, cursored, deduped via ``seen``), retrieves a representative
    historical frame per new segment, runs the detector, and emits recovered-intelligence events via
    ``on_event``. Returns a truthful summary. Never blocks or fabricates; unsupported archive =>
    status is reported verbatim and nothing is recovered.
    """
    cap = (driver.historical_capability() or {}).get("segments", UNKNOWN)
    if cap != SUPPORTED:
        return {"status": cap, "recovered": 0, "activity": 0, "snapshots": 0,
                "frames": 0, "no_frame": 0, "duplicates": 0,
                "provenance": RECOVERED_SOURCE,
                "reason": f"archive segments {cap} on this recorder"}

    provider = frame_provider or (lambda drv, ch, ts: recovered_frame(
        drv, ch, ts, decoder=decoder, clip_seconds=clip_seconds))
    seen = seen if seen is not None else set()
    recovered = activity = snapshots = frames = no_frame = duplicates = attempted = 0

    for w_start, w_end in backfill._windows(start, end, window_seconds):
        cursor = None
        while True:
            res = driver.enumerate_historical_events(
                channel, w_start, w_end, cursor=cursor, limit=page_limit) or {}
            if res.get("status") != SUPPORTED:
                return {"status": res.get("status", UNKNOWN), "recovered": recovered,
                        "activity": activity, "snapshots": snapshots, "frames": frames,
                        "no_frame": no_frame, "duplicates": duplicates,
                        "provenance": RECOVERED_SOURCE,
                        "reason": "driver changed status mid-scan"}

            for raw in res.get("events", []) or []:
                seg = raw.get("segment") or {}
                fallback_ts = seg.get("start") or raw.get("ts")
                base_id = _segment_ref(raw, channel, fallback_ts)
                # Only the footage window travels with the event, never the recorder URI/path.
                seg_window = {"start": seg.get("start"), "end": seg.get("end")}
                for sample_ts in _segment_sample_times(
                        seg, fallback_ts, snapshot_interval_seconds):
                    sample_iso = _iso(sample_ts)
                    key = f"ai:{base_id}:{sample_iso}"
                    if key in seen:
                        duplicates += 1
                        continue
                    seen.add(key)

                    attempted += 1
                    frame = provider(driver, channel, sample_ts)
                    status, event = analyze_segment(
                        detector, frame, channel=channel, ts=sample_ts,
                        device_event_id=f"{base_id}:{sample_iso}", segment=seg_window)
                    if status == "no_frame":
                        no_frame += 1
                        continue

                    frames += 1
                    recovered += 1
                    if status == "recovered":
                        activity += 1
                    else:
                        snapshots += 1
                    if on_event:
                        on_event(event)

                    if max_frames is not None and frames >= max_frames:
                        return {"status": SUPPORTED, "recovered": recovered,
                                "activity": activity, "snapshots": snapshots,
                                "frames": frames, "no_frame": no_frame,
                                "attempted": attempted, "duplicates": duplicates,
                                "provenance": RECOVERED_SOURCE,
                                "stopped_at_limit": True}

            cursor = res.get("next_cursor")
            if not cursor:
                break

    final_status = SUPPORTED
    reason = None
    if attempted > 0 and frames == 0:
        final_status = UNKNOWN
        reason = "recorded segments were found but no historical frame could be decoded"
    return {"status": final_status, "recovered": recovered, "activity": activity,
            "snapshots": snapshots, "frames": frames, "no_frame": no_frame,
            "attempted": attempted, "duplicates": duplicates,
            "provenance": RECOVERED_SOURCE, "reason": reason}



__all__ = ["decode_jpeg_frame", "recovered_frame", "media_kind", "inspect_and_decode",
           "analyze_segment", "backfill_intelligence", "RECOVERED_SOURCE", "PROVENANCE_LINE",
           "DEFAULT_FRAME_CLIP_SECONDS", "DEFAULT_SNAPSHOT_INTERVAL_SECONDS", "decoder_selftest"]
