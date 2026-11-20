#!/usr/bin/env python3
"""WatchLog's own update channel (migration 0236 + edge function watchlog-update-manifest).

Remote updates were dead: every Agent checks the retired cloud function, which answers 503
"manifest signing unavailable". Proves:
  * the edge function's canonical bytes are the Agent's (pinned string shared with sign_test.ts);
  * an Agent whose watchlog.ini still names an update feed on a retired host uses the build's feed
    (and key) instead, so one Repair/Upgrade moves every site to the new channel;
  * a site feed on any other host is left alone.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import agent_core  # noqa: E402
import updater  # noqa: E402

PINNED = ('{"channels":{"production":{"min_installed_components":"5.1.0","notes":"Caf\\u00e9 \\"sharp\\" views / update",'
          '"sha256":"abababababababababababababababababababababababababababababababab","size":91557259,'
          '"update_class":"AGENT_ONLY_COMPATIBLE","url":"https://example.test/a/watchlog-agent.exe","version":"5.1.13"}},'
          '"generated_at":"2026-11-06T16:00:00Z","schema":"watchlog.release_manifest.v1"}')

OLD = "https://oyvgubyxmjlijiczjona.supabase.co/functions/v1/watchlog-update-manifest"
NEW = "https://supa.watchlog.161.97.175.15.sslip.io/functions/v1/watchlog-update-manifest"
KEY = "0OM6vRLc4lCxY7uj2OpnEMNUikyK8uv5arw/04bFOM0="


def test_canonical_bytes_match_the_edge_function():
    fixture = {"schema": "watchlog.release_manifest.v1", "generated_at": "2026-11-06T16:00:00Z",
               "signature": "ignored",
               "channels": {"production": {"version": "5.1.13", "url": "https://example.test/a/watchlog-agent.exe",
                                           "sha256": "ab" * 32, "size": 91557259,
                                           "update_class": "AGENT_ONLY_COMPATIBLE",
                                           "min_installed_components": "5.1.0",
                                           "notes": 'Café "sharp" views / update'}}}
    assert updater.canonical_manifest_bytes(fixture).decode() == PINNED
    ts = (ROOT / "supabase/functions/watchlog-update-manifest/sign_test.ts").read_text(encoding="utf-8")
    assert "Caf\\\\u00e9" in ts and '"version":"5.1.13"' in ts, "sign_test.ts pins the same string"


def _config(tmp_path, monkeypatch, site_update_url, defaults_update_url=NEW):
    (tmp_path / "watchlog.defaults.ini").write_text(textwrap.dedent(f"""\
        [watchlog]
        supabase_url = https://supa.watchlog.161.97.175.15.sslip.io
        supabase_publishable_key = eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test
        update_url = {defaults_update_url}
        update_public_key = {KEY}
        retired_supabase_urls = https://oyvgubyxmjlijiczjona.supabase.co
        """), encoding="utf-8")
    (tmp_path / "watchlog.ini").write_text(textwrap.dedent(f"""\
        [watchlog]
        supabase_url = https://supa.watchlog.161.97.175.15.sslip.io
        supabase_publishable_key = eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test
        update_url = {site_update_url}
        update_public_key = {KEY}
        """), encoding="utf-8")
    monkeypatch.setattr(agent_core, "base_dir", lambda: tmp_path)
    for k in ("WATCHLOG_UPDATE_URL", "WATCHLOG_UPDATE_PUBLIC_KEY", "WATCHLOG_SUPABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    return agent_core.Config(tmp_path / "watchlog.ini")


def test_a_site_feed_on_a_retired_host_moves_to_the_builds_feed(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, OLD)
    assert cfg.update_url == NEW
    assert cfg.update_public_key == KEY


def test_a_retired_env_override_is_ignored(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, OLD)
    monkeypatch.setenv("WATCHLOG_UPDATE_URL", OLD)
    cfg = agent_core.Config(tmp_path / "watchlog.ini")
    assert cfg.update_url == NEW


def test_a_feed_on_another_host_is_left_alone(tmp_path, monkeypatch):
    other = "https://updates.example.test/manifest"
    cfg = _config(tmp_path, monkeypatch, other)
    assert cfg.update_url == other


def test_no_move_when_the_build_itself_names_a_retired_feed(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch, OLD, defaults_update_url=OLD)
    assert cfg.update_url == OLD
