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


class OnnxDetector:
    """
    YOLOv8n via onnxruntime — the backend the shipped exe uses.

    Why not ultralytics in production: ultralytics drags in torch, over a
    gigabyte, which makes a one-file installer impractical. onnxruntime is
    ~15 MB and the model file is ~12 MB. Same model, same three classes,
    a fraction of the size.

    Same contract as Detector, including FAIL OPEN: if onnxruntime is
    missing, the model will not load, or inference throws, every event is
    kept and the reason is logged once. A broken filter must never
    silently swallow real incidents.

    The decode is written out rather than pulled from a library because
    the whole point of this path is to avoid the heavy library. YOLOv8's
    export produces one output tensor shaped (1, 4+classes, 8400): the
    first four rows are box centre-x, centre-y, width, height in the
    letterboxed 640-space, the rest are per-class scores.
    """

    def __init__(self, model_path, confidence=DEFAULT_CONFIDENCE,
                 image_size=DEFAULT_IMAGE_SIZE, classes=None, log=print):
        self.model_name = str(model_path)
        self.confidence = float(confidence)
        self.image_size = int(image_size)
        self.classes = dict(classes or COCO_KEEP)
        self._log = log
        self._sess = None
        self._unavailable = None
        self._warned = False
        self._lock = threading.Lock()
        self.stats = {"seen": 0, "kept": 0, "discarded": 0, "errors": 0,
                      "ms_total": 0.0}

    @property
    def available(self):
        return self._unavailable is None

    def _load(self):
        if self._sess is not None or self._unavailable:
            return self._sess
        import os
        if not os.path.exists(self.model_name):
            self._fail(f"model file not found: {self.model_name}")
            return None
        try:
            import onnxruntime as ort           # noqa: PLC0415
            self._np = __import__("numpy")
            t0 = time.monotonic()
            so = ort.SessionOptions()
            so.log_severity_level = 3
            self._sess = ort.InferenceSession(
                self.model_name, sess_options=so,
                providers=["CPUExecutionProvider"])
            self._inp = self._sess.get_inputs()[0].name
            self._log(f"vision: loaded {self.model_name} via onnxruntime in "
                      f"{time.monotonic() - t0:.1f}s "
                      f"(classes: {', '.join(sorted(self.classes.values()))})")
        except Exception as e:                   # noqa: BLE001
            self._fail(f"onnxruntime unavailable or model invalid: "
                       f"{type(e).__name__}: {str(e)[:160]}")
            return None
        return self._sess

    def _fail(self, reason):
        self._unavailable = reason
        if not self._warned:
            self._warned = True
            self._log(f"vision: DISABLED - {reason}")
            self._log("vision: every event will be kept unfiltered. False "
                      "alarms will reach the report.")

    def _letterbox(self, img):
        np = self._np
        s = self.image_size
        w, h = img.size
        scale = min(s / w, s / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        from PIL import Image                     # noqa: PLC0415
        resized = img.resize((nw, nh), Image.BILINEAR)
        canvas = Image.new("RGB", (s, s), (114, 114, 114))
        px, py = (s - nw) // 2, (s - nh) // 2
        canvas.paste(resized, (px, py))
        arr = np.asarray(canvas, dtype=np.float32) / 255.0
        arr = arr.transpose(2, 0, 1)[None, ...]   # NCHW
        return np.ascontiguousarray(arr), scale, px, py

    def detect(self, jpeg):
        sess = self._load()
        if sess is None:
            return None
        np = self._np
        try:
            import io
            from PIL import Image                 # noqa: PLC0415
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
            blob, scale, px, py = self._letterbox(img)
            with self._lock:
                out = sess.run(None, {self._inp: blob})[0]
        except Exception as e:                     # noqa: BLE001
            self.stats["errors"] += 1
            self._log(f"vision: inference failed ({type(e).__name__}: "
                      f"{str(e)[:120]}); keeping event")
            return None
        self.stats["ms_total"] += (time.monotonic() - t0) * 1000

        # out: (1, 4+C, 8400) -> (8400, 4+C)
        pred = out[0].transpose(1, 0)
        boxes = pred[:, :4]
        scores_all = pred[:, 4:]
        wanted = sorted(self.classes)
        found = []
        for cls in wanted:
            col = scores_all[:, cls]
            idx = self._np.where(col >= self.confidence)[0]
            for i in idx:
                cx, cy, bw, bh = boxes[i]
                x1 = (cx - bw / 2 - px) / scale
                y1 = (cy - bh / 2 - py) / scale
                x2 = (cx + bw / 2 - px) / scale
                y2 = (cy + bh / 2 - py) / scale
                found.append(Detection(self.classes[cls], float(col[i]),
                                       (x1, y1, x2, y2)))
        return found

    # classify_event / summary are identical to Detector's, so reuse them.
    classify_event = Detector.classify_event
    summary = Detector.summary


def build(cfg, log=print):
    """
    Detector from config, or None when the operator has turned it off.

    Backend choice, in order:
      1. An ONNX model (onnxruntime) - what the shipped exe uses, because
         it avoids torch. Chosen when detect_model ends in .onnx, or a
         yolov8n.onnx sits next to the agent, and onnxruntime is present.
      2. ultralytics (.pt) - the development path.
      3. Neither - fail open, every event reported.

    Reads, all optional:
        detect            true/false  (default true)
        detect_model      yolov8n.onnx  (or .pt for the ultralytics path)
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

    conf = float(getattr(cfg, "detect_confidence", None) or DEFAULT_CONFIDENCE)
    model = getattr(cfg, "detect_model", None)

    # Find a model to use. Prefer an explicit .onnx, then a yolov8n.onnx
    # sitting beside the agent (which is how the installer ships it), then
    # fall back to the ultralytics .pt path.
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    onnx_candidates = []
    if model and str(model).lower().endswith(".onnx"):
        onnx_candidates.append(model)
    onnx_candidates += [os.path.join(here, "yolov8n.onnx"),
                        os.path.join(os.getcwd(), "yolov8n.onnx")]
    onnx_model = next((p for p in onnx_candidates if os.path.exists(p)), None)

    if onnx_model:
        return OnnxDetector(onnx_model, confidence=conf, classes=mapping, log=log)

    # No ONNX model on disk. If onnxruntime is here but the model is not,
    # say so plainly - this is the common installer state until the model
    # is dropped in - then fall through to ultralytics (dev) or fail open.
    try:
        import onnxruntime  # noqa: F401,PLC0415
        log("vision: onnxruntime is present but no yolov8n.onnx was found; "
            "false-alarm filtering is OFF until the model is added. Data "
            "still flows - every event is reported unfiltered.")
    except Exception:       # noqa: BLE001
        pass

    return Detector(
        model=model or DEFAULT_MODEL, confidence=conf, classes=mapping, log=log)
