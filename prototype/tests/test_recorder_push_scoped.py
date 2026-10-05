#!/usr/bin/env python3
"""MNVR-011 (Agent side): recorder push is configured per recorder, never per site.

configure-push used to fetch ONE site-level token (wl_agent_issue_push_token(agent,
key)) and point the legacy watchlog.ini recorder at it. On a site with two recorders
that token names no recorder, so wl_ingest_push resolved channel N by site+channel and
attached the alarm to an arbitrary recorder's camera. Now:

  * with a recorder registry, every configured recorder runs from its own
    RecorderContext: its own token from wl_agent_issue_push_token(agent, key,
    p_recorder_id), its own driver and its own push URL;
  * on a multi-recorder registry it refuses unless WatchLog offers recorder-scoped
    push, and shows the database's own 42501 text; it never falls back to the
    site-level form there;
  * a single recorder on a database without the recorder form keeps the 5.0.x
    site-level form (which the database guards itself);
  * without a registry (the 5.0.x singleton) nothing changes.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import credential_store as cs  # noqa: E402
import recorder_registry as rr  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402

REC_A = "11111111-1111-1111-1111-111111111111"
REC_B = "22222222-2222-2222-2222-222222222222"
REFUSAL = "recorder push is not available for a site with more than one recorder"
STATE = {"agent_id": "agent-1", "agent_key": "key-1"}


def _base_cfg():
    return SimpleNamespace(push_bridge_url="https://push.example.io",
                           state_path=Path("unused.json"), supabase_url="https://x",
                           publishable_key="k", nvr_url="http://legacy", nvr_driver="auto")


def _ctx(local_id, cloud_id, name, url):
    cfg = SimpleNamespace(nvr_url=url, nvr_driver="hikvision-isapi",
                          recorder_local_id=local_id, recorder_cloud_id=cloud_id)
    return SimpleNamespace(local_id=local_id, cloud_recorder_id=cloud_id,
                           display_name=name, config=cfg)


class Cloud:
    """wl_agent_issue_push_token in either form; ``recorder_form`` False = a database
    from before recorder-scoped push (PostgREST has no 3-argument function)."""

    def __init__(self, recorder_form=True, refuse=None, legacy_refuses=False,
                 wrong_recorder=False):
        self.calls = []
        self.recorder_form = recorder_form
        self.refuse = refuse or {}
        self.legacy_refuses = legacy_refuses
        self.wrong_recorder = wrong_recorder

    def call(self, fn, **kw):
        self.calls.append((fn, dict(kw)))
        assert fn == "wl_agent_issue_push_token", fn
        rid = kw.get("p_recorder_id")
        if rid is None:
            if self.legacy_refuses:
                raise core.CloudError(fn, 403, "42501", REFUSAL)
            return {"ok": True, "token": "siteTok"}
        if not self.recorder_form:
            raise core.CloudError(fn, 404, "PGRST202",
                                  "Could not find the function public.wl_agent_issue_push_token")
        if rid in self.refuse:
            raise core.CloudError(fn, 403, "42501", self.refuse[rid])
        named = REC_A if self.wrong_recorder else rid
        return {"ok": True, "token": f"tok{rid[:1]}", "recorder_id": named}


class Opener:
    def __init__(self, verified=True):
        self.opened = []
        self.urls = {}
        self.verified = verified

    def __call__(self, cfg):
        self.opened.append(cfg)
        outer = self

        class Driver:
            def configure_push(self, url):
                outer.urls[cfg.nvr_url] = url
                return {"applied": True, "verified": outer.verified, "detail": "ok"}

            def close(self):
                pass
        return Driver()


def _run(contexts, cloud, opener=None):
    opener = opener or Opener()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = core.cmd_configure_push(_base_cfg(), _state=dict(STATE),
                                     _cloud_factory=lambda: cloud,
                                     _open_driver=opener,
                                     _contexts=lambda _cfg: list(contexts))
    payload = {}
    for line in buf.getvalue().splitlines():
        if line.startswith("PUSH_JSON "):
            payload = json.loads(line[len("PUSH_JSON "):])
    assert "tok" not in json.dumps(payload).replace("token", ""), "a push token was printed"
    return rc, payload, opener


TWO = [_ctx("loc-a", REC_A, "Front", "http://a"), _ctx("loc-b", REC_B, "Back", "http://b")]


def test_multi_recorder_registry_refuses_without_recorder_scoped_push():
    cloud = Cloud(recorder_form=False)
    rc, out, opener = _run(TWO, cloud)
    assert rc != 0
    assert out["configured"] is False and out["verified"] is False
    assert out["detail"] == REFUSAL
    assert opener.opened == [], "no recorder may be pointed at a site-wide token"
    assert all("p_recorder_id" in kw for _fn, kw in cloud.calls), \
        "the site-level form must never be used on a multi-recorder registry"


def test_each_recorder_gets_its_own_token_driver_and_url():
    cloud = Cloud()
    rc, out, opener = _run(TWO, cloud)
    assert rc == 0, out
    assert out["configured"] is True and out["verified"] is True
    asked = [kw["p_recorder_id"] for _fn, kw in cloud.calls]
    assert asked == [REC_A, REC_B]
    assert [c.nvr_url for c in opener.opened] == ["http://a", "http://b"]
    assert opener.urls == {"http://a": "https://push.example.io/push/tok1",
                           "http://b": "https://push.example.io/push/tok2"}
    assert [r["recorder"] for r in out["recorders"]] == ["Front", "Back"]


def test_database_refusal_for_one_recorder_shows_its_message():
    cloud = Cloud(refuse={REC_B: "recorder not configured for this agent site"})
    rc, out, opener = _run(TWO, cloud)
    assert rc != 0
    assert out["verified"] is False
    back = [r for r in out["recorders"] if r["recorder"] == "Back"][0]
    assert back["configured"] is False
    assert back["detail"] == "recorder not configured for this agent site"
    assert [c.nvr_url for c in opener.opened] == ["http://a"], \
        "the refused recorder must not be touched"


def test_token_for_another_recorder_is_refused():
    cloud = Cloud(wrong_recorder=True)
    rc, out, opener = _run(TWO, cloud)
    assert rc != 0
    assert [c.nvr_url for c in opener.opened] == ["http://a"]


def test_unbound_recorder_on_multi_registry_is_not_configured():
    contexts = [TWO[0], _ctx("loc-c", None, "Side", "http://c")]
    cloud = Cloud()
    rc, out, opener = _run(contexts, cloud)
    assert rc != 0
    side = [r for r in out["recorders"] if r["recorder"] == "Side"][0]
    assert side["configured"] is False and "linked" in side["detail"]
    assert "http://c" not in [c.nvr_url for c in opener.opened]
    assert all(kw.get("p_recorder_id") for _fn, kw in cloud.calls)


def test_single_bound_recorder_uses_the_recorder_form_and_its_context():
    cloud = Cloud()
    rc, out, opener = _run(TWO[:1], cloud)
    assert rc == 0, out
    assert cloud.calls == [("wl_agent_issue_push_token",
                            {"p_agent_id": "agent-1", "p_agent_key": "key-1",
                             "p_recorder_id": REC_A})]
    assert [c.nvr_url for c in opener.opened] == ["http://a"]


def test_single_recorder_on_a_database_without_the_recorder_form_keeps_5_0_form():
    cloud = Cloud(recorder_form=False)
    rc, out, opener = _run(TWO[:1], cloud)
    assert rc == 0, out
    assert [kw.get("p_recorder_id") for _fn, kw in cloud.calls] == [REC_A, None]
    assert opener.urls == {"http://a": "https://push.example.io/push/siteTok"}


def test_singleton_without_registry_shows_the_database_refusal():
    cloud = Cloud(legacy_refuses=True)
    rc, out, opener = _run([], cloud)
    assert rc != 0
    assert out["detail"] == REFUSAL
    assert opener.opened == []


def _real_registry(monkeypatch, tmp_path, rows):
    """A real recorders.json + per-recorder credentials under a temp ProgramData."""
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))

    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(text[5:])

    monkeypatch.setattr(cs, "write_json_secret", wjs)
    monkeypatch.setattr(cs, "read_json_secret", rjs)
    for row in rows:
        cs.save_recorder_credential(row["local_id"], "user", "pw")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": rows})


def test_the_shipped_command_reads_the_registry_and_refuses_a_site_token(monkeypatch, tmp_path):
    """No injection: the real command loads recorders.json and, on a database without
    recorder-scoped push, never points either recorder (or the legacy one) at a token."""
    _real_registry(monkeypatch, tmp_path, [
        {"local_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "cloud_recorder_id": REC_A,
         "display_name": "Front", "url": "http://a", "driver": "hikvision-isapi",
         "is_primary": True, "is_configured": True},
        {"local_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cloud_recorder_id": REC_B,
         "display_name": "Back", "url": "http://b", "driver": "hikvision-isapi",
         "is_primary": False, "is_configured": True},
    ])
    cfg = _base_cfg()
    cfg.state_path = tmp_path / "WatchLog" / "agent_state.json"
    cfg.spool_path = tmp_path / "WatchLog" / "spool.sqlite"
    cfg.health_store_path = tmp_path / "WatchLog" / "health.sqlite"
    cfg.last_live_path = tmp_path / "WatchLog" / "last_live.json"
    cloud, opener = Cloud(recorder_form=False), Opener()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = core.cmd_configure_push(cfg, _state=dict(STATE), _cloud_factory=lambda: cloud,
                                     _open_driver=opener)
    out = json.loads(buf.getvalue().split("PUSH_JSON ", 1)[1].splitlines()[0])
    assert rc != 0
    assert out["detail"] == REFUSAL
    assert opener.opened == []
    assert all("p_recorder_id" in kw for _fn, kw in cloud.calls)
