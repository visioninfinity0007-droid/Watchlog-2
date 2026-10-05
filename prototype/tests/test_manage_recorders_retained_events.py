"""Manage Recorders tells the technician what a disabled recorder kept on this PC.

disable_managed_recorder uploads a recorder's queued events while WatchLog still
accepts them and returns how many it could not send (retained_events). They are
never deleted and upload if the recorder is re-enabled, but the window used to
show a fixed "Recorder disabled" line and dropped the number. The count is now in
the disable message and, for as long as the recorder stays disabled, in its State.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import setup_backend as sb  # noqa: E402
import windows_secret as ws  # noqa: E402
from spool import Spool  # noqa: E402


class _Env:
    def __enter__(self):
        self.saved = os.environ.get("PROGRAMDATA")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROGRAMDATA"] = self.tmp.name
        self.saved_crypto = (cs.write_json_secret, cs.read_json_secret)

        def wjs(path, obj):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

        def rjs(path):
            text = Path(path).read_text(encoding="utf-8")
            if not text.startswith("JSON:"):
                raise ws.SecretError("corrupt")
            return json.loads(text[5:])

        cs.write_json_secret, cs.read_json_secret = wjs, rjs
        self.root = Path(self.tmp.name) / "WatchLog"
        self.root.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *_args):
        cs.write_json_secret, cs.read_json_secret = self.saved_crypto
        if self.saved is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self.saved
        self.tmp.cleanup()


def _site(root: Path, kept: int) -> tuple[str, str]:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    cs.save_recorder_credential(a, "a", "pw-a")
    cs.save_recorder_credential(b, "b", "pw-b")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": [
        {"local_id": a, "cloud_recorder_id": str(uuid.uuid4()), "display_name": "Shop",
         "url": "http://192.0.2.10", "is_primary": True, "continuity_owner": True},
        {"local_id": b, "cloud_recorder_id": str(uuid.uuid4()), "display_name": "Yard",
         "url": "http://192.0.2.20", "is_configured": False},
    ]})
    if kept:
        queue = Spool(root / "recorders" / b / "spool.sqlite")
        try:
            for i in range(kept):
                queue.add({"channel": "1", "event_type": "motion", "payload": {"n": i}})
        finally:
            queue.close()
    return a, b


def test_a_disabled_recorders_kept_events_are_listed_and_shown():
    with _Env() as env:
        a, b = _site(env.root, kept=3)

        rows = {row["local_id"]: row for row in sb.list_managed_recorders(env.root / "watchlog.ini")}

        assert rows[b]["retained_events"] == 3
        assert "retained_events" not in rows[a]          # only disabled recorders hold them
        assert sb.managed_recorder_state(rows[b]) == "Disabled (3 events kept on this PC)"
        assert sb.managed_recorder_state(rows[a]) == "Available"


def test_a_disabled_recorder_with_nothing_kept_just_says_disabled():
    with _Env() as env:
        _a, b = _site(env.root, kept=0)
        rows = {row["local_id"]: row for row in sb.list_managed_recorders(env.root / "watchlog.ini")}
        assert rows[b]["retained_events"] == 0
        assert sb.managed_recorder_state(rows[b]) == "Disabled"


def test_state_text_keeps_its_existing_meaning():
    assert sb.managed_recorder_state({"is_configured": True,
                                      "credential_state": "needs_attention"}) == "Needs attention"
    assert sb.managed_recorder_state({"is_configured": False,
                                      "credential_state": "needs_attention",
                                      "retained_events": 4}) == "Needs attention"
    assert sb.managed_recorder_state({"is_configured": False, "credential_state": "available",
                                      "retained_events": None}) == "Disabled"
    assert sb.managed_recorder_state({"is_configured": False, "credential_state": "available",
                                      "retained_events": 1}) == "Disabled (1 event kept on this PC)"


def test_the_disable_message_reports_what_was_kept():
    assert sb.disabled_recorder_message({"retained_events": 0}) == \
        "Recorder disabled. Historical evidence was preserved."
    one = sb.disabled_recorder_message({"retained_events": 1})
    assert "1 recorded event from this recorder is kept on this PC" in one
    many = sb.disabled_recorder_message({"retained_events": 12})
    assert "12 recorded events from this recorder are kept on this PC" in many
    assert "upload if the recorder is re-enabled" in many
    assert sb.disabled_recorder_message(None) == \
        "Recorder disabled. Historical evidence was preserved."
