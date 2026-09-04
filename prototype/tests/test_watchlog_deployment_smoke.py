#!/usr/bin/env python3
"""Contract tests for the read-only WatchLog deployment smoke tool."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "watchlog_deployment_smoke.py"

spec = importlib.util.spec_from_file_location("watchlog_deployment_smoke", TOOL)
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


class Handler(BaseHTTPRequestHandler):
    methods: list[str] = []

    def do_GET(self):
        type(self).methods.append("GET")
        path = self.path.split("?", 1)[0]
        if path == "/portal/login/":
            self._send(200, "text/html", "<html><title>WatchLog</title>Login</html>")
        elif path == "/marketing/":
            self._send(200, "text/html", "<html>WatchLog marketing</html>")
        elif path == "/marketing/sitemap.xml":
            self._send(200, "application/xml", "<urlset><url></url></urlset>")
        elif path == "/marketing/robots.txt":
            self._send(200, "text/plain", "Sitemap: https://example/sitemap.xml")
        elif path == "/pushsvc/":
            self._send(200, "text/plain", "WatchLog push bridge: OK")
        elif path == "/reporter/":
            self._send_json(200, {"service": "watchlog-report-runner", "ok": True})
        elif path == "/billing/health":
            self._send_json(200, {"service": "watchlog-billing", "ok": True})
        elif path == "/badreporter/":
            self._send_json(200, {"service": "wrong-service", "ok": True})
        else:
            self._send(404, "text/plain", "not found")

    def do_POST(self):  # pragma: no cover - any call is a test failure
        type(self).methods.append("POST")
        self._send(500, "text/plain", "POST forbidden in smoke test")

    def _send(self, status: int, ctype: str, body: str):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, payload: dict):
        self._send(status, "application/json", json.dumps(payload))

    def log_message(self, *_args):
        pass


def with_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/"


def test_all_expected_surfaces_pass_with_get_only() -> None:
    Handler.methods.clear()
    server, base = with_server()
    try:
        report = smoke.run_checks(
            portal=base + "portal/",
            marketing=base + "marketing/",
            push=base + "pushsvc/",
            reporter=base + "reporter/",
            billing=base + "billing/",
            timeout=3,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert report["ok"] is True
    assert len(report["checks"]) == 7
    assert Handler.methods and set(Handler.methods) == {"GET"}


def test_wrong_service_identity_fails() -> None:
    Handler.methods.clear()
    server, base = with_server()
    try:
        result = smoke._json_check(
            "bad_reporter", base + "badreporter/", 3, "watchlog-report-runner"
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.ok is False
    assert "wrong-service" in result.detail
    assert set(Handler.methods) == {"GET"}


def test_url_validation_rejects_non_http() -> None:
    try:
        smoke._base_url("file:///etc/passwd", "IGNORED")
    except RuntimeError as exc:
        assert "invalid HTTP(S) URL" in str(exc)
    else:
        raise AssertionError("non-HTTP URL was accepted")


def test_source_has_no_mutating_http_methods() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert 'method="GET"' in source
    for method in ('method="POST"', 'method="PUT"', 'method="PATCH"', 'method="DELETE"'):
        assert method not in source


if __name__ == "__main__":
    tests = [
        test_all_expected_surfaces_pass_with_get_only,
        test_wrong_service_identity_fails,
        test_url_validation_rejects_non_http,
        test_source_has_no_mutating_http_methods,
    ]
    for test in tests:
        test()
    print(f"OK: {len(tests)} deployment smoke contract tests passed")
