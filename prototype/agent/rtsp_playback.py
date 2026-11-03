"""Exact-window recorded footage over a recorder's RTSP playback service.

Both supported recorder families serve their archive as an RTSP playback stream bounded by a start
and end time (Dahua ``/cam/playback``, Hikvision ``/Streaming/tracks/N01``). Unlike the HTTP export
endpoints, which on some firmware answer empty (Dahua XVR ``loadfile.cgi``) or send a whole recording
file instead of the window (Hikvision ``ContentMgmt/download``), a playback stream carries only the
requested window. The bundled FFmpeg reads it and stream-copies the video into MP4: nothing is
re-encoded and nothing on the recorder changes.

Safety / truth rules:
* read-only: an RTSP PLAY of recorded footage;
* the recorder credentials go to FFmpeg in the URL only and never appear in an error, a log line or
  the returned bytes; FFmpeg's own text is classified, never passed on;
* bounded by the caller's deadline and by ``max_bytes`` (FFmpeg stops writing at the size cap);
* an empty or near-empty result is a failure, never a clip.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable

DEFAULT_PORT = 554
SOCKET_TIMEOUT_SECONDS = 10          # FFmpeg's RTSP socket I/O timeout
# Playback is paced in real time and must wait for the first key frame, so a window needs about
# its own length plus this much to arrive.
STARTUP_SECONDS = 12
MIN_CLIP_BYTES = 4 * 1024            # less than this is a header with no footage


class PlaybackFailed(DriverError):
    """The playback stream gave no usable footage. Retryable; says nothing about capability."""


class PlaybackTooLarge(PlaybackFailed):
    """The window's footage reached the byte cap."""


class PlaybackTimedOut(PlaybackFailed):
    """The time budget ran out before the window arrived."""


class PlaybackAuthFailed(PlaybackFailed, NvrAuthFailed):
    """The recorder refused the login for playback."""


class PlaybackUnreachable(PlaybackFailed, NvrUnreachable):
    """The recorder's RTSP service did not answer."""


def url(host: str, port: int, path: str, username: str = "", password: str = "") -> str:
    """An RTSP URL with the login embedded the way FFmpeg reads it."""
    login = ""
    if username:
        login = quote(str(username), safe="") + ":" + quote(str(password or ""), safe="") + "@"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"          # IPv6 literal
    return f"rtsp://{login}{host}:{int(port or DEFAULT_PORT)}{path}"


def _ffmpeg() -> str | None:
    """The bundled FFmpeg, located by recovery_ai, which the Agent runtime always has loaded
    (release_agent -> incident_evidence -> recovery_ai). It is NOT imported here: Setup reaches the
    archive modules for its archive proof (search only, never playback), and importing recovery_ai
    would pull the analytics stack into Setup (test_setup_import_graph). Without it there is no
    playback path."""
    module = sys.modules.get("recovery_ai")
    if module is None:
        return None
    try:
        return module._ffmpeg_exe()
    except Exception:  # noqa: BLE001 — no bundled tool means no playback path
        return None


def _classify(stderr: str) -> PlaybackFailed:
    """FFmpeg's error text -> a typed, customer-safe failure. The text itself never leaves here:
    FFmpeg can print the URL, and with it the recorder address and login."""
    low = (stderr or "").lower()
    if "401" in low or "unauthorized" in low or "403" in low or "forbidden" in low:
        return PlaybackAuthFailed("The recorder refused the WatchLog login for recorded playback.")
    if ("connection refused" in low or "timed out" in low or "timeout" in low
            or "no route to host" in low or "network is unreachable" in low
            or "connection reset" in low):
        return PlaybackUnreachable("The recorder's playback service did not answer.")
    if "404" in low or "not found" in low or "454" in low or "457" in low:
        return PlaybackFailed("The recorder has no playback footage for this window.")
    return PlaybackFailed("The recorder's playback stream gave no usable footage.")


def fetch(stream_url: str, seconds: float, *, deadline: float, max_bytes: int) -> bytes:
    """Read ``seconds`` of an RTSP playback stream into MP4 bytes within ``deadline``
    (a ``time.monotonic()`` value)."""
    exe = _ffmpeg()
    if not exe:
        raise PlaybackFailed("This site computer cannot prepare recorded footage yet.")
    seconds = max(1.0, float(seconds))
    remaining = deadline - time.monotonic()
    if remaining < seconds + 2:
        raise PlaybackTimedOut("The recorder did not finish sending the footage in time. Request it again.")
    out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            out = tmp.name
        command = [
            exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-rtsp_transport", "tcp",
            "-timeout", str(SOCKET_TIMEOUT_SECONDS * 1_000_000),
            "-i", stream_url,
            "-t", f"{seconds:.3f}",
            "-map", "0:v:0", "-c:v", "copy", "-an",
            "-fs", str(int(max_bytes)),
            "-movflags", "+faststart", "-f", "mp4", out,
        ]
        try:
            done = subprocess.run(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=max(1.0, remaining), check=False,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
            )
        except subprocess.TimeoutExpired as error:
            raise PlaybackTimedOut(
                "The recorder did not finish sending the footage in time. Request it again.") from error
        except OSError as error:
            raise PlaybackFailed("This site computer could not prepare the recorded footage.") from error
        size = os.path.getsize(out) if os.path.exists(out) else 0
        if size >= int(max_bytes):
            raise PlaybackTooLarge("The footage for this window is larger than the 32 MiB download limit.")
        if done.returncode != 0 or size < MIN_CLIP_BYTES:
            # A clean exit with almost nothing written is a stream that never started.
            raise _classify(done.stderr.decode("utf-8", "replace") if done.returncode != 0 else "")
        with open(out, "rb") as handle:
            return handle.read()
    finally:
        if out:
            try:
                os.unlink(out)
            except OSError:
                pass
