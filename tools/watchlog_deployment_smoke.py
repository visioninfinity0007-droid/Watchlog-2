#!/usr/bin/env python3
"""Read-only HTTP smoke checks for a deployed WatchLog stack.

The tool issues GET requests only. It never sends credentials, enrollment
codes, recorder tokens, report-runner tokens, billing mutations, or POST/PUT/
DELETE requests.

URLs may be supplied with command-line flags or environment variables:
  WATCHLOG_PORTAL_URL
  WATCHLOG_MARKETING_URL
  WATCHLOG_PUSH_URL
  WATCHLOG_REPORT_URL
  WATCHLOG_BILLING_URL

Example:
  python tools/watchlog_deployment_smoke.py \
    --portal https://portal.example.com \
    --marketing https://www.example.com \
    --push https://push.example.com \
    --reporter https://report.example.com \
    --billing https://billing.example.com \
    --output watchlog-deployment-smoke.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

USER_AGENT = "WatchLog-Deployment-Smoke/1.0"


@dataclass
class CheckResult:
    name: str
    url: str
    ok: bool
    status: int | None
    detail: str


def _base_url(value: str | None, env_name: str) -> str:
    raw = (value or os.environ.get(env_name, "")).strip()
    if not raw:
        raise RuntimeError(f"missing URL: pass flag or set {env_name}")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"invalid HTTP(S) URL for {env_name}: {raw!r}")
    return raw.rstrip("/") + "/"


def _get(url: str, timeout: float) -> tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read(512_000).decode("utf-8", errors="replace")
            ctype = response.headers.get("Content-Type", "")
            return int(response.status), ctype, body
    except urllib.error.HTTPError as exc:
        body = exc.read(64_000).decode("utf-8", errors="replace")
        return int(exc.code), exc.headers.get("Content-Type", ""), body


def _text_check(name: str, url: str, timeout: float, required: tuple[str, ...]) -> CheckResult:
    try:
        status, _ctype, body = _get(url, timeout)
        missing = [needle for needle in required if needle.lower() not in body.lower()]
        ok = 200 <= status < 300 and not missing
        detail = "ok" if ok else (
            f"HTTP {status}; missing text: {', '.join(missing)}" if missing else f"HTTP {status}"
        )
        return CheckResult(name, url, ok, status, detail)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name, url, False, None, f"{type(exc).__name__}: {str(exc)[:240]}")


def _json_check(
    name: str,
    url: str,
    timeout: float,
    expected_service: str,
) -> CheckResult:
    try:
        status, _ctype, body = _get(url, timeout)
        try:
            payload: Any = json.loads(body)
        except json.JSONDecodeError:
            return CheckResult(name, url, False, status, "response is not JSON")
        service = payload.get("service") if isinstance(payload, dict) else None
        service_ok = service == expected_service
        flag_ok = bool(payload.get("ok")) if isinstance(payload, dict) else False
        ok = 200 <= status < 300 and service_ok and flag_ok
        detail = "ok" if ok else (
            f"HTTP {status}; service={service!r}; ok={flag_ok}"
        )
        return CheckResult(name, url, ok, status, detail)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name, url, False, None, f"{type(exc).__name__}: {str(exc)[:240]}")


def run_checks(
    *,
    portal: str,
    marketing: str,
    push: str,
    reporter: str,
    billing: str,
    timeout: float,
) -> dict[str, Any]:
    checks = [
        _text_check(
            "portal_login",
            urljoin(portal, "login/"),
            timeout,
            ("WatchLog",),
        ),
        _text_check(
            "marketing_home",
            marketing,
            timeout,
            ("WatchLog",),
        ),
        _text_check(
            "marketing_sitemap",
            urljoin(marketing, "sitemap.xml"),
            timeout,
            ("<url",),
        ),
        _text_check(
            "marketing_robots",
            urljoin(marketing, "robots.txt"),
            timeout,
            ("sitemap",),
        ),
        _text_check(
            "push_bridge",
            push,
            timeout,
            ("WatchLog push bridge", "OK"),
        ),
        _json_check(
            "report_runner",
            reporter,
            timeout,
            "watchlog-report-runner",
        ),
        _json_check(
            "billing_health",
            urljoin(billing, "health"),
            timeout,
            "watchlog-billing",
        ),
    ]
    return {
        "tool": "watchlog_deployment_smoke",
        "method_policy": "GET only",
        "ok": all(check.ok for check in checks),
        "checks": [asdict(check) for check in checks],
        "limitations": [
            "This proves HTTP surface reachability/content contracts only.",
            "It does not prove the deployed Git commit/image revision.",
            "It does not authenticate into customer or platform-admin routes.",
            "It does not run report delivery, billing mutation, recorder push, or database writes.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only WatchLog deployment smoke")
    parser.add_argument("--portal")
    parser.add_argument("--marketing")
    parser.add_argument("--push")
    parser.add_argument("--reporter")
    parser.add_argument("--billing")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--output")
    args = parser.parse_args()

    try:
        report = run_checks(
            portal=_base_url(args.portal, "WATCHLOG_PORTAL_URL"),
            marketing=_base_url(args.marketing, "WATCHLOG_MARKETING_URL"),
            push=_base_url(args.push, "WATCHLOG_PUSH_URL"),
            reporter=_base_url(args.reporter, "WATCHLOG_REPORT_URL"),
            billing=_base_url(args.billing, "WATCHLOG_BILLING_URL"),
            timeout=max(1.0, min(args.timeout, 60.0)),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
