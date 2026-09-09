"""WatchLog local analytics engine.

The engine is deliberately local-only and has no cloud or recorder code. It
accepts detections from the packaged on-site object detector, keeps a short
per-camera centroid track, and turns movement into business measurements.

Tracking is not identification. A track exists only for a few seconds on one
camera so the same visible object is not counted once per sampled frame. No
face embeddings, biometric identifiers, or cross-camera re-identification are
created.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TRACK_TTL_SECONDS = 8.0
RULE_STATE_TTL_SECONDS = 30.0
MATCH_DISTANCE = 0.16
LINE_EPSILON = 0.002
SEGMENT_EPSILON = 1e-9
# Occupancy is especially sensitive to one-frame detector misses. Establish the
# first observed baseline immediately, but require a changed count to be seen in
# two consecutive inference samples before it becomes a measurement.
OCCUPANCY_CHANGE_CONFIRM_SAMPLES = 2


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def center(box, frame_size):
    """Detection box centre normalized to 0..1."""
    w, h = frame_size
    x1, y1, x2, y2 = box
    return (((x1 + x2) / 2) / max(1, w), ((y1 + y2) / 2) / max(1, h))


def line_side(p, a, b):
    """Signed 2D cross product: which side of directed line a->b is p?"""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _orientation(a, b, c):
    return line_side(c, a, b)


def _between(a, b, x, eps=SEGMENT_EPSILON):
    return min(a, b) - eps <= x <= max(a, b) + eps


def _on_segment(a, b, p):
    return (abs(_orientation(a, b, p)) <= SEGMENT_EPSILON
            and _between(a[0], b[0], p[0])
            and _between(a[1], b[1], p[1]))


def segments_intersect(a, b, c, d):
    """Whether finite segment a-b intersects finite segment c-d."""
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if ((o1 > SEGMENT_EPSILON and o2 < -SEGMENT_EPSILON)
            or (o1 < -SEGMENT_EPSILON and o2 > SEGMENT_EPSILON)):
        if ((o3 > SEGMENT_EPSILON and o4 < -SEGMENT_EPSILON)
                or (o3 < -SEGMENT_EPSILON and o4 > SEGMENT_EPSILON)):
            return True
    return (_on_segment(a, b, c) or _on_segment(a, b, d)
            or _on_segment(c, d, a) or _on_segment(c, d, b))


def point_in_polygon(point, polygon):
    """Ray casting; polygon points are normalized coordinates."""
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        crosses = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if crosses:
            inside = not inside
        j = i
    return inside


def _hm(value):
    hh, mm = str(value).split(":", 1)
    hh, mm = int(hh), int(mm)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("invalid time")
    return hh * 60 + mm


def _day_key(index):
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][index % 7]


def schedule_contains(schedule_json, when: datetime, timezone_name: str) -> bool:
    """Return whether *when* is inside a weekly schedule.

    Canonical JSON:
      {"days":{"mon":[["08:00","18:00"]], ...}}

    Overnight windows are supported correctly across day boundaries. A Monday
    22:00 to 06:00 window therefore includes Tuesday at 02:00. An empty or
    missing schedule means always active.
    """
    if not schedule_json or not schedule_json.get("days"):
        return True
    try:
        local = when.astimezone(ZoneInfo(timezone_name or "Asia/Karachi"))
    except Exception:
        local = when.astimezone(ZoneInfo("Asia/Karachi"))

    days = schedule_json.get("days") or {}
    minute = local.hour * 60 + local.minute
    today = local.weekday()

    # Windows declared on the current day.
    for raw in days.get(_day_key(today)) or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        try:
            start, end = _hm(raw[0]), _hm(raw[1])
        except Exception:
            continue
        if start == end:
            # Equal endpoints mean a full-day window. This is less surprising
            # than silently treating 00:00 -> 00:00 as disabled.
            return True
        if start < end and start <= minute < end:
            return True
        if start > end and minute >= start:
            return True

    # Early-morning tail of an overnight window declared yesterday.
    for raw in days.get(_day_key(today - 1)) or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        try:
            start, end = _hm(raw[0]), _hm(raw[1])
        except Exception:
            continue
        if start > end and minute < end:
            return True
    return False


def load_config(path: Path) -> dict:
    if not path.exists():
        return {"version": 0, "config": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data
    except Exception:
        return {"version": 0, "config": None}


def save_config(path: Path, version: int, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": int(version), "config": config}, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


@dataclass
class Track:
    id: str
    label: str
    point: tuple
    first_seen: datetime
    last_seen: datetime


class CentroidTracker:
    """Small per-camera tracker: nearest centroid, same class, short TTL."""

    def __init__(self, max_distance=MATCH_DISTANCE, ttl=TRACK_TTL_SECONDS):
        self.max_distance = float(max_distance)
        self.ttl = float(ttl)
        self._tracks = {}
        self._counter = 0

    def update(self, channel: str, detections: list, frame_size, when: datetime):
        bucket = self._tracks.setdefault(str(channel), {})
        dead = [key for key, track in bucket.items()
                if (when - track.last_seen).total_seconds() > self.ttl]
        for key in dead:
            del bucket[key]

        observations = [(d.label, center(d.box, frame_size), d) for d in detections]
        unused = set(bucket)
        matched = []
        for label, point, detection in observations:
            best = None
            best_dist = self.max_distance
            for key in list(unused):
                track = bucket[key]
                if track.label != label:
                    continue
                distance = math.dist(point, track.point)
                if distance < best_dist:
                    best, best_dist = key, distance
            if best is None:
                self._counter += 1
                best = f"{channel}:{label}:{self._counter}"
                bucket[best] = Track(best, label, point, when, when)
            else:
                track = bucket[best]
                track.point = point
                track.last_seen = when
                unused.discard(best)
            matched.append((bucket[best], detection))
        return matched

    def active(self, channel: str, label: str | None = None):
        values = list(self._tracks.get(str(channel), {}).values())
        return [track for track in values if label is None or track.label == label]


class AnalyticsEngine:
    def __init__(self, log=print):
        self.log = log
        self.tracker = CentroidTracker()
        self.config = None
        self.schedules = {}
        self.cameras = {}
        self.state = {}      # (rule_id, track_id) -> rule-specific state
        self.occupancy = {}  # rule_id -> last emitted stable count
        self.occupancy_pending = {}  # rule_id -> {count, samples}

    def configure(self, config: dict | None):
        self.config = config or None
        self.schedules = {str(s["id"]): s for s in (config or {}).get("schedules", [])}
        self.cameras = {str(c["channel"]): c for c in (config or {}).get("cameras", [])}
        # A geometry/config change invalidates previous crossing/occupancy state.
        self.state.clear()
        self.occupancy.clear()
        self.occupancy_pending.clear()

    def sample_plan(self):
        """List (channel, minimum sampling seconds) for enabled cameras."""
        plan = []
        for channel, camera in self.cameras.items():
            rules = [rule for rule in camera.get("rules", []) if rule.get("enabled", True)]
            # Health is platform-level and does not require video inference.
            infer_rules = [rule for rule in rules if rule.get("rule_type") != "health"]
            if not infer_rules:
                continue
            interval = min(max(0.5, float(rule.get("sample_seconds") or 2.0))
                           for rule in infer_rules)
            plan.append((channel, interval))
        return plan

    def _schedule_allows(self, rule, when):
        sid = rule.get("schedule_id")
        if not sid:
            # An after-hours rule without a schedule cannot define after hours.
            return rule.get("rule_type") != "schedule_activity"
        schedule = self.schedules.get(str(sid))
        if not schedule or not schedule.get("enabled", True):
            return True
        inside = schedule_contains(
            schedule.get("schedule") or {}, when,
            schedule.get("timezone") or (self.config or {}).get("timezone") or "Asia/Karachi")
        mode = (rule.get("direction") or {}).get("schedule_mode")
        if rule.get("rule_type") == "schedule_activity":
            mode = mode or "outside"
        return (not inside) if mode == "outside" else inside

    @staticmethod
    def _event(rule, channel, track, event_type, when, direction=None,
               duration=None, metadata=None, confidence=None):
        raw = f"{rule['id']}|{track.id}|{event_type}|{iso(when)}|{direction or ''}"
        dedupe = hashlib.sha256(raw.encode()).hexdigest()
        meta = dict(metadata or {})
        # Confidence rides in metadata so the existing analytic-events ingestion stores it
        # unchanged; the server-side operations-incident bridge reads metadata->>'confidence'
        # to apply the rule's confidence gate. Detection quality is never overstated.
        if confidence is not None:
            meta.setdefault("confidence", round(float(confidence), 3))
        return {
            "rule_id": str(rule["id"]),
            "channel": str(channel),
            "event_type": event_type,
            "object_class": track.label,
            "track_key": track.id,
            "direction": direction,
            "occurred_at": iso(when),
            "duration_seconds": round(float(duration), 2) if duration is not None else None,
            "dedupe_key": dedupe,
            "metadata": meta,
        }

    def _prune_rule_state(self, when):
        stale = []
        for key, state in self.state.items():
            last = state.get("last_seen")
            if last and (when - last).total_seconds() > RULE_STATE_TTL_SECONDS:
                stale.append(key)
        for key in stale:
            del self.state[key]

    def process(self, channel: str, detections: list, frame_size,
                when: datetime | None = None):
        when = when or datetime.now(timezone.utc)
        camera = self.cameras.get(str(channel))
        if not camera or not camera.get("analytics_enabled", True):
            return []

        matched = self.tracker.update(str(channel), detections or [], frame_size, when)
        emitted = []
        rules = [rule for rule in camera.get("rules", []) if rule.get("enabled", True)]
        self._prune_rule_state(when)

        for rule in rules:
            allowed_classes = set(rule.get("object_classes") or [])
            rule_type = rule.get("rule_type")
            if rule_type == "health":
                continue
            geometry = rule.get("geometry") or {}

            for track, detection in matched:
                if allowed_classes and track.label not in allowed_classes:
                    continue
                conf = getattr(detection, "confidence", None)
                key = (str(rule["id"]), track.id)
                state = self.state.setdefault(key, {})
                previous_point = state.get("point")
                state["point"] = track.point
                state["last_seen"] = when

                if rule_type == "line_crossing":
                    points = geometry.get("points") or []
                    if len(points) != 2:
                        continue
                    side = line_side(track.point, points[0], points[1])
                    if abs(side) <= LINE_EPSILON:
                        continue
                    previous_side = state.get("side")
                    state["side"] = side
                    if previous_side is None or previous_side * side >= 0:
                        continue
                    # A configured line is a finite segment. Crossing the
                    # infinite extension outside the drawn line must not count.
                    if previous_point is None or not segments_intersect(
                            previous_point, track.point, points[0], points[1]):
                        continue
                    if not self._schedule_allows(rule, when):
                        continue
                    mapping = rule.get("direction") or {}
                    direction = (mapping.get("negative_to_positive", "in")
                                 if previous_side < 0 < side else
                                 mapping.get("positive_to_negative", "out"))
                    emitted.append(self._event(
                        rule, channel, track, "line_crossing", when, direction, confidence=conf))

                elif rule_type in ("zone_entry", "zone_exit", "zone_dwell", "vehicle_activity"):
                    polygon = geometry.get("points") or []
                    inside = point_in_polygon(track.point, polygon)
                    was_inside = state.get("inside")
                    state["inside"] = inside
                    if inside and not was_inside:
                        state["entered_at"] = when
                        state["dwell_emitted"] = False
                        if rule_type in ("zone_entry", "vehicle_activity") and self._schedule_allows(rule, when):
                            emitted.append(self._event(
                                rule, channel, track,
                                "vehicle_activity" if rule_type == "vehicle_activity" else "zone_entry",
                                when, confidence=conf))
                    if was_inside and not inside and rule_type == "zone_exit" and self._schedule_allows(rule, when):
                        emitted.append(self._event(
                            rule, channel, track, "zone_exit", when, confidence=conf))
                    if not inside:
                        state.pop("entered_at", None)
                        state["dwell_emitted"] = False
                    elif (rule_type == "zone_dwell" and state.get("entered_at")
                          and not state.get("dwell_emitted")):
                        elapsed = (when - state["entered_at"]).total_seconds()
                        threshold = int(rule.get("dwell_seconds") or 60)
                        if elapsed >= threshold and self._schedule_allows(rule, when):
                            emitted.append(self._event(
                                rule, channel, track, "dwell_completed", when,
                                duration=elapsed, confidence=conf))
                            state["dwell_emitted"] = True

                elif rule_type == "schedule_activity":
                    active = self._schedule_allows(rule, when)
                    if active and not state.get("schedule_emitted"):
                        emitted.append(self._event(
                            rule, channel, track, "schedule_activity", when, confidence=conf))
                        state["schedule_emitted"] = True
                    elif not active:
                        state["schedule_emitted"] = False

            # ---- per-rule temporal primitives: presence/absence, queue/wait ----
            # These evaluate the whole zone each sample (even with zero tracks), so an
            # EMPTY zone can raise an "expected present / unattended" or "empty too long"
            # exception. Absence is a STATE over monitored time, never a fabricated event.
            if rule_type in ("zone_presence", "zone_absence"):
                polygon = geometry.get("points") or []
                present = any((not allowed_classes or t.label in allowed_classes)
                              and point_in_polygon(t.point, polygon) for t, _ in matched)
                rkey = (str(rule["id"]), "__zone__")
                rstate = self.state.setdefault(rkey, {})
                rstate["last_seen"] = when
                threshold = int(rule.get("dwell_seconds") or 60)
                if present:
                    rstate["clear_since"] = None
                    rstate["absence_emitted"] = False
                else:
                    if rstate.get("clear_since") is None:
                        rstate["clear_since"] = when          # start the empty clock now
                    empty_for = (when - rstate["clear_since"]).total_seconds()
                    # zone_presence = expected present -> fire only inside the schedule window;
                    # zone_absence = empty too long regardless of schedule.
                    window_ok = self._schedule_allows(rule, when) if rule_type == "zone_presence" else True
                    if window_ok and empty_for >= threshold and not rstate.get("absence_emitted"):
                        klass = next(iter(allowed_classes)) if allowed_classes else "person"
                        synthetic = Track(f"{channel}:{rule['id']}:absence", klass, (0, 0), when, when)
                        et = "expected_absent" if rule_type == "zone_presence" else "zone_empty"
                        emitted.append(self._event(
                            rule, channel, synthetic, et, when,
                            duration=empty_for, metadata={"empty_seconds": round(empty_for, 1)}))
                        rstate["absence_emitted"] = True

            elif rule_type == "queue_wait":
                polygon = geometry.get("points") or []
                count = sum(1 for t, _ in matched
                            if (not allowed_classes or t.label in allowed_classes)
                            and point_in_polygon(t.point, polygon))
                rkey = (str(rule["id"]), "__queue__")
                rstate = self.state.setdefault(rkey, {})
                rstate["last_seen"] = when
                min_len = int(rule.get("occupancy_min") or 1)
                wait_seconds = int(rule.get("dwell_seconds") or 60)
                if count >= min_len:
                    if rstate.get("over_since") is None:
                        rstate["over_since"] = when
                    over_for = (when - rstate["over_since"]).total_seconds()
                    if over_for >= wait_seconds and not rstate.get("queue_emitted") and self._schedule_allows(rule, when):
                        synthetic = Track(f"{channel}:{rule['id']}:queue", "person", (0, 0), when, when)
                        emitted.append(self._event(
                            rule, channel, synthetic, "queue_wait", when,
                            duration=over_for, metadata={"count": count, "min_length": min_len}))
                        rstate["queue_emitted"] = True
                else:
                    rstate["over_since"] = None
                    rstate["queue_emitted"] = False

            elif rule_type == "occupancy":
                polygon = (rule.get("geometry") or {}).get("points") or []
                count = sum(
                    1 for track, _ in matched
                    if (not allowed_classes or track.label in allowed_classes)
                    and point_in_polygon(track.point, polygon))
                rule_id = str(rule["id"])
                stable = self.occupancy.get(rule_id)
                should_emit = False

                if stable is None:
                    # First sample establishes the baseline immediately.
                    should_emit = True
                elif count == stable:
                    # A single contradictory frame has recovered; discard it.
                    self.occupancy_pending.pop(rule_id, None)
                else:
                    pending = self.occupancy_pending.get(rule_id)
                    if pending and pending.get("count") == count:
                        pending["samples"] = int(pending.get("samples") or 0) + 1
                    else:
                        pending = {"count": count, "samples": 1}
                        self.occupancy_pending[rule_id] = pending
                    should_emit = pending["samples"] >= OCCUPANCY_CHANGE_CONFIRM_SAMPLES

                if should_emit and self._schedule_allows(rule, when):
                    synthetic = Track(
                        f"{channel}:occupancy", "person", (0, 0), when, when)
                    emitted.append(self._event(
                        rule, channel, synthetic, "occupancy", when,
                        metadata={"count": count}))
                    self.occupancy[rule_id] = count
                    self.occupancy_pending.pop(rule_id, None)

        return emitted