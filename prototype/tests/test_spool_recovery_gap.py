#!/usr/bin/env python3
"""Durable spool overflow -> recorder recovery interval contract."""
from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from spool import Spool  # noqa: E402


def test_overflow_gap_is_durable_and_acknowledged_explicitly():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "spool.sqlite3"
        sp = Spool(path, max_rows=2)
        try:
            for i in range(4):
                sp.add({"device_ts": f"2026-09-25T12:00:0{i}+00:00", "n": i})
                time.sleep(0.01)
            assert sp.trim() == 2
            gap = sp.pending_recovery_gap()
            assert gap is not None and gap[0] <= gap[1]
            assert sp.count() == 2
        finally:
            sp.close()

        # Marker survives process restart / power interruption.
        sp = Spool(path, max_rows=2)
        try:
            assert sp.pending_recovery_gap() == gap
            assert sp.clear_recovery_gap(*gap) is True
            assert sp.pending_recovery_gap() is None
        finally:
            sp.close()


def test_new_overflow_cannot_be_cleared_by_stale_ack():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "spool.sqlite3"
        sp = Spool(path, max_rows=1)
        try:
            sp.add({"n": 1}); sp.add({"n": 2})
            assert sp.trim() == 1
            old = sp.pending_recovery_gap()
            time.sleep(0.01)
            sp.add({"n": 3})
            assert sp.trim() == 1
            new = sp.pending_recovery_gap()
            assert new is not None
            assert sp.clear_recovery_gap(*old) is False
            assert sp.pending_recovery_gap() == new
        finally:
            sp.close()


if __name__ == "__main__":
    test_overflow_gap_is_durable_and_acknowledged_explicitly()
    test_new_overflow_cannot_be_cleared_by_stale_ack()
    print("OK: spool overflow is durably reconciled through recorder recovery")
