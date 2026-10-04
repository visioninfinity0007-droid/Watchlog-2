"""Hardware-free fake of the Dahua CGI archive surface, shared by the dahua_archive tests.

The fake recorder keeps its OWN wall clock (agent UTC + zone + drift) and an archive of DAV files
whose times are recorder-local wall time. That is the working model dahua_archive is written
against; whether a real DH-XVR1B08-I reports mediaFileFind times this way is a field question this
fake cannot answer. Every CGI call is recorded so tests can assert the exact recorder-local
windows WatchLog asked for.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from drivers.dahua import DahuaDriver  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"
DHAV = b"DHAV" + b"\x00" * 1024
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def local(text: str) -> datetime:
    """A recorder-local wall time (naive), as the recorder prints it."""
    return datetime.strptime(text, FMT)


def pinned_datetime(now: datetime):
    """A datetime class whose now() is pinned, for patching ``dahua_archive.datetime``."""
    class Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)
    return Pinned


class FakeResponse:
    def __init__(self, status=200, text="", chunks=None):
        self.status_code = status
        self._text = text
        self._chunks = chunks if chunks is not None else []
        self.closed = False

    @property
    def text(self):
        return self._text

    def iter_content(self, chunk_size=0):
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeRecorder:
    """Routes the archive CGI calls like a Dahua recorder; doubles as the driver's session."""

    def __init__(self, pc_now: datetime, *, zone=timedelta(hours=5), drift=timedelta(0),
                 files=(), clip_chunks=None):
        self.pc_now = pc_now
        self.zone, self.drift = zone, drift
        self.files = list(files)          # [(start_local, end_local, path)], recorder wall time
        self.clip_chunks = clip_chunks
        self.calls = []                   # [(cgi, params, timeout, stream)]
        self.responses = []
        self.auth = None
        self._finders: dict[str, list] = {}
        self.on_call = None               # optional hook(cgi, params) for clock/latency tricks

    def wall_clock(self) -> datetime:
        return self.pc_now.astimezone(timezone.utc).replace(tzinfo=None) + self.zone + self.drift

    def get(self, url, params=None, timeout=None, stream=False):
        params = dict(params or {})
        cgi = url.rsplit("/", 1)[-1]
        self.calls.append((cgi, params, timeout, stream))
        if self.on_call:
            self.on_call(cgi, params)
        action = str(params.get("action", ""))
        if cgi == "global.cgi":
            return FakeResponse(text=f"result={self.wall_clock().strftime(FMT)}\r\n")
        if cgi == "mediaFileFind.cgi":
            if action == "factory.create":
                finder = f"finder{len(self._finders) + 1}"
                self._finders[finder] = []
                return FakeResponse(text=f"result={finder}\r\n")
            if action == "findFile":
                start = local(params["condition.StartTime"])
                end = local(params["condition.EndTime"])
                self._finders[params["object"]] = [
                    f for f in self.files if f[0] < end and f[1] > start]
                return FakeResponse(text="OK\r\n")
            if action == "findNextFile":
                hits = self._finders[params["object"]]
                count = int(params["count"])
                page, self._finders[params["object"]] = hits[:count], hits[count:]
                return FakeResponse(text=self.items_text(page))
            return FakeResponse(text="OK\r\n")            # close / destroy
        if cgi == "loadfile.cgi":
            # A callable yields a fresh (possibly endless) chunk stream per download.
            chunks = self.clip_chunks() if callable(self.clip_chunks) else list(self.clip_chunks or [DHAV])
            response = FakeResponse(chunks=chunks)
            self.responses.append(response)
            return response
        return FakeResponse(status=404, text="Error")

    @staticmethod
    def items_text(page) -> str:
        lines = [f"found={len(page)}"]
        for i, (start, end, path) in enumerate(page):
            lines += [f"items[{i}].Channel=0",
                      f"items[{i}].StartTime={start.strftime(FMT) if isinstance(start, datetime) else start}",
                      f"items[{i}].EndTime={end.strftime(FMT) if isinstance(end, datetime) else end}",
                      f"items[{i}].FilePath={path}"]
        return "\r\n".join(lines) + "\r\n"

    def calls_to(self, cgi: str, action: str | None = None) -> list:
        return [c for c in self.calls
                if c[0] == cgi and (action is None or c[1].get("action") == action)]

    def loadfile_windows(self) -> list[tuple[datetime, datetime]]:
        return [(local(c[1]["startTime"]), local(c[1]["endTime"]))
                for c in self.calls_to("loadfile.cgi", "startLoad")]


class FakeDahua(DahuaDriver):
    """A real DahuaDriver (so dahua_archive.install() applies) whose session is the fake."""

    def __init__(self, recorder: FakeRecorder):
        super().__init__("http://recorder.invalid", "u", "p", timeout=5)
        self.s = recorder


class FakeCloud:
    """Just enough of the recovery RPC surface for RecoveryRunner."""

    def __init__(self, intervals):
        self.intervals = intervals
        self.completes = []

    def call(self, name, **kw):
        if name == "wl_agent_claim_recovery":
            return [iv for iv in self.intervals if iv.get("status") == "pending"][: kw.get("p_limit", 1)]
        if name == "wl_complete_recovery":
            self.completes.append(kw)
        return {}


def continuous_files(first: datetime, last: datetime, minutes: int = 30) -> list:
    """Back-to-back recorder-local DAV files covering [first, last)."""
    files, cur = [], first
    while cur < last:
        nxt = cur + timedelta(minutes=minutes)
        files.append((cur, nxt, f"/mnt/dvr/{cur.strftime('%Y%m%d%H%M%S')}.dav"))
        cur = nxt
    return files
