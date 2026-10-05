"""Hardware-free ONVIF recorder for the OnvifDriver regression tests.

Stands in for requests.Session inside drivers.onvif_driver. It answers the
SOAP operations the driver sends with spec-shaped, namespaced XML (the way a
device sends it, so the driver's own namespace stripping and parsing run) and
records every call. Tokens, names and times are invented fixtures; nothing
here describes the behaviour of any real recorder model.

Default inventory: 8 physical inputs, each exposed as a MainStream and a
SubStream profile (16 profiles). The VideoSourceConfiguration token, the
VideoSource token and the profile tokens all differ, so a lookup in the wrong
map cannot pass by accident.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from drivers import onvif_driver  # noqa: E402

CAMERAS = 8
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0])

_ENV_OPEN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:wsa="http://www.w3.org/2005/08/addressing"'
    ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
    ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
    ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
    ' xmlns:tt="http://www.onvif.org/ver10/schema"'
    ' xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"'
    ' xmlns:tns1="http://www.onvif.org/ver10/topics">'
    '<s:Body>')
_ENV_CLOSE = '</s:Body></s:Envelope>'
_FAULT = ('<s:Fault><s:Code><s:Value>s:Receiver</s:Value></s:Code>'
          '<s:Reason><s:Text xml:lang="en">fixture fault</s:Text></s:Reason></s:Fault>')


def source_token(n: int) -> str:
    return f"VideoSourceToken_{n:03d}"


def config_token(n: int) -> str:
    return f"VideoSourceConfig_{n:03d}"


def profile_token(n: int, kind: str = "main") -> str:
    return f"MediaProfile_{n:03d}_{kind}"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def notification(topic: str, utc: str, source: dict, data: dict,
                 operation: str | None = "Changed") -> str:
    """One wsnt:NotificationMessage in the WS-BaseNotification shape:
    wsnt:Message wraps the ONVIF tt:Message that carries UtcTime,
    PropertyOperation, Source and Data."""
    src = "".join(f'<tt:SimpleItem Name="{k}" Value="{v}"/>' for k, v in source.items())
    dat = "".join(f'<tt:SimpleItem Name="{k}" Value="{v}"/>' for k, v in data.items())
    op = f' PropertyOperation="{operation}"' if operation else ""
    return ('<wsnt:NotificationMessage>'
            '<wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">'
            f'{topic}</wsnt:Topic>'
            f'<wsnt:Message><tt:Message UtcTime="{utc}"{op}>'
            f'<tt:Source>{src}</tt:Source><tt:Data>{dat}</tt:Data>'
            '</tt:Message></wsnt:Message></wsnt:NotificationMessage>')


class WallClock(datetime):
    """Stands in for onvif_driver.datetime: now() reads `current`, so a test
    sets (or steps) the PC clock that recorder stamps are compared with."""

    current = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)

    @classmethod
    def install(cls, monkeypatch, at: datetime) -> None:
        monkeypatch.setattr(onvif_driver, "datetime", cls)
        cls.current = at


class FakeClock:
    """Injected as OnvifDriver._monotonic; advanced explicitly by tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Resp:
    def __init__(self, status: int, content: bytes) -> None:
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8", "replace")


class _Session:
    def __init__(self, recorder: "FakeRecorder") -> None:
        self.recorder = recorder

    def post(self, url, data=None, headers=None, timeout=None):
        return self.recorder._post(url, data.decode("utf-8"), timeout)

    def get(self, url, auth=None, timeout=None):
        return self.recorder._get(url)

    def close(self) -> None:
        self.recorder.sessions_closed += 1


class FakeRecorder:
    def __init__(self, cameras: int = CAMERAS, grant_seconds: int = 600,
                 device_now: str = "2026-10-04T10:00:00Z") -> None:
        self.cameras = cameras
        self.grant_seconds = grant_seconds
        self.device_now = datetime.fromisoformat(device_now.replace("Z", "+00:00"))
        self.calls: list[dict] = []
        self.snapshot_urls: list[str] = []
        self.batches: list[list[str]] = []
        self.fail: set[str] = set()
        self.stop = None              # threading.Event set once the batches run dry
        self.clock: FakeClock | None = None
        self.pull_seconds = 0.0       # fake monotonic time each PullMessages takes
        self.subscriptions = 0
        self.sessions_closed = 0
        # As it appears in the response XML (so "&" must be written "&amp;").
        self.subscription_path = "/onvif/subscription/{n}"

    # -- wiring ---------------------------------------------------------

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(onvif_driver.requests, "Session", lambda: _Session(self))

    def queue(self, *messages: str) -> None:
        self.batches.append(list(messages))

    def ops(self) -> list[str]:
        return [c["op"] for c in self.calls]

    def calls_of(self, op: str) -> list[dict]:
        return [c for c in self.calls if c["op"] == op]

    # -- SOAP -----------------------------------------------------------

    def _post(self, url: str, body: str, timeout) -> _Resp:
        m = re.search(r"<s:Body>\s*<(?:\w+:)?(\w+)", body)
        op = m.group(1) if m else "?"
        self.calls.append({"op": op, "url": url, "body": body, "timeout": timeout})
        try:
            ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            # A device's SOAP stack rejects a request that is not XML.
            self.calls[-1]["malformed"] = True
            return _Resp(400, (_ENV_OPEN + _FAULT + _ENV_CLOSE).encode())
        if op in self.fail:
            return _Resp(500, (_ENV_OPEN + _FAULT + _ENV_CLOSE).encode())
        handler = getattr(self, "_op_" + op, None)
        if handler is None:
            return _Resp(400, (_ENV_OPEN + _FAULT + _ENV_CLOSE).encode())
        return _Resp(200, (_ENV_OPEN + handler(body) + _ENV_CLOSE).encode())

    def _get(self, url: str) -> _Resp:
        self.snapshot_urls.append(url)
        token = url.rsplit("profile=", 1)[-1]
        return _Resp(200, JPEG + token.encode())

    def _times(self) -> str:
        end = self.device_now + timedelta(seconds=self.grant_seconds)
        return (f"<wsnt:CurrentTime>{iso(self.device_now)}</wsnt:CurrentTime>"
                f"<wsnt:TerminationTime>{iso(end)}</wsnt:TerminationTime>")

    def _op_GetDeviceInformation(self, _body: str) -> str:
        return ("<tds:GetDeviceInformationResponse>"
                "<tds:Manufacturer>Fixture</tds:Manufacturer><tds:Model>FX-8</tds:Model>"
                "<tds:FirmwareVersion>0.0</tds:FirmwareVersion>"
                "<tds:SerialNumber>FIXTURE0</tds:SerialNumber><tds:HardwareId>0</tds:HardwareId>"
                "</tds:GetDeviceInformationResponse>")

    def _op_GetCapabilities(self, _body: str) -> str:
        # Advertised on an unroutable host: the driver must rehost onto base_url.
        return ("<tds:GetCapabilitiesResponse><tds:Capabilities>"
                "<tt:Events><tt:XAddr>http://0.0.0.0/onvif/event_service</tt:XAddr></tt:Events>"
                "<tt:Media><tt:XAddr>http://0.0.0.0/onvif/media_service</tt:XAddr></tt:Media>"
                "</tds:Capabilities></tds:GetCapabilitiesResponse>")

    def _op_GetProfiles(self, _body: str) -> str:
        out = []
        for n in range(1, self.cameras + 1):
            for kind, label in (("main", "MainStream"), ("sub", "SubStream1")):
                out.append(
                    f'<trt:Profiles token="{profile_token(n, kind)}" fixed="true">'
                    f'<tt:Name>MediaProfile_Channel{n}_{label}</tt:Name>'
                    f'<tt:VideoSourceConfiguration token="{config_token(n)}">'
                    f'<tt:Name>VideoSourceConfig_{n}</tt:Name><tt:UseCount>2</tt:UseCount>'
                    f'<tt:SourceToken>{source_token(n)}</tt:SourceToken>'
                    '</tt:VideoSourceConfiguration></trt:Profiles>')
        return "<trt:GetProfilesResponse>" + "".join(out) + "</trt:GetProfilesResponse>"

    def _op_GetSnapshotUri(self, body: str) -> str:
        token = re.search(r"ProfileToken>([^<]+)<", body).group(1)
        return ("<trt:GetSnapshotUriResponse><trt:MediaUri>"
                f"<tt:Uri>http://0.0.0.0/onvif/snapshot?profile={token}</tt:Uri>"
                "</trt:MediaUri></trt:GetSnapshotUriResponse>")

    def _op_CreatePullPointSubscription(self, _body: str) -> str:
        self.subscriptions += 1
        path = self.subscription_path.format(n=self.subscriptions)
        return ("<tev:CreatePullPointSubscriptionResponse><tev:SubscriptionReference>"
                f"<wsa:Address>http://0.0.0.0{path}</wsa:Address>"
                "</tev:SubscriptionReference>" + self._times()
                + "</tev:CreatePullPointSubscriptionResponse>")

    def _op_PullMessages(self, _body: str) -> str:
        if self.clock is not None:
            self.clock.advance(self.pull_seconds)
        messages = self.batches.pop(0) if self.batches else []
        if not self.batches and not messages and self.stop is not None:
            self.stop.set()
        return ("<tev:PullMessagesResponse>"
                f"<tev:CurrentTime>{iso(self.device_now)}</tev:CurrentTime>"
                f"<tev:TerminationTime>{iso(self.device_now)}</tev:TerminationTime>"
                + "".join(messages) + "</tev:PullMessagesResponse>")

    def _op_Renew(self, _body: str) -> str:
        return "<wsnt:RenewResponse>" + self._times() + "</wsnt:RenewResponse>"

    def _op_Unsubscribe(self, _body: str) -> str:
        return "<wsnt:UnsubscribeResponse/>"


def stream(driver, recorder: FakeRecorder) -> list:
    """Drain driver.stream_events() until the recorder's queued batches run dry."""
    import threading
    recorder.stop = threading.Event()
    return list(driver.stream_events(recorder.stop))


class FakeCfg:
    """The few Config fields open_driver() and the collectors read."""

    nvr_driver = "onvif"
    nvr_url = "http://192.0.2.10"
    nvr_username = "local-user"
    nvr_password = "local-password"
    snapshots = True
    snapshot_min_interval = 0
    detect = False
    supabase_url = "https://cloud.invalid"
    publishable_key = "fixture"

    def require_nvr(self) -> None:
        pass
