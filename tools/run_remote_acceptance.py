#!/usr/bin/env python3
"""Run the WatchLog full acceptance test on a site remotely and print the verdict table.

    SUPABASE_URL=https://<project>.supabase.co \\
    SUPABASE_PUBLISHABLE_KEY=<publishable key> \\
    WATCHLOG_OWNER_JWT=<an owner's or admin's access token> \\
    python tools/run_remote_acceptance.py --site <site uuid> [--recorder <recorder uuid>]

It calls wl_site_run_acceptance (owner/admin of the site's account; migration 0163), which
queues the read-only ``run_full_acceptance_test`` Site Control command for the site's
current Agent, then polls wl_site_acceptance_result until the Agent has completed it (or
``--timeout`` passes) and prints one line per check, e.g.

    Agent                    PASS
    ...
    47/47 PASS
    Hardware: Dahua DH-XVR1B08-I firmware ... serial ...
    Agent: 5.1.2 (build ...)
    Tested: 2026-10-07T10:00:00Z

Without --recorder every configured recorder of the site is tested (one section each).

The access token is read from the environment only and is never printed or written; every
line this tool prints is scrubbed of it. SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY may also
come from the repository's .env (public values only; the token never does).

Exit: 0 = no check FAILed, 2 = at least one FAIL, 3 = the command failed, expired or did not
finish in time, 1 = usage or HTTP error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))

import full_acceptance  # noqa: E402  (format_table: one table for CLI and portal tooling)

TOKEN_ENV = "WATCHLOG_OWNER_JWT"
PENDING = ("queued", "claimed", "executing", "verifying")
DEFAULT_TIMEOUT_SECONDS = 420      # queue + claim poll + the suite's own <= 180 s budget
DEFAULT_POLL_SECONDS = 5.0


class ToolError(RuntimeError):
    pass


def _env_file_values() -> dict:
    values = {}
    path = ROOT / ".env"
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
        if m and m.group(1) in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY"):
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return values


def settings(environ=None) -> dict:
    environ = os.environ if environ is None else environ
    file_values = _env_file_values() if environ is os.environ else {}
    url = (environ.get("SUPABASE_URL") or file_values.get("SUPABASE_URL") or "").rstrip("/")
    key = environ.get("SUPABASE_PUBLISHABLE_KEY") or file_values.get("SUPABASE_PUBLISHABLE_KEY") \
        or ""
    token = (environ.get(TOKEN_ENV) or "").strip()
    missing = [name for name, value in (("SUPABASE_URL", url),
                                        ("SUPABASE_PUBLISHABLE_KEY", key),
                                        (TOKEN_ENV, token)) if not value]
    if missing:
        raise ToolError("missing environment: " + ", ".join(missing))
    if not url.startswith("https://"):
        raise ToolError("SUPABASE_URL must be https://")
    return {"url": url, "key": key, "token": token}


def scrub(text, token: str) -> str:
    text = str(text)
    if token:
        text = text.replace(token, "[token]")
    return re.sub(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+", "[token]", text)


def _http_post(url: str, headers: dict, body: dict, timeout: float = 30.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "null"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")[:500]


def rpc(conf: dict, fn: str, body: dict, *, http=_http_post):
    headers = {"apikey": conf["key"], "Authorization": "Bearer " + conf["token"],
               "Content-Type": "application/json"}
    status, data = http(f"{conf['url']}/rest/v1/rpc/{fn}", headers, body)
    if status >= 400:
        detail = data
        if isinstance(data, str):
            try:
                detail = json.loads(data).get("message", data)
            except (ValueError, AttributeError):
                pass
        raise ToolError(f"{fn}: HTTP {status}: {scrub(detail, conf['token'])[:300]}")
    return data


def run(site_id: str, recorder_id: str | None, *, conf: dict, timeout: float, poll: float,
        out=print, http=_http_post, sleep=time.sleep, monotonic=time.monotonic,
        json_path: str | None = None) -> int:
    def say(line=""):
        out(scrub(line, conf["token"]))

    command_id = rpc(conf, "wl_site_run_acceptance",
                     {"p_site_id": site_id, "p_recorder_id": recorder_id}, http=http)
    if not command_id:
        raise ToolError("wl_site_run_acceptance returned no command id")
    say(f"acceptance test queued: command {command_id}"
        + (f" (recorder {recorder_id})" if recorder_id else " (every configured recorder)"))
    deadline = monotonic() + timeout
    last = None
    while True:
        result = rpc(conf, "wl_site_acceptance_result", {"p_command_id": command_id}, http=http)
        status = (result or {}).get("status")
        if status != last:
            say(f"status: {status}")
            last = status
        if status not in PENDING:
            break
        if monotonic() >= deadline:
            say(f"no result after {int(timeout)}s; the command is still {status} "
                f"(command {command_id})")
            return 3
        sleep(poll)

    if status != "succeeded":
        say(f"the acceptance test did not run: {status}"
            + (f" ({result.get('error')})" if result.get("error") else ""))
        return 3
    document = {"summary": result.get("summary") or {}, "checks": result.get("checks") or [],
                "recorders": result.get("recorders") or []}
    if json_path:
        Path(json_path).write_text(json.dumps(result, indent=2, sort_keys=True),
                                   encoding="utf-8")
    say()
    for line in full_acceptance.format_table(document):
        say(line)
    return 2 if int(document["summary"].get("failed") or 0) else 0


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a UUID: {value}") from error


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--site", required=True, type=_uuid, help="site UUID")
    ap.add_argument("--recorder", type=_uuid, default=None,
                    help="test only this recorder (default: every configured recorder)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                    help="seconds to wait for the result")
    ap.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS)
    ap.add_argument("--json", metavar="PATH", help="also write the raw result JSON here")
    args = ap.parse_args(argv)
    try:
        conf = settings()
        return run(args.site, args.recorder, conf=conf, timeout=args.timeout, poll=args.poll,
                   json_path=args.json)
    except ToolError as error:
        token = os.environ.get(TOKEN_ENV, "")
        print(f"error: {scrub(error, token)}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as error:
        print(f"error: could not reach WatchLog ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
