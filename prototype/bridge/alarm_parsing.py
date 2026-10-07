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
  * Hikvision keep-alives are not events: heartBeat, and videoloss with
    activePostCount 0 (or none), active or inactive;
  * one end rule (5.1.2): the END of a fault/safety alarm is an event, never dropped as
    a keep-alive. Hikvision eventState=inactive and Dahua action=Stop become
    video_restore, tamper_end, alarm_input_end or camera_reconnect (END_TYPES); the end
    of any other alarm (motion, line crossing...) is still not an occurrence, and a
    Dahua action=State is a keep-alive;
  * one burst rule: a continuing alarm repeats about once a second, and repeats of the
    same (channel, type) inside BURST_WINDOW_SECONDS collapse into one event, timed on
    a monotonic receive clock, never on the recorder's clock. A start and its end are
    different types, so an end is never suppressed by the start before it; an end is
    admitted once per start (repeats of an end are not new occurrences), and it re-arms
    the start, so a loss right after a restore is a new event;
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
    # A recorder alarm input (5.1.2: one name with Dahua AlarmLocal; the port stays in
    # the payload as native_input).
    "io": "alarm_input",
    # NVR exception "IP camera disconnected". IMPLEMENTED_UNVERIFIED: the eventType text
    # is matched case-insensitively (ipcDisconnect / IPCDisconnect) and has not been seen
    # on a field unit yet.
    "ipcdisconnect": "camera_disconnect",
}

# eventState=inactive on these eventTypes is the END of the alarm, and an event in its
# own right (5.1.2). Any other inactive alert is still not an occurrence.
HIK_END_TYPE_MAP = {
    "videoloss": "video_restore",
    "tamperdetection": "tamper_end",
    "shelteralarm": "tamper_end",
    "io": "alarm_input_end",
    "ipcdisconnect": "camera_reconnect",
}

# Alert types that describe the recorder itself (its disks, logins, network link and
# alarm inputs), not a camera. A channel field on these does not name a video input
# (MNVR-028). An alert that carries inputIOPortID is an alarm input whatever its type.
HIK_RECORDER_SCOPED_TYPES = {"diskfull", "diskerror", "illaccess", "illegalaccess",
                             "ipconflict", "nicbroken", "io"}

# The end of a fault/safety alarm -> the start type it closes. Used by both vendors so a
# restore pairs with its loss in BurstFilter whichever path it arrived by.
END_TYPES = {
    "video_restore": "video_loss",
    "tamper_end": "tamper",
    "alarm_input_end": "alarm_input",
    "camera_reconnect": "camera_disconnect",
}
PAIRED_START_TYPES = frozenset(END_TYPES.values())

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

# action=Stop on these codes is the END of the alarm, and an event in its own right
# (5.1.2). A Stop on any other code is still not an occurrence. On an NVR an IP camera
# that goes offline raises VideoLoss on its channel, so VideoLoss start/stop is also the
# camera disconnect/reconnect signal (IMPLEMENTED_UNVERIFIED: no Dahua code that names
# an IP-camera disconnect explicitly is mapped).
DAHUA_END_CODE_MAP = {
    "VideoLoss": "video_restore",
    "VideoBlind": "tamper_end",
    "AlarmLocal": "alarm_input_end",
}

# Codes whose index names a disk or an alarm input, not a video channel. They are
# recorder-scoped: channel None plus a flag, never index+1 guessed onto a camera.
DAHUA_RECORDER_SCOPED_CODES = {"AlarmLocal", "StorageNotExist", "StorageFailure",
                               "StorageLowSpace"}

# 5.1.2 subscribes to codes=[All], so codes outside the map arrive too and are stored raw
# (lowercased). Their index is a video channel only when the code is a camera analytic;
# for the recorder's own conditions (network, logins, power, chassis, alarm outputs) it is
# not, and for a code we do not know it is not guessed either: channel None plus
# channel_unknown (and native_index), never index+1 onto a camera that did not raise it.
# Compared case-insensitively.
DAHUA_RECORDER_LEVEL_CODES = {"netabort", "ipconflict", "macconflict", "loginfailure",
                              "powerfault", "chassisintruded", "alarmoutput",
                              "storagenotexist", "storagefailure", "storagelowspace"}


def _dahua_camera_code(code: str) -> bool:
    """A Dahua code whose index is a video channel: video/audio analytics of one input."""
    low = code.lower()
    return (low.startswith(("video", "audio", "smartmotion", "face", "human"))
            or low.endswith("detection"))


# Chatter a Dahua unit emits that is not an occurrence (compared case-insensitively).
# With codes=[All] these also arrive: per-file recording notices, motion/IVS metadata
# frames and our own RTSP/snapshot sessions are not occurrences.
DAHUA_NON_EVENTS = {"heartbeat", "keepalive", "timechange", "ntpadjusttime",
                    "newfile", "mdresult", "videomotioninfo", "intelliframe",
                    "rtspsessiondisconnect", "snapmanual"}

_NS = re.compile(r"\{.*?\}")


@dataclass
class Alarm:
    """What an alarm means, independent of the path it arrived by.

    ``raw_time`` is the recorder's own time text, or None when the format carries
    none. ``burst_key`` identifies a continuing alarm for burst collapse. ``phase`` is
    "start" or "end" for a fault/safety alarm that has an end (END_TYPES), else None;
    ``pair_key`` is then the burst key of the start (the same for both halves).
    """
    vendor: str
    channel: str | None
    event_type: str
    burst_key: tuple
    raw_time: str | None
    payload: dict = field(default_factory=dict)
    phase: str | None = None
    pair_key: tuple | None = None


def _pairing(scope_key, event_type: str) -> tuple[tuple, str | None, tuple | None]:
    """(burst_key, phase, pair_key) for an event of ``event_type`` on ``scope_key``."""
    burst_key = (scope_key, event_type)
    start = END_TYPES.get(event_type)
    if start is not None:
        return burst_key, "end", (scope_key, start)
    if event_type in PAIRED_START_TYPES:
        return burst_key, "start", burst_key
    return burst_key, None, None


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
    state = (_child_text(root, "eventState") or "").strip().lower()

    # Hikvision keeps alertStream warm by emitting videoloss with activePostCount 0
    # whenever nothing is happening (usually inactive). A genuine video-loss alarm, and
    # its genuine end, carry a non-zero count. Dropping all videoloss would hide a real
    # fault; keeping all of it would fill the database with heartbeats. The count is the
    # discriminator, for the start and the end alike.
    active_post = (_child_text(root, "activePostCount") or "").strip()
    if etype_raw.lower() == "videoloss" and active_post in ("0", ""):
        return None
    if not etype_raw or etype_raw.lower() in HIK_NON_EVENTS:
        return None

    if state == "inactive":
        # The end of a fault/safety alarm is an event (restore, tamper end, input
        # reset, camera back); the end of anything else is not an occurrence.
        etype = HIK_END_TYPE_MAP.get(etype_raw.lower())
        if etype is None:
            return None
    else:
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
    smart_native = (state != "inactive"
                    and (etype_raw.lower() in HIK_SMART_TYPES or bool(targets)))

    scope_key = (channel if channel is not None
                 else f"recorder:{native_channel or ''}:{native_input}")
    burst_key, phase, pair_key = _pairing(scope_key, etype)
    if state:
        scope["eventState"] = state
    return Alarm(
        vendor="hikvision",
        channel=channel,
        event_type=etype,
        burst_key=burst_key,
        raw_time=_child_text(root, "dateTime"),
        payload={"vendor": "hikvision", "eventType": etype_raw,
                 "native_code": etype_raw,
                 "native_ai": smart_native,
                 "targets": targets,
                 "eventDescription": _child_text(root, "eventDescription"),
                 "activePostCount": _child_text(root, "activePostCount"),
                 **scope},
        phase=phase,
        pair_key=pair_key,
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
    if code.lower() in DAHUA_NON_EVENTS:
        return None
    action = (fields.get("action") or fields.get("Action") or "").lower()
    if action == "stop":
        # The end of a fault/safety alarm is an event; the end of anything else is not.
        etype = DAHUA_END_CODE_MAP.get(code)
        if etype is None:
            return None
    elif action in ("start", "pulse", ""):
        etype = DAHUA_EVENT_CODE_MAP.get(code) or code.lower()
    else:
        return None                       # State (a keep-alive) or unknown: not an occurrence

    scope: dict = {}
    index = fields.get("index", fields.get("Index"))
    mapped = code in DAHUA_EVENT_CODE_MAP
    if code in DAHUA_RECORDER_SCOPED_CODES or code.lower() in DAHUA_RECORDER_LEVEL_CODES:
        channel = None
        scope = {"recorder_scoped": True, "native_index": index}
    elif not mapped and not _dahua_camera_code(code):
        # A code we do not know: its index is not guessed onto a camera.
        channel = None
        scope = {"channel_unknown": True, "native_index": index}
    else:
        # index is 0-based on the wire; channels are 1-based everywhere else.
        try:
            channel = str(int(str(index).strip()) + 1)
        except (TypeError, ValueError):
            channel = None            # no usable index: unknown, never camera 1
            scope = {"channel_unknown": True, "native_index": index}

    scope_key = channel if channel is not None else f"recorder:{index}"
    burst_key, phase, pair_key = _pairing(scope_key, etype)
    return Alarm(
        vendor="dahua",
        channel=channel,
        event_type=etype,
        burst_key=burst_key,
        raw_time=fields.get("dateTime") or fields.get("DateTime") or None,
        payload={"vendor": "dahua", "code": code, "action": action,
                 "data": (data[:500] if data is not None else None), **scope},
        phase=phase,
        pair_key=pair_key,
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
        # pair key -> (phase, admitted at, burst key) of the last admitted start/end.
        self._phase: dict = {}
        self._lock = threading.Lock()

    def admit(self, key, now: float, pair=None, phase: str | None = None) -> bool:
        """Admit ``key`` at ``now``. ``pair``/``phase`` (Alarm.pair_key/phase) pair the
        start and the end of a fault/safety alarm: an end is admitted once per start (its
        repeats are not new occurrences, and an end with no start seen since this process
        began is admitted once), and it re-arms the start so the next loss is admitted at
        once rather than collapsed into the burst of the previous one."""
        with self._lock:
            if phase == "end" and pair is not None:
                prior = self._phase.get(pair)
                if prior is not None and prior[0] == "end":
                    return False
                self._phase[pair] = ("end", now, key)
                self._last.pop(pair, None)
                self._prune(now)
                return True
            last = self._last.get(key)
            if last is not None and now - last < self.window:
                return False
            self._last[key] = now
            if phase == "start" and pair is not None:
                self._phase[pair] = ("start", now, key)
            self._prune(now)
            return True

    def _prune(self, now: float) -> None:
        if len(self._last) > self.max_keys:
            cutoff = now - self.window
            self._last = {k: v for k, v in self._last.items() if v >= cutoff}
        if len(self._phase) > self.max_keys:
            cutoff = now - self.window
            self._phase = {k: v for k, v in self._phase.items() if v[1] >= cutoff}

    def forget(self, key, now: float) -> None:
        """Undo an admission made at ``now`` (the event was not delivered)."""
        with self._lock:
            if self._last.get(key) == now:
                del self._last[key]
            for pair, (phase, at, admitted) in list(self._phase.items()):
                if at == now and admitted == key:
                    del self._phase[pair]
