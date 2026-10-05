"""
Hikvision and Dahua alarm parsing shared by the Agent drivers and the push bridge.

One alarm can reach WatchLog two ways: the Agent reads it from the recorder's event
stream (alertStream / eventManager attach), or the recorder POSTs it to the push
bridge. Both paths are keyed by the same database dedupe identity (site, recorder,
channel, device_event_id, device time, event type), so they must agree on every
part of it or one alarm becomes two rows with two different meanings (MNVR-026).
This module is that single agreement:

  * one event-type map per vendor;
  * one channel rule: a recorder-level alert (disk, login, network, alarm input —
    including a Hikvision IO alert or any alert carrying inputIOPortID, whose port is
    kept as ``native_input``) has channel None plus ``recorder_scoped``; a camera alert without a channel id has
    channel None plus ``channel_unknown``. Never camera "1", never a camera name;
  * no invented device_event_id: neither vendor's alarm carries a stable id;
  * Hikvision keep-alives are not events: inactive alerts, heartBeat, and videoloss
    with activePostCount 0 (or none);
  * one burst rule: a continuing alarm repeats about once a second, and repeats of the
    same (channel, type) inside BURST_WINDOW_SECONDS collapse into one event, timed on
    a monotonic receive clock, never on the recorder's clock;
  * one time rule: an alarm time with an offset is used as sent; a naive one is
    recorder-local time and is never read as UTC. Without a known recorder offset the
    receive time is used and the recorder's text is kept.

STDLIB ONLY. The push bridge is a stdlib-only container whose Docker build context is
prototype/bridge, so it ships a byte-identical copy of this file at
prototype/bridge/alarm_parsing.py. test_push_bridge pins the two copies identical:
edit this file, then copy it there.
"""

from __future__ import annotations

import json
import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

# Repeats of one continuing alarm inside this window collapse into one event.
BURST_WINDOW_SECONDS = 30

# A recorder stamp further than this from the time the alarm arrived is kept but
# flagged on the event (clock_skew_seconds) rather than trusted silently.
CLOCK_SKEW_FLAG_SECONDS = 300

# Hikvision eventType values worth keeping, mapped to our vocabulary.
HIK_EVENT_TYPE_MAP = {
    "vmd": "motion",
    "motiondetection": "motion",
    "linedetection": "line_crossing",
    "fielddetection": "intrusion",
    "regionexiting": "region_exit",
    "regionentrance": "region_entry",
    "tamperdetection": "tamper",
    "shelteralarm": "tamper",
    "videoloss": "video_loss",
    "diskfull": "disk_full",
    "diskerror": "disk_error",
    "facedetection": "face",
    "peopledetection": "person",
    "vehicledetection": "vehicle",
}

# Alert types that describe the recorder itself (its disks, logins, network link and
# alarm inputs), not a camera. A channel field on these does not name a video input
# (MNVR-028). An alert that carries inputIOPortID is an alarm input whatever its type.
HIK_RECORDER_SCOPED_TYPES = {"diskfull", "diskerror", "illaccess", "illegalaccess",
                             "ipconflict", "nicbroken", "io"}

# Recorder-side smart analytics (as opposed to plain motion or faults).
HIK_SMART_TYPES = {"linedetection", "fielddetection", "regionexiting", "regionentrance",
                   "facedetection", "peopledetection", "vehicledetection"}

# The HTTP-host heartBeat is the recorder's liveness signal, never an incident.
HIK_NON_EVENTS = {"heartbeat"}

# Dahua event codes -> our vocabulary.
DAHUA_EVENT_CODE_MAP = {
    "VideoMotion": "motion",
    "SmartMotionHuman": "person",
    "SmartMotionVehicle": "vehicle",
    "CrossLineDetection": "line_crossing",
    "CrossRegionDetection": "intrusion",
    "LeftDetection": "object_left",
    "TakenAwayDetection": "object_removed",
    "VideoLoss": "video_loss",
    "VideoBlind": "tamper",
    "AlarmLocal": "alarm_input",
    "StorageNotExist": "disk_error",
    "StorageFailure": "disk_error",
    "StorageLowSpace": "disk_full",
    "FaceDetection": "face",
}

# Codes whose index names a disk or an alarm input, not a video channel. They are
# recorder-scoped: channel None plus a flag, never index+1 guessed onto a camera.
DAHUA_RECORDER_SCOPED_CODES = {"AlarmLocal", "StorageNotExist", "StorageFailure",
                               "StorageLowSpace"}

# Chatter a Dahua unit emits that is not an occurrence (compared case-insensitively).
DAHUA_NON_EVENTS = {"heartbeat", "keepalive", "timechange", "ntpadjusttime"}

_NS = re.compile(r"\{.*?\}")


@dataclass
class Alarm:
    """What an alarm means, independent of the path it arrived by.

    ``raw_time`` is the recorder's own time text, or None when the format carries
    none. ``burst_key`` identifies a continuing alarm for burst collapse.
    """
    vendor: str
    channel: str | None
    event_type: str
    burst_key: tuple
    raw_time: str | None
    payload: dict = field(default_factory=dict)


def parse_iso_time(raw: str | None) -> datetime | None:
    """ISO 8601 as the recorder sent it: aware with an offset, naive without one, None
    when absent or unparseable. A naive value is recorder-local time, never UTC."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_event_time(raw_time: str | None, received_at: datetime, *,
                       receive_source: str,
                       naive_offset: Callable[[], timedelta | None] | None = None
                       ) -> tuple[datetime, dict]:
    """The event time, plus payload fields that say which clock stamped it.

    An aware recorder time is used as sent (skew against ``received_at`` flagged past
    CLOCK_SKEW_FLAG_SECONDS). A naive one is localised with ``naive_offset()`` when the
    recorder states its offset; otherwise the receive time is used, labelled
    ``receive_source``, and the recorder's text is kept as device_time_raw."""
    clock: dict = {}
    ts = parse_iso_time(raw_time)
    source = "recorder"
    if ts is not None and ts.tzinfo is None:
        offset = naive_offset() if naive_offset is not None else None
        if offset is not None:
            ts, source = ts.replace(tzinfo=timezone(offset)), "recorder_local"
        else:
            ts = None
    if ts is None:
        ts, source = received_at, receive_source
        if raw_time:
            clock["device_time_raw"] = raw_time
    else:
        skew = (ts - received_at).total_seconds()
        if abs(skew) > CLOCK_SKEW_FLAG_SECONDS:
            clock["clock_skew_seconds"] = int(round(skew))
    clock["clock_source"] = source
    return ts, clock


def _child_text(node: ET.Element, path: str) -> str | None:
    found = node.find(path)
    return found.text.strip() if found is not None and found.text else None


def parse_hikvision_alert(raw: bytes) -> Alarm | None:
    """One EventNotificationAlert document, or None when it is not an event."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    for elem in root.iter():
        elem.tag = _NS.sub("", elem.tag)
    if root.tag != "EventNotificationAlert":
        return None

    etype_raw = (_child_text(root, "eventType") or "").strip()
    state = (_child_text(root, "eventState") or "").lower()
    if state == "inactive":
        return None

    # Hikvision keeps alertStream warm by emitting videoloss with activePostCount 0
    # whenever nothing is happening. A genuine video-loss alarm carries a non-zero
    # count. Dropping all videoloss would hide a real fault; keeping all of it would
    # fill the database with heartbeats. The count is the discriminator.
    active_post = (_child_text(root, "activePostCount") or "").strip()
    if etype_raw.lower() == "videoloss" and active_post in ("0", ""):
        return None
    if not etype_raw or etype_raw.lower() in HIK_NON_EVENTS:
        return None

    etype = HIK_EVENT_TYPE_MAP.get(etype_raw.lower()) or etype_raw.lower()

    # A recorder-level alert, or a camera alert without a channel id, has channel
    # None plus a flag. Never camera "1", and never the camera NAME as a channel id
    # (it joins no camera); the name stays in the payload only.
    scope: dict = {}
    native_channel = _child_text(root, "channelID") or _child_text(root, "dynChannelID")
    native_input = (_child_text(root, "inputIOPortID") or "").strip()
    if etype_raw.lower() in HIK_RECORDER_SCOPED_TYPES or native_input:
        channel = None
        scope["recorder_scoped"] = True
        if native_channel:
            scope["native_channel"] = native_channel
        if native_input:
            scope["native_input"] = native_input
    elif native_channel:
        channel = native_channel
    else:
        channel = None
        scope["channel_unknown"] = True
        if _child_text(root, "channelName"):
            scope["channelName"] = _child_text(root, "channelName")

    targets = []
    for node in root.iter():
        tag = node.tag.lower()
        text = (node.text or "").strip().lower()
        if "targettype" in tag or tag in ("objecttype", "targetclass"):
            for raw_target in re.split(r"[,;|\s]+", text):
                if raw_target in ("human", "person", "pedestrian"):
                    targets.append("human")
                elif raw_target in ("vehicle", "car", "motorvehicle"):
                    targets.append("vehicle")
    targets = sorted(set(targets))
    smart_native = etype_raw.lower() in HIK_SMART_TYPES or bool(targets)

    return Alarm(
        vendor="hikvision",
        channel=channel,
        event_type=etype,
        burst_key=(channel if channel is not None
                   else f"recorder:{native_channel or ''}:{native_input}", etype),
        raw_time=_child_text(root, "dateTime"),
        payload={"vendor": "hikvision", "eventType": etype_raw,
                 "native_code": etype_raw,
                 "native_ai": smart_native,
                 "targets": targets,
                 "eventDescription": _child_text(root, "eventDescription"),
                 "activePostCount": _child_text(root, "activePostCount"),
                 **scope},
    )


def split_dahua_text(text: str) -> tuple[dict, str | None] | None:
    """``Code=...;action=...;index=...;data={...}`` (or the same fields as JSON) ->
    (fields, data text or None). None when it is not a Dahua event block."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            return None
        if not isinstance(obj, dict):
            return None
        fields = {str(k): ("" if v is None else str(v)) for k, v in obj.items()
                  if not isinstance(v, (dict, list))}
        data = obj.get("data")
        return fields, (json.dumps(data) if isinstance(data, (dict, list)) else None)
    # data={...} may itself contain ';', so only split the leading pairs.
    head, sep, data = text.partition(";data=")
    if "Code=" not in head:
        return None
    fields = {}
    for part in head.split(";"):
        k, _, v = part.partition("=")
        if k:
            fields[k.strip()] = v.strip()
    return fields, (data if sep else None)


def parse_dahua_block(text: str) -> Alarm | None:
    """One Dahua event block, or None when it is not an occurrence."""
    split = split_dahua_text(text)
    if split is None:
        return None
    fields, data = split
    code = fields.get("Code") or fields.get("code") or ""
    if not code:
        return None
    action = (fields.get("action") or fields.get("Action") or "").lower()
    if action not in ("start", "pulse", ""):
        return None                       # Stop / State — not an occurrence

    etype = DAHUA_EVENT_CODE_MAP.get(code)
    if etype is None:
        if code.lower() in DAHUA_NON_EVENTS:
            return None
        etype = code.lower()

    scope: dict = {}
    index = fields.get("index", fields.get("Index"))
    if code in DAHUA_RECORDER_SCOPED_CODES:
        channel = None
        scope = {"recorder_scoped": True, "native_index": index}
    else:
        # index is 0-based on the wire; channels are 1-based everywhere else.
        try:
            channel = str(int(str(index).strip()) + 1)
        except (TypeError, ValueError):
            channel = None            # no usable index: unknown, never camera 1
            scope = {"channel_unknown": True, "native_index": index}

    return Alarm(
        vendor="dahua",
        channel=channel,
        event_type=etype,
        burst_key=(channel if channel is not None else f"recorder:{index}", etype),
        raw_time=fields.get("dateTime") or fields.get("DateTime") or None,
        payload={"vendor": "dahua", "code": code, "action": action,
                 "data": (data[:500] if data is not None else None), **scope},
    )


class BurstFilter:
    """Admit the first of a continuing alarm's repeats per BURST_WINDOW_SECONDS.

    ``now`` is a monotonic receive clock, so a recorder clock stepping backwards can
    never suppress later events (MNVR-023). Thread-safe; old keys are pruned so a
    long-running process does not grow without bound."""

    def __init__(self, window: float = BURST_WINDOW_SECONDS, max_keys: int = 4096) -> None:
        self.window = window
        self.max_keys = max_keys
        self._last: dict = {}
        self._lock = threading.Lock()

    def admit(self, key, now: float) -> bool:
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.window:
                return False
            self._last[key] = now
            if len(self._last) > self.max_keys:
                cutoff = now - self.window
                self._last = {k: v for k, v in self._last.items() if v >= cutoff}
            return True

    def forget(self, key, now: float) -> None:
        """Undo an admission made at ``now`` (the event was not delivered)."""
        with self._lock:
            if self._last.get(key) == now:
                del self._last[key]
