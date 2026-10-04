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
binary. Only a handful of operations are needed.

    GetDeviceInformation                identity
    GetCapabilities                     locate the events + media services
    GetProfiles                         channels, and the token maps events use
    CreatePullPointSubscription         start an event subscription
    PullMessages                        drain it, long-poll, outbound only
    GetSnapshotUri                      stills

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
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlparse

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from .base import (Channel, DeviceInfo, DriverError, Event, NvrDriver,
                   explain)

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

# Event types that belong to the recorder, not to a camera. They carry no
# video source, so they are emitted with channel None and a recorder_scope
# flag; putting them on a channel would turn a recorder HDD fault into a
# fault on whichever camera that channel happens to be.
RECORDER_SCOPED_TYPES = {"disk_error"}

# Data items that carry the state of a property event; false on one of them
# is the falling edge. Any other item that is literally "false" (StorageFailure
# "Failed", TamperDetector "IsTamper"...) is a cleared state too. "0" counts
# only for these keys, because elsewhere it may be an ObjectId or a count.
STATE_KEYS = ("ismotion", "state", "isinside", "logicalstate")

# Source SimpleItems that name the video input, and the token map each is
# looked up in first. The ONVIF topic definitions use a VideoSourceConfiguration
# token for rule-engine topics and a VideoSource token for VideoSource/*
# topics; what a given device sends is not verified, so the other maps are
# tried after the preferred one.
SOURCE_ITEMS = (
    ("videosourceconfigurationtoken", "config"),
    ("videosourcetoken",              "source"),
    ("videosource",                   "source"),
    ("source",                        "source"),
    ("profiletoken",                  "profile"),
)

BURST_WINDOW_SECONDS = 30
SNAPSHOT_TIMEOUT = 10
JPEG_MAGIC = bytes([0xFF, 0xD8])   # a JPEG always starts FF D8

PULL_TIMEOUT = "PT30S"
PULL_LIMIT = 100
SUBSCRIPTION_MINUTES = 10


def _strip_ns(elem: ET.Element) -> ET.Element:
    for e in elem.iter():
        e.tag = _TAG.sub("", e.tag)
    return elem


def _xs_datetime(text: str | None) -> datetime | None:
    """An xs:dateTime from the device as an aware datetime, or None."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_cleared(data: dict) -> bool:
    """True when the Data items say the property is off, not that it fired."""
    known = [v for k, v in data.items() if k.lower() in STATE_KEYS]
    if known:
        return any(str(v).strip().lower() in ("false", "0") for v in known)
    return any(str(v).strip().lower() == "false" for v in data.values())


def _bind(table: dict, token: str, channel: str) -> None:
    """Map a token to a channel. A token claimed by two cameras maps to None:
    an event carrying it cannot be attributed, so it must not be guessed."""
    if token:
        table[token] = channel if table.get(token, channel) == channel else None


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
        # Token -> physical channel, one map per token kind, all filled by
        # _load_profiles(). A value of None marks an ambiguous token.
        self._source_to_channel: dict[str, str | None] = {}
        self._config_to_channel: dict[str, str | None] = {}
        self._profile_to_channel: dict[str, str | None] = {}
        self._profile_tokens: dict[str, str] = {}     # channel -> snapshot profile
        # Burst filter state, in receive-time monotonic seconds: neither the
        # PC clock nor the recorder clock can step it backwards.
        self._last_emitted: dict[tuple[str | None, str], float] = {}
        self._monotonic = time.monotonic
        # Events whose source token matched no camera. They are dropped, never
        # guessed onto a channel; the count and the last source are kept so
        # the token the device actually sends can be read off a live site.
        self.dropped_unmapped = 0
        self.last_unmapped_source: dict | None = None

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
            raise DriverError(f"{url}: {explain(e)}") from e

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
        # The live collector, the stills worker and the analytics sampler use
        # a driver that was only probed and never call list_channels() on it,
        # so the token maps must be loaded here. Best effort: identification
        # has succeeded; stream_events()/get_snapshot() retry the load.
        try:
            self._ensure_profiles()
        except DriverError:
            pass
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
        actually reach and take the rest of the URL as advertised. The query
        matters: a snapshot URI or subscription address may name the channel,
        profile or pull point there, and dropping it would send every
        camera's request to the same URL.
        """
        try:
            adv, base = urlparse(advertised), urlparse(self.base_url)
            return adv._replace(scheme=base.scheme, netloc=base.netloc).geturl()
        except ValueError:
            return advertised

    def list_channels(self) -> list[Channel]:
        return self._load_profiles() or [Channel(channel="1", name="Channel 1")]

    def _ensure_profiles(self) -> bool:
        """Load the token maps on first use. True once a camera is mapped."""
        if not self._profile_tokens:
            self._load_profiles()
        return bool(self._profile_tokens)

    def _load_profiles(self) -> list[Channel]:
        """
        GetProfiles -> physical channels, plus every token an event or a
        snapshot request can name for each one. Returns [] when the device
        has no media service; the maps then stay empty rather than invented.
        """
        if not self.media_service:
            self._discover_services()
        if not self.media_service:
            return []

        root = self._call(self.media_service, "<trt:GetProfiles/>")

        # ONVIF GetProfiles returns ENCODING PROFILES, not physical cameras. A
        # recorder commonly exposes MainStream + SubStream for the same
        # VideoSourceConfiguration/SourceToken. Treating each profile as a
        # channel created duplicate "cameras" in WatchLog. Collapse profiles by
        # SourceToken and retain one preferred profile token for snapshots.
        groups: list[dict] = []
        by_source: dict[str, dict] = {}
        for idx, prof in enumerate(root.findall(".//Profiles"), start=1):
            name_node = prof.find("Name")
            name = (name_node.text.strip()
                    if name_node is not None and name_node.text
                    else f"Camera {idx}")
            src = prof.find(".//VideoSourceConfiguration/SourceToken")
            source_token = src.text.strip() if src is not None and src.text else ""
            profile_token = prof.get("token") or prof.findtext("token") or ""
            vsc = prof.find(".//VideoSourceConfiguration")
            config_token = (vsc.get("token") or "").strip() if vsc is not None else ""

            # If a device omits SourceToken, fail safe: that profile remains a
            # separate camera rather than accidentally merging unrelated views.
            key = source_token or f"__profile__:{profile_token or idx}"
            row = by_source.get(key)
            is_sub = bool(re.search(r"(sub[ _-]?stream|stream[ _-]?2)", name, re.I))
            is_main = bool(re.search(r"(main[ _-]?stream|stream[ _-]?1)", name, re.I))
            if row is None:
                row = {
                    "source_token": source_token,
                    "profile_token": profile_token,
                    "name": name,
                    "is_sub": is_sub,
                    "is_main": is_main,
                    "profiles": [],
                    "configs": [],
                }
                by_source[key] = row
                groups.append(row)
            # Every profile and configuration of this source resolves to the
            # same physical channel, whichever one an event happens to name.
            row["profiles"].append(profile_token)
            row["configs"].append(config_token)
            if row.get("is_sub") and not is_sub:
                # Prefer a non-sub/main profile for snapshot quality when both
                # profiles point at the same physical video source.
                row.update(profile_token=profile_token, name=name,
                           is_sub=is_sub, is_main=is_main)
            elif is_main and not row.get("is_main"):
                row.update(profile_token=profile_token, name=name,
                           is_sub=is_sub, is_main=is_main)

        sources: dict[str, str | None] = {}
        configs: dict[str, str | None] = {}
        profiles: dict[str, str | None] = {}
        snapshot_profiles: dict[str, str] = {}
        out: list[Channel] = []
        for physical_idx, row in enumerate(groups, start=1):
            channel = str(physical_idx)
            _bind(sources, row.get("source_token") or "", channel)
            for token in row["configs"]:
                _bind(configs, token, channel)
            for token in row["profiles"]:
                _bind(profiles, token, channel)
            if row.get("profile_token"):
                snapshot_profiles[channel] = str(row["profile_token"])

            raw_name = str(row.get("name") or "").strip()
            # Dahua/Hikvision ONVIF profile labels are transport/profile names,
            # not customer-facing camera names. Use a neutral physical camera
            # label until the operator names it in Guided Setup.
            if re.search(r"mediaprofile[_ -]*channel\d+", raw_name, re.I):
                raw_name = f"Camera {physical_idx}"
            out.append(Channel(channel=channel,
                               name=raw_name or f"Camera {physical_idx}"))

        # Swap whole maps so a reader never sees a half-built one.
        self._source_to_channel = sources
        self._config_to_channel = configs
        self._profile_to_channel = profiles
        self._profile_tokens = snapshot_profiles
        return out

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
        # Without the token maps every event would be unattributable; fail
        # the attempt so the collector reconnects, rather than stream blind.
        if not self._ensure_profiles():
            raise DriverError("device returned no ONVIF media profiles; "
                              "events cannot be attributed to cameras")
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

        # WS-BaseNotification wraps the ONVIF payload: wsnt:Message holds the
        # tt:Message that carries UtcTime, PropertyOperation, Source and Data.
        # With namespaces stripped both are "Message" and the first found is
        # the wrapper, which has no UtcTime; take the inner one.
        outer = msg.find(".//Message")
        if outer is None:
            return None
        inner = outer.find("Message")
        if inner is None:
            inner = outer            # a device that omits the wrapper

        # PropertyOperation="Initialized" is the device reporting a property's
        # CURRENT state because a subscription started, which happens on every
        # reconnect and every replaced pull point; "Deleted" says the property
        # is gone. Neither is an occurrence. Only "Changed", or a plain event
        # with no PropertyOperation, is something that just happened.
        if (inner.get("PropertyOperation") or "").lower() in ("initialized", "deleted"):
            return None

        # device_ts is the recorder's own stamp; receive time only when the
        # message carries none we can read.
        ts = _xs_datetime(inner.get("UtcTime")) or datetime.now(timezone.utc)

        # An ONVIF "event" fires on both rising and falling edge; the Data
        # SimpleItem carries the state. Drop the falling edge.
        data = {}
        for item in inner.findall(".//Data/SimpleItem"):
            data[item.get("Name", "")] = item.get("Value", "")
        if _is_cleared(data):
            return None

        source = {}
        for item in inner.findall(".//Source/SimpleItem"):
            source[item.get("Name", "")] = item.get("Value", "")
        if etype in RECORDER_SCOPED_TYPES:
            channel = None           # the recorder's own fault, no camera
        else:
            channel = self._resolve_channel(source)
            if channel is None:
                # Unknown camera: drop and count. Never default to a channel;
                # that pinned every camera's events on "1" and let one camera's
                # burst window swallow another's events.
                self.dropped_unmapped += 1
                self.last_unmapped_source = source
                return None

        # Collapse repeats by when we received them. A wall-clock delta goes
        # negative on a backward step, passes "< window", and silently drops
        # every later event of this (channel, type) until the clock catches up.
        key = (channel, etype)
        now = self._monotonic()
        last = self._last_emitted.get(key)
        if last is not None and now - last < BURST_WINDOW_SECONDS:
            return None
        self._last_emitted[key] = now

        payload = {"vendor": "onvif", "topic": topic,
                   "source": source, "data": data}
        if channel is None:
            payload["recorder_scope"] = True
        return Event(
            channel=channel,
            event_type=etype,
            device_ts=ts,
            device_event_id=None,
            payload=payload,
        )

    def _resolve_channel(self, source: dict) -> str | None:
        """
        The physical channel a notification's Source items name, or None.

        Each token is looked up in the map for its kind first, then in the
        others, using the same physical-camera grouping as list_channels().
        Items that disagree, or a token claimed by two cameras, give None.
        """
        tables = {"config": self._config_to_channel,
                  "source": self._source_to_channel,
                  "profile": self._profile_to_channel}
        items = {str(k).lower(): str(v or "").strip() for k, v in source.items()}
        found: set[str | None] = set()
        for name, kind in SOURCE_ITEMS:
            token = items.get(name)
            if not token:
                continue
            for table in [tables[kind]] + [t for k, t in tables.items() if k != kind]:
                if token in table:
                    found.add(table[token])
                    break
        return found.pop() if len(found) == 1 else None

    def get_snapshot(self, channel: str) -> bytes | None:
        """
        ONVIF GetSnapshotUri, then fetch the URI it hands back.

        The URI often needs HTTP auth of its own, and devices frequently
        advertise it on an address they cannot actually be reached at, so
        it goes through the same rehosting as the service endpoints.

        Callers hold a driver that was only probed (or, for Site Control,
        only built), so the profile map is loaded here on first use.
        """
        token = self._profile_tokens.get(str(channel))
        if not token:
            try:
                self._ensure_profiles()
            except DriverError:
                return None
            token = self._profile_tokens.get(str(channel))
        if not token or not self.media_service:
            return None
        try:
            root = self._call(
                self.media_service,
                f"<trt:GetSnapshotUri><trt:ProfileToken>{token}"
                f"</trt:ProfileToken></trt:GetSnapshotUri>")
        except DriverError:
            return None

        node = root.find(".//Uri")
        if node is None or not node.text:
            return None
        try:
            r = self.s.get(self._rehost(node.text.strip()),
                           auth=HTTPDigestAuth(self.username, self.password),
                           timeout=SNAPSHOT_TIMEOUT)
            if r.status_code == 401:
                r = self.s.get(self._rehost(node.text.strip()),
                               auth=HTTPBasicAuth(self.username, self.password),
                               timeout=SNAPSHOT_TIMEOUT)
        except requests.RequestException:
            return None
        if r.status_code == 200 and r.content[:2] == JPEG_MAGIC:
            return r.content
        return None

    def close(self) -> None:
        self.s.close()
