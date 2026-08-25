"""
ONVIF driver — the universal fallback.

This is what makes "compatible with most of the Pakistani market" a real
claim rather than an aspiration. Hikvision and Dahua have proprietary
APIs worth using directly, but everything else — Uniview, Tiandy, and the
long tail of rebadged units on dealer shelves — converges on ONVIF.
Anything advertising Profile S or Profile T should work here.

Hand-rolled SOAP over requests, deliberately: the alternatives
(onvif-zeep and friends) drag in lxml and zeep, which triples the
PyInstaller payload and adds two more things to go wrong inside a frozen
binary. Only five operations are needed.

    GetDeviceInformation                identity
    GetCapabilities                     locate the events + media services
    GetProfiles                         channels
    CreatePullPointSubscription         start an event subscription
    PullMessages                        drain it, long-poll, outbound only

Auth is WS-Security UsernameToken with a SHA-1 password digest, which is
what ONVIF mandates and what nearly every device accepts.

NOT YET VERIFIED AGAINST HARDWARE. ONVIF conformance in the budget end of
the market is uneven — this is the driver most likely to need adjusting
once a real device is on the bench. Run `watchlog_agent.py --probe`.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlparse

import requests

from .base import Channel, DeviceInfo, DriverError, Event, NvrDriver

_TAG = re.compile(r"\{.*?\}")

NS = {
    "s":    "http://www.w3.org/2003/05/soap-envelope",
    "wsa":  "http://www.w3.org/2005/08/addressing",
    "tds":  "http://www.onvif.org/ver10/device/wsdl",
    "trt":  "http://www.onvif.org/ver10/media/wsdl",
    "tev":  "http://www.onvif.org/ver10/events/wsdl",
    "tt":   "http://www.onvif.org/ver10/schema",
    "wsnt": "http://docs.oasis-open.org/wsn/b-2",
}

# ONVIF topics -> our vocabulary. Matched as substrings because vendors
# prefix and nest topics inconsistently.
TOPIC_MAP = [
    ("CellMotionDetector",  "motion"),
    ("MotionAlarm",         "motion"),
    ("MotionDetect",        "motion"),
    ("LineDetector",        "line_crossing"),
    ("CrossLine",           "line_crossing"),
    ("FieldDetector",       "intrusion"),
    ("IntrusionDetect",     "intrusion"),
    ("TamperDetect",        "tamper"),
    ("VideoSource/ImageTooDark",  "tamper"),
    ("VideoSource/SignalLoss",    "video_loss"),
    ("VideoLoss",           "video_loss"),
    ("Face",                "face"),
    ("HumanDetect",         "person"),
    ("PeopleDetect",        "person"),
    ("VehicleDetect",       "vehicle"),
    ("Storage",             "disk_error"),
]

BURST_WINDOW_SECONDS = 30
PULL_TIMEOUT = "PT30S"
PULL_LIMIT = 100
SUBSCRIPTION_MINUTES = 10


def _strip_ns(elem: ET.Element) -> ET.Element:
    for e in elem.iter():
        e.tag = _TAG.sub("", e.tag)
    return elem


def _security_header(user: str, password: str) -> str:
    """WS-Security UsernameToken, PasswordDigest profile."""
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()
    ).decode()
    return f"""<wsse:Security s:mustUnderstand="1"
        xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
        xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
      <wsse:UsernameToken>
        <wsse:Username>{user}</wsse:Username>
        <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
        <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce>
        <wsu:Created>{created}</wsu:Created>
      </wsse:UsernameToken>
    </wsse:Security>"""


class OnvifDriver(NvrDriver):
    name = "onvif"
    verified_against_hardware = False

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.s = requests.Session()
        self.device_service = self.base_url + "/onvif/device_service"
        self.events_service: str | None = None
        self.media_service: str | None = None
        self._sub_address: str | None = None
        self._sub_expires: datetime | None = None
        self._source_to_channel: dict[str, str] = {}
        self._last_emitted: dict[tuple[str, str], datetime] = {}

    # -- SOAP -----------------------------------------------------------

    def _call(self, url: str, body: str, action: str | None = None,
              to: str | None = None, timeout: int | None = None) -> ET.Element:
        headers_xml = _security_header(self.username, self.password)
        if to:
            headers_xml = (f'<wsa:To s:mustUnderstand="1">{to}</wsa:To>'
                           f'<wsa:Action s:mustUnderstand="1">{action}</wsa:Action>'
                           + headers_xml)
        envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="{NS['s']}" xmlns:wsa="{NS['wsa']}"
            xmlns:tds="{NS['tds']}" xmlns:trt="{NS['trt']}"
            xmlns:tev="{NS['tev']}" xmlns:tt="{NS['tt']}">
  <s:Header>{headers_xml}</s:Header>
  <s:Body>{body}</s:Body>
</s:Envelope>"""

        try:
            r = self.s.post(url, data=envelope.encode("utf-8"),
                            headers={"Content-Type":
                                     "application/soap+xml; charset=utf-8"},
                            timeout=timeout or self.timeout)
        except requests.RequestException as e:
            raise DriverError(f"{url}: {e}") from e

        if r.status_code >= 400:
            fault = re.search(rb"<[^>]*Text[^>]*>(.*?)</", r.content, re.S)
            detail = fault.group(1).decode("utf-8", "replace")[:200] if fault \
                else r.text[:200]
            raise DriverError(f"{url}: HTTP {r.status_code} {detail}")

        try:
            return _strip_ns(ET.fromstring(r.content))
        except ET.ParseError as e:
            raise DriverError(f"{url}: not XML ({e})") from e

    # -- interface ------------------------------------------------------

    def probe(self) -> DeviceInfo:
        root = self._call(self.device_service,
                          "<tds:GetDeviceInformation/>")
        get = lambda t: (root.find(f".//{t}").text                # noqa: E731
                         if root.find(f".//{t}") is not None else None)
        info = DeviceInfo(
            vendor=get("Manufacturer") or "ONVIF device",
            model=get("Model"),
            firmware=get("FirmwareVersion"),
            serial=get("SerialNumber"),
            driver=self.name,
            raw={"hardwareId": get("HardwareId")},
        )
        self._discover_services()
        return info

    def _discover_services(self) -> None:
        try:
            root = self._call(
                self.device_service,
                "<tds:GetCapabilities><tds:Category>All</tds:Category>"
                "</tds:GetCapabilities>")
        except DriverError:
            return
        for tag, attr in (("Events", "events_service"),
                          ("Media", "media_service")):
            node = root.find(f".//{tag}/XAddr")
            if node is not None and node.text:
                setattr(self, attr, self._rehost(node.text.strip()))

    def _rehost(self, advertised: str) -> str:
        """
        Devices often advertise service URLs using their own idea of their
        address (a stale DHCP lease, or 0.0.0.0). Keep the host we can
        actually reach and take only the path.
        """
        try:
            adv, base = urlparse(advertised), urlparse(self.base_url)
            return f"{base.scheme}://{base.netloc}{adv.path}"
        except ValueError:
            return advertised

    def list_channels(self) -> list[Channel]:
        if not self.media_service:
            self._discover_services()
        if not self.media_service:
            return [Channel(channel="1", name="Channel 1")]

        root = self._call(self.media_service, "<trt:GetProfiles/>")
        out: list[Channel] = []
        for idx, prof in enumerate(root.findall(".//Profiles"), start=1):
            name_node = prof.find("Name")
            src = prof.find(".//VideoSourceConfiguration/SourceToken")
            token = src.text.strip() if src is not None and src.text else None
            channel = str(idx)
            if token:
                self._source_to_channel[token] = channel
            out.append(Channel(
                channel=channel,
                name=(name_node.text.strip()
                      if name_node is not None and name_node.text
                      else f"Channel {idx}")))
        return out or [Channel(channel="1", name="Channel 1")]

    # -- events ---------------------------------------------------------

    def _subscribe(self) -> None:
        if not self.events_service:
            self._discover_services()
        if not self.events_service:
            raise DriverError("device advertises no ONVIF events service")

        root = self._call(
            self.events_service,
            f"<tev:CreatePullPointSubscription>"
            f"<tev:InitialTerminationTime>PT{SUBSCRIPTION_MINUTES}M"
            f"</tev:InitialTerminationTime>"
            f"</tev:CreatePullPointSubscription>")

        addr = root.find(".//SubscriptionReference/Address")
        if addr is None or not addr.text:
            raise DriverError("CreatePullPointSubscription returned no address")
        self._sub_address = self._rehost(addr.text.strip())
        self._sub_expires = (datetime.now(timezone.utc)
                             + timedelta(minutes=SUBSCRIPTION_MINUTES))

    def stream_events(self, stop: threading.Event) -> Iterator[Event]:
        self._subscribe()
        action = ("http://www.onvif.org/ver10/events/wsdl/"
                  "PullPointSubscription/PullMessages")

        while not stop.is_set():
            if (self._sub_expires
                    and datetime.now(timezone.utc)
                    > self._sub_expires - timedelta(minutes=2)):
                self._subscribe()

            root = self._call(
                self._sub_address,
                f"<tev:PullMessages><tev:Timeout>{PULL_TIMEOUT}</tev:Timeout>"
                f"<tev:MessageLimit>{PULL_LIMIT}</tev:MessageLimit>"
                f"</tev:PullMessages>",
                action=action, to=self._sub_address,
                timeout=self.timeout + 40)

            for msg in root.findall(".//NotificationMessage"):
                ev = self._parse_notification(msg)
                if ev:
                    yield ev

    def _parse_notification(self, msg: ET.Element) -> Event | None:
        topic_node = msg.find("Topic")
        topic = (topic_node.text or "").strip() if topic_node is not None else ""

        etype = None
        for needle, mapped in TOPIC_MAP:
            if needle.lower() in topic.lower():
                etype = mapped
                break
        if etype is None:
            return None

        inner = msg.find(".//Message")
        if inner is None:
            return None

        utc = inner.get("UtcTime")
        try:
            ts = (datetime.fromisoformat(utc.replace("Z", "+00:00"))
                  if utc else datetime.now(timezone.utc))
        except ValueError:
            ts = datetime.now(timezone.utc)

        # An ONVIF "event" fires on both rising and falling edge; the Data
        # SimpleItem carries the state. Drop the falling edge.
        data = {}
        for item in inner.findall(".//Data/SimpleItem"):
            data[item.get("Name", "")] = item.get("Value", "")
        for key, val in data.items():
            if key.lower() in ("ismotion", "state", "isinside", "logicalstate"):
                if str(val).lower() in ("false", "0"):
                    return None

        source = {}
        for item in inner.findall(".//Source/SimpleItem"):
            source[item.get("Name", "")] = item.get("Value", "")
        token = (source.get("VideoSourceConfigurationToken")
                 or source.get("VideoSource")
                 or source.get("Source") or "")
        channel = self._source_to_channel.get(token, "1")

        key = (channel, etype)
        last = self._last_emitted.get(key)
        if last and (ts - last).total_seconds() < BURST_WINDOW_SECONDS:
            return None
        self._last_emitted[key] = ts

        return Event(
            channel=channel,
            event_type=etype,
            device_ts=ts,
            device_event_id=None,
            payload={"vendor": "onvif", "topic": topic,
                     "source": source, "data": data},
        )

    def close(self) -> None:
        self.s.close()
