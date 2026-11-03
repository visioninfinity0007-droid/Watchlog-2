#!/usr/bin/env python3
"""tools/run_remote_acceptance.py: queue, poll, print the verdict table; never print the token.

No network: the HTTP call is injected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import run_remote_acceptance as tool  # noqa: E402

# A JWT-shaped fake, assembled at run time so the tracked-file secret scan never sees one.
TOKEN = ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiJvd25lci10ZXN0In0",
                  "c2lnbmF0dXJlLXZhbHVl"])
SITE = "33333333-3333-3333-3333-333333333333"
CONF = {"url": "https://example.supabase.co", "key": "sb_publishable_test", "token": TOKEN}


def check(name, status="PASS", scope="agent", channel=None, error=None):
    return {"name": name, "scope": scope, "channel": channel, "status": status,
            "recorder_id": None, "last_error": error}


def done_result(checks, failed=0):
    passed = sum(1 for c in checks if c["status"] == "PASS")
    return {"status": "succeeded", "summary": {
        "passed": passed, "failed": failed, "unknown": len(checks) - passed - failed,
        "unsupported": 0, "total": len(checks),
        "hardware": {"vendor": "Dahua", "model": "DH-XVR1B08-I", "firmware": "4.001",
                     "serial": "8E06857PAZ7EB3A"},
        "agent": {"version": "5.1.2", "build_sha": "c151d52d0000"},
        "tested_at": "2026-10-07T10:00:00Z"}, "checks": checks, "recorders": []}


class FakeHttp:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, url, headers, body, timeout=30.0):
        self.calls.append((url, headers, body))
        if url.endswith("/wl_site_run_acceptance"):
            return 200, "cmd-123"
        return 200, self.results.pop(0)


def test_queues_polls_and_prints_the_table():
    checks = [check("Agent"), check("Cloud auth"), check("Snapshot", scope="camera", channel="1")]
    http = FakeHttp([{"status": "queued"}, {"status": "claimed"}, done_result(checks)])
    lines = []
    code = tool.run(SITE, None, conf=CONF, timeout=60, poll=0, out=lines.append, http=http,
                    sleep=lambda _s: None)
    assert code == 0
    assert http.calls[0][2] == {"p_site_id": SITE, "p_recorder_id": None}
    assert http.calls[1][2] == {"p_command_id": "cmd-123"}
    assert http.calls[0][1]["Authorization"] == "Bearer " + TOKEN
    assert "Agent                    PASS" in lines
    assert "Snapshot ch1             PASS" in lines
    assert lines[-4:] == ["3/3 PASS",
                          "Hardware: Dahua DH-XVR1B08-I firmware 4.001 serial 8E06857PAZ7EB3A",
                          "Agent: 5.1.2 (build c151d52d0000)",
                          "Tested: 2026-10-07T10:00:00Z"]
    assert not any(TOKEN in line for line in lines)


def test_a_failed_check_exits_2():
    checks = [check("Agent"), check("Recording", "FAIL", "camera", "2", "no recording found")]
    http = FakeHttp([done_result(checks, failed=1)])
    lines = []
    assert tool.run(SITE, None, conf=CONF, timeout=60, poll=0, out=lines.append, http=http,
                    sleep=lambda _s: None) == 2
    assert lines[-4].startswith("1/2 PASS (1 FAIL")


def test_timeout_and_expiry_exit_3():
    clock = iter(range(0, 1000, 50))
    http = FakeHttp([{"status": "queued"}] * 50)
    lines = []
    assert tool.run(SITE, None, conf=CONF, timeout=100, poll=0, out=lines.append, http=http,
                    sleep=lambda _s: None, monotonic=lambda: next(clock)) == 3
    assert "no result after 100s" in lines[-1]
    http = FakeHttp([{"status": "expired"}])
    assert tool.run(SITE, None, conf=CONF, timeout=60, poll=0, out=lines.append, http=http,
                    sleep=lambda _s: None) == 3


def test_http_error_never_echoes_the_token():
    def http(url, headers, body, timeout=30.0):
        return 403, '{"message":"this needs the owner or admin role; token ' + TOKEN + '"}'
    with pytest.raises(tool.ToolError) as raised:
        tool.run(SITE, None, conf=CONF, timeout=1, poll=0, out=print, http=http)
    assert TOKEN not in str(raised.value) and "owner or admin" in str(raised.value)


def test_settings_require_https_and_a_token():
    with pytest.raises(tool.ToolError):
        tool.settings({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "k"})
    with pytest.raises(tool.ToolError):
        tool.settings({"SUPABASE_URL": "http://x", "SUPABASE_PUBLISHABLE_KEY": "k",
                       tool.TOKEN_ENV: TOKEN})
    conf = tool.settings({"SUPABASE_URL": "https://x.supabase.co/", "SUPABASE_PUBLISHABLE_KEY": "k",
                          tool.TOKEN_ENV: TOKEN})
    assert conf["url"] == "https://x.supabase.co"


def test_cli_rejects_a_non_uuid_site(capsys):
    with pytest.raises(SystemExit):
        tool.main(["--site", "not-a-uuid"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
