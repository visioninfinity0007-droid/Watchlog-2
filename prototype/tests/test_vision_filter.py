#!/usr/bin/env python3
"""
False-alarm filter tests.

The filter decides which events a customer ever hears about, so its two
failure modes are asymmetric and both need pinning down:

  * Discarding a real incident is invisible. Nobody reports the alert
    they never got, and a site that has gone quiet looks exactly like a
    site that is safe. This is the dangerous one.
  * Keeping a false alarm is merely annoying.

So the rule is FAIL OPEN, and most of what follows checks that the filter
keeps events whenever it is not certain: no model, no image, unreadable
image, inference crash.

ultralytics is stubbed rather than installed. Loading real weights pulls
in torch, needs several hundred MB and a network, and would make these
tests slow and flaky - while testing ultralytics rather than our logic.
What we actually need to prove is the decision layer: which detections
mean keep, which mean discard, and what happens when the model misbehaves.

    python prototype/tests/test_vision_filter.py
    pytest -q prototype/tests/test_vision_filter.py
"""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))


# ---------------------------------------------------------------------
# a real, tiny JPEG - the filter runs PIL over it before inference
# ---------------------------------------------------------------------

def make_jpeg() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (20, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------
# ultralytics stub
# ---------------------------------------------------------------------

class FakeBox:
    def __init__(self, cls, conf, box=(1, 2, 3, 4)):
        self.cls = [cls]
        self.conf = [conf]
        self.xyxy = [list(box)]


class FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class FakeYOLO:
    """Stands in for ultralytics.YOLO. `script` drives each call."""
    script = []          # list of "boxes" lists, or an Exception to raise
    load_error = None

    def __init__(self, name):
        if FakeYOLO.load_error:
            raise FakeYOLO.load_error
        self.name = name

    def predict(self, img, **kw):
        item = FakeYOLO.script.pop(0) if FakeYOLO.script else []
        if isinstance(item, Exception):
            raise item
        # Honour the caller's class filter, as the real model does.
        wanted = kw.get("classes")
        if wanted is not None:
            item = [b for b in item if int(b.cls[0]) in set(wanted)]
        return [FakeResult(item)]


def install_stub():
    mod = types.ModuleType("ultralytics")
    mod.YOLO = FakeYOLO
    sys.modules["ultralytics"] = mod


def fresh_detector(**kw):
    """A detector with the stub installed and a clean log."""
    install_stub()
    FakeYOLO.load_error = None
    FakeYOLO.script = []
    import vision
    logs = []
    return vision.Detector(log=logs.append, **kw), logs


# ---------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("a person in frame is kept, and the objects are attached")
def t_person():
    d, _ = fresh_detector()
    FakeYOLO.script = [[FakeBox(0, 0.91)]]
    keep, found = d.classify_event(make_jpeg())
    assert keep is True, "an event containing a person was discarded"
    assert found and found[0].label == "person", f"expected person, got {found}"
    assert found[0].confidence == 0.91
    return "kept, labelled person @0.91"


@case("car and motorcycle are both tracked classes")
def t_vehicles():
    for cls, label in ((2, "car"), (3, "motorcycle")):
        d, _ = fresh_detector()
        FakeYOLO.script = [[FakeBox(cls, 0.7)]]
        keep, found = d.classify_event(make_jpeg())
        assert keep and found[0].label == label, f"{label} not kept"
    return "car and motorcycle both kept"


@case("an empty frame is discarded as a false alarm")
def t_empty():
    d, _ = fresh_detector()
    FakeYOLO.script = [[]]
    keep, found = d.classify_event(make_jpeg())
    assert keep is False, "an empty frame was reported as an incident"
    assert found == []
    assert d.stats["discarded"] == 1
    return "discarded"


@case("a dog is not an incident (only person/car/motorcycle count)")
def t_dog():
    d, _ = fresh_detector()
    FakeYOLO.script = [[FakeBox(16, 0.98)]]      # 16 = dog in COCO
    keep, _ = d.classify_event(make_jpeg())
    assert keep is False, "a dog at high confidence was reported as an incident"
    return "discarded - class filter holds even at 0.98 confidence"


@case("a low-confidence person is discarded")
def t_low_conf():
    d, _ = fresh_detector(confidence=0.5)
    FakeYOLO.script = [[FakeBox(0, 0.20)]]
    keep, _ = d.classify_event(make_jpeg())
    assert keep is False, "a 0.20-confidence guess was reported as an incident"
    return "discarded below threshold"


@case("FAIL OPEN: inference crashing keeps the event")
def t_inference_crash():
    d, logs = fresh_detector()
    FakeYOLO.script = [RuntimeError("CUDA exploded")]
    keep, found = d.classify_event(make_jpeg())
    assert keep is True, "a crashed detector silently swallowed an event"
    assert found is None
    assert any("keeping event" in l for l in logs), "crash was not logged"
    return "kept, and logged"


@case("FAIL OPEN: an unreadable frame keeps the event")
def t_bad_image():
    d, _ = fresh_detector()
    keep, found = d.classify_event(b"this is not a JPEG at all")
    assert keep is True, "a corrupt frame silently swallowed an event"
    assert found is None
    return "kept"


@case("FAIL OPEN: weights that will not load disable the filter, not the agent")
def t_load_failure():
    install_stub()
    FakeYOLO.load_error = OSError("no weights and no network")
    import vision
    logs = []
    d = vision.Detector(log=logs.append)
    keep, _ = d.classify_event(make_jpeg())
    assert keep is True, "unloadable weights caused events to be dropped"
    assert d.available is False
    assert any("DISABLED" in l for l in logs)
    return "filter disabled, events still flow"


@case("an event with no image is always kept (faults, video loss)")
def t_no_image():
    d, _ = fresh_detector()
    keep, found = d.classify_event(None)
    assert keep is True, "a fault event with no frame was filtered away"
    assert found is None
    return "kept - these are the events that say a camera died"


@case("the model is loaded once, not per event")
def t_single_load():
    d, logs = fresh_detector()
    FakeYOLO.script = [[FakeBox(0, 0.9)], [FakeBox(0, 0.9)], [FakeBox(0, 0.9)]]
    for _ in range(3):
        d.classify_event(make_jpeg())
    loads = [l for l in logs if "loaded" in l]
    assert len(loads) == 1, f"model loaded {len(loads)} times, expected once"
    return "loaded once across three events"


@case("config can narrow the class list but not widen it past the scope")
def t_build_classes():
    install_stub()
    import vision
    logs = []
    cfg = types.SimpleNamespace(detect=True, detect_model=None,
                                detect_confidence=None,
                                detect_classes="person,elephant")
    d = vision.build(cfg, log=logs.append)
    assert set(d.classes.values()) == {"person"}, f"got {d.classes}"
    assert any("elephant" in l for l in logs), "unsupported class not reported"
    return "narrowed to person; elephant refused and logged"


@case("detection can be turned off entirely")
def t_build_disabled():
    install_stub()
    import vision
    cfg = types.SimpleNamespace(detect=False)
    assert vision.build(cfg, log=lambda *_: None) is None
    return "returns None, collector then reports everything"


def run() -> int:
    print("False-alarm filter (YOLOv8n decision layer)")
    print("=" * 66)
    passed = failed = 0
    for name, fn in CASES:
        try:
            print(f"  PASS  {name}\n          {fn()}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}\n          {e}")
            failed += 1
        except Exception as e:                       # noqa: BLE001
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}")
            failed += 1
    print("=" * 66)
    print(f"  {passed} passed, {failed} failed")
    return 1 if failed else 0


def test_vision_filter():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
