#!/usr/bin/env python3
"""Shrink sharp camera stills before upload (Agent only; view_quality.register_resizer).

Kept apart from view_quality.py so the recorder drivers, which the light Setup UI also uses,
never reach Pillow. The Agent ships Pillow (requirements.txt).
"""
from __future__ import annotations

import io

from PIL import Image


def cap_jpeg(data: bytes, max_width: int, quality: int) -> bytes:
    """A JPEG at most max_width wide (shape kept). Fails open: any error returns the original."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.width <= max_width:
            return data
        img = img.convert("RGB")
        h = max(1, round(img.height * max_width / img.width))
        img = img.resize((max_width, h), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception:                                       # noqa: BLE001
        return data
