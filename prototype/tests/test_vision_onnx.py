#!/usr/bin/env python3
"""
Real ONNX inference test for the shipped detector (`OnnxDetector`).

Runs only when an exported model is present at `prototype/models/yolov8n.onnx`
(gitignored), so it SKIPS cleanly in CI and runs locally / at release time
where the model has been exported. Unlike test_vision_filter (which stubs the
model), this exercises the *actual* shipped path: load the ONNX model, run
inference through onnxruntime, and confirm a junk frame is discarded.

    python prototype/tests/test_vision_onnx.py
    pytest -q prototype/tests/test_vision_onnx.py

Optional: WATCHLOG_SELFTEST_IMAGE points at a real image with a
person/car/motorcycle to additionally prove that class is retained.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
MODEL = ROOT / "models" / "yolov8n.onnx"


def run() -> int:
    if not MODEL.exists():
        print(f"SKIP: no model at {MODEL.relative_to(ROOT.parent)} "
              f"(export it to run this real-inference test). CI-safe skip.")
        return 0
    try:
        import onnxruntime  # noqa: F401
        import numpy        # noqa: F401
        from PIL import Image
    except Exception as e:  # noqa: BLE001
        print(f"SKIP: ONNX runtime not importable ({type(e).__name__})")
        return 0

    from vision import OnnxDetector, DEFAULT_CONFIDENCE

    det = OnnxDetector(str(MODEL), confidence=DEFAULT_CONFIDENCE, log=print)
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (120, 120, 120)).save(buf, "JPEG")
    keep, dets = det.classify_event(buf.getvalue())

    assert det.available, f"model failed to load: {det._unavailable}"
    assert keep is False and dets == [], \
        f"a junk frame must be discarded as a false alarm; got keep={keep} dets={dets}"
    print("PASS: model loaded via onnxruntime, inference ran, junk frame discarded")

    img = os.environ.get("WATCHLOG_SELFTEST_IMAGE")
    if img and os.path.exists(img):
        with open(img, "rb") as f:
            k2, d2 = det.classify_event(f.read())
        labels = sorted({x.label for x in (d2 or [])})
        print(f"real image -> keep={k2} labels={labels}")
        assert k2 and d2, "expected a person/car/motorcycle to be retained"
        assert set(labels) <= {"person", "car", "motorcycle"}, f"unexpected class: {labels}"
        print("PASS: real object retained; only in-scope classes")
    print(det.summary())
    return 0


def test_vision_onnx():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
