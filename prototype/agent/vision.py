"""
Local object detection — the false-alarm filter.

Why this exists
---------------
A motion event from a DVR means "some pixels changed". Rain, headlights
sweeping a wall, a cat, a flag, an IR lamp cutting in at dusk: all motion,
none of them worth a line in someone's morning report. Left unfiltered, a
site produces hundreds of events a night and the report becomes noise,
which is the failure mode this product exists to avoid.

So every event that carries a still is shown to YOLOv8n before it is
spooled. If the frame contains none of the classes the scope of work
tracks - person, car, motorcycle - the event is discarded as a false
alarm and never leaves the site.

Two constraints from the scope of work, both deliberate:

  * Detection runs ON SITE. Frames are never sent to a cloud model. The
    only image that ever leaves the building is the still attached to an
    event that already passed the filter.
  * Exactly three classes. Not "everything COCO knows". A dog at 3am is
    not an incident, and widening this list silently is how a report
    becomes noise again.

Failure policy: FAIL OPEN. If ultralytics is not installed, the weights
cannot be loaded, or inference throws, every event is kept and the reason
is logged once. A broken filter must never silently swallow real
incidents - under-reporting is far worse than over-reporting here, and a
site that goes quiet looks identical to a site that is safe.

The import of ultralytics is deferred to first use. It pulls in torch,
which costs seconds and hundreds of MB; agents running with detection
disabled should not pay that, and the frozen exe should not carry it
unless asked.
"""

from __future__ import annotations

import io
import threading
import time

# Classes the scope of work commits to, mapped to COCO ids as used by the
# stock YOLOv8 weights. Anything not listed is not an incident.
COCO_KEEP = {0: "person", 2: "car", 3: "motorcycle"}

DEFAULT_MODEL = "yolov8n.pt"
DEFAULT_CONFIDENCE = 0.35
DEFAULT_IMAGE_SIZE = 640


class Detection:
    """One object the model found."""

    __slots__ = ("label", "confidence", "box")

    def __init__(self, label: str, confidence: float, box: tuple):
        self.label = label
        self.confidence = round(float(confidence), 3)
        self.box = tuple(int(v) for v in box)      # x1, y1, x2, y2

    def as_dict(self) -> dict:
        return {"label": self.label, "confidence": self.confidence,
                "box": list(self.box)}

    def __repr__(self) -> str:                      # pragma: no cover
        return f"{self.label}@{self.confidence}"


class Detector:
    """
    YOLOv8n, loaded once and reused.

    Thread-safe: the collector runs in its own thread and a second one may
    call in during a probe. Ultralytics models are not documented as
    re-entrant, so inference is serialised behind a lock. The cost is
    irrelevant at the event rates a DVR produces.
    """

    def __init__(self, model: str = DEFAULT_MODEL,
                 confidence: float = DEFAULT_CONFIDENCE,
                 image_size: int = DEFAULT_IMAGE_SIZE,
                 classes: dict | None = None,
                 log=print):
        self.model_name = model
        self.confidence = float(confidence)
        self.image_size = int(image_size)
        self.classes = dict(classes or COCO_KEEP)
        self._log = log
        self._model = None
        self._lock = threading.Lock()
        self._unavailable = None       # reason string once we give up
        self._warned = False
        self.stats = {"seen": 0, "kept": 0, "discarded": 0, "errors": 0,
                      "ms_total": 0.0}

    # -- availability ---------------------------------------------------

    @property
    def available(self) -> bool:
        return self._unavailable is None

    def _load(self):
        """Import and load on first use. Returns the model or None."""
        if self._model is not None or self._unavailable:
            return self._model
        try:
            from ultralytics import YOLO           # noqa: PLC0415
        except Exception as e:                     # noqa: BLE001
            self._fail(f"ultralytics not installed ({type(e).__name__}). "
                       f"Install it with:  pip install ultralytics")
            return None
        try:
            t0 = time.monotonic()
            model = YOLO(self.model_name)
            self._log(f"vision: loaded {self.model_name} in "
                      f"{time.monotonic() - t0:.1f}s "
                      f"(classes: {', '.join(sorted(self.classes.values()))})")
            self._model = model
        except Exception as e:                     # noqa: BLE001
            # Most often: no weights on disk and no network to fetch them.
            self._fail(f"could not load {self.model_name}: "
                       f"{type(e).__name__}: {str(e)[:160]}")
            return None
        return self._model

    def _fail(self, reason: str) -> None:
        self._unavailable = reason
        if not self._warned:
            self._warned = True
            self._log(f"vision: DISABLED - {reason}")
            self._log("vision: every event will be kept unfiltered. False "
                      "alarms will reach the report.")

    # -- inference ------------------------------------------------------

    def detect(self, jpeg: bytes) -> list:
        """
        Objects of interest in this frame.

        Returns [] when the frame is clean. Raises nothing - on any
        failure it reports "no opinion" by returning None, which callers
        must treat as keep, not discard.
        """
        model = self._load()
        if model is None:
            return None

        try:
            from PIL import Image                  # noqa: PLC0415
            img = Image.open(io.BytesIO(jpeg))
            img.load()
            if img.mode != "RGB":
                img = img.convert("RGB")
        except Exception as e:                     # noqa: BLE001
            self.stats["errors"] += 1
            self._log(f"vision: unreadable frame ({type(e).__name__}); keeping event")
            return None

        t0 = time.monotonic()
        try:
            with self._lock:
                results = model.predict(
                    img, imgsz=self.image_size, conf=self.confidence,
                    classes=sorted(self.classes), verbose=False)
        except Exception as e:                     # noqa: BLE001
            self.stats["errors"] += 1
            self._log(f"vision: inference failed ({type(e).__name__}: "
                      f"{str(e)[:120]}); keeping event")
            return None
        self.stats["ms_total"] += (time.monotonic() - t0) * 1000

        found = []
        for r in results or []:
            boxes = getattr(r, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                try:
                    cls = int(b.cls[0])
                    conf = float(b.conf[0])
                    xy = [float(v) for v in b.xyxy[0]]
                except Exception:                  # noqa: BLE001
                    continue
                label = self.classes.get(cls)
                if label and conf >= self.confidence:
                    found.append(Detection(label, conf, xy))
        return found

    def classify_event(self, jpeg: bytes | None) -> tuple:
        """
        Decide the fate of one event.

        Returns (keep: bool, detections: list[Detection] | None).

        No image means no opinion, so keep it. That is the common case for
        device faults and video-loss events, which carry no frame and must
        never be filtered away - they are the events that say a camera has
        stopped working.
        """
        self.stats["seen"] += 1
        if not jpeg:
            self.stats["kept"] += 1
            return True, None

        found = self.detect(jpeg)
        if found is None:                # detector had no opinion -> keep
            self.stats["kept"] += 1
            return True, None
        if found:
            self.stats["kept"] += 1
            return True, found
        self.stats["discarded"] += 1
        return False, []

    def summary(self) -> str:
        s = self.stats
        if not s["seen"]:
            return "vision: nothing classified yet"
        avg = s["ms_total"] / max(1, s["seen"] - s["discarded"] + s["discarded"])
        return (f"vision: {s['seen']} classified, {s['kept']} kept, "
                f"{s['discarded']} discarded as false alarms, "
                f"{s['errors']} errors, {avg:.0f}ms avg")


def build(cfg, log=print):
    """
    Detector from config, or None when the operator has turned it off.

    Reads, all optional:
        detect            true/false  (default true)
        detect_model      yolov8n.pt
        detect_confidence 0.35
        detect_classes    person,car,motorcycle
    """
    enabled = getattr(cfg, "detect", True)
    if not enabled:
        log("vision: disabled by config; every event will be reported")
        return None

    classes = getattr(cfg, "detect_classes", None)
    mapping = COCO_KEEP
    if classes:
        wanted = {c.strip().lower() for c in str(classes).split(",") if c.strip()}
        mapping = {k: v for k, v in COCO_KEEP.items() if v in wanted}
        unknown = wanted - set(COCO_KEEP.values())
        if unknown:
            log(f"vision: ignoring unsupported class(es) {', '.join(sorted(unknown))}"
                f" - the scope covers {', '.join(COCO_KEEP.values())}")
        if not mapping:
            log("vision: detect_classes left nothing to detect; disabling filter")
            return None

    return Detector(
        model=getattr(cfg, "detect_model", None) or DEFAULT_MODEL,
        confidence=float(getattr(cfg, "detect_confidence", None) or DEFAULT_CONFIDENCE),
        classes=mapping, log=log)
