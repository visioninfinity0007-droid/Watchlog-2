#!/usr/bin/env python3
"""A broken recorder login stays visible and contained (credential-invariants audit P5/P8).

* P5-a: Manage Recorders listed NOTHING when the primary recorder's saved login could not be
  decrypted (the fail-closed migration check raised before the per-row check), so the operator
  could not select that recorder to fix it. It is now listed as needing attention.
* P5-b: a credential blob that decrypts but is not a JSON object raised ValueError, which
  callers do not treat as a credential fault; it is now a SecretError like any corrupt secret.
* P8-b: quarantining a registry left by another installation or site left that installation's
  per-recorder logins live in Secrets\recorders; they now move aside with it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import windows_secret as ws  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_recorder_manager as trm  # noqa: E402  (shared fake-DPAPI environment and seed)


@pytest.mark.parametrize("plain", [b"\xff\xfe", b"not json", b"[1, 2]"])
def test_a_blob_that_is_not_a_json_object_is_a_secret_error(monkeypatch, tmp_path, plain):
    monkeypatch.setattr(ws, "read_secret", lambda path: plain)
    with pytest.raises(ws.SecretError):
        ws.read_json_secret(tmp_path / "x.dpapi")


def test_manage_recorders_lists_a_recorder_whose_login_is_unreadable():
    with trm._Env() as env:
        a, b = trm._seed_two()
        trm.cs.recorder_credential_path(a).write_text("garbage", encoding="utf-8")
        rows = trm.sb.list_managed_recorders(env.ini)
        by_id = {r["local_id"]: r for r in rows}
        assert set(by_id) == {a, b}
        assert by_id[a]["credential_state"] == "needs_attention"
        assert by_id[b]["credential_state"] == "available"


def test_quarantine_moves_the_quarantined_recorders_logins_aside():
    with trm._Env():
        a, b = trm._seed_two()
        live = trm.cs.recorder_secrets_dir()
        assert (live / f"{b}.dpapi").exists()
        moved = trm.rr.quarantine_registry()
        assert not (live / f"{b}.dpapi").exists()
        assert any(p.name.startswith("recorders.quarantine-") and p.parent.name == "Secrets"
                   for p in moved)
        assert any((p / f"{b}.dpapi").exists() for p in moved if p.parent.name == "Secrets")


def test_a_failed_details_update_after_a_verified_login_change_says_what_is_true(monkeypatch):
    # P4-b: the verified login is saved; a registry failure afterwards must not read as
    # "nothing changed" while the new login is already in use.
    with trm._Env():
        a, _b = trm._seed_two()
        proven = {"url": "http://192.0.2.10", "vendor": "V", "model": "M", "driver": "onvif",
                  "channels": [{"channel": "1"}]}

        def broken(*_a, **_k):
            raise PermissionError("registry locked")
        monkeypatch.setattr(trm.rr, "update_observed_identity", broken)
        with pytest.raises(ValueError, match="verified and saved"):
            trm.sb.repair_managed_recorder_credential(a, "new-user", "new-pw",
                                                      verified_recorder=proven)
        assert trm.cs.load_recorder_credential(a)["username"] == "new-user"
