"""Single source of truth for the WatchLog build version.

Everything derives its version from here: the runtime agent, the analytics
entrypoint, the setup UI/backend, the NSIS installer (read by the build script
and passed as /DAPPVERSION), and the portal heartbeat (via the agent). Do not
hardcode a version string anywhere else — the 0.3.3 release shipped runtime
identifying itself as 0.3.0 precisely because there were three separate
constants. A contract test enforces this.

BUILD_SHA / BUILD_CHANNEL are optionally stamped by the release pipeline.
"""
from __future__ import annotations

import os

VERSION = "0.3.4"
BUILD_SHA = os.environ.get("WATCHLOG_BUILD_SHA", "")
BUILD_CHANNEL = os.environ.get("WATCHLOG_BUILD_CHANNEL", "production")


def version_string() -> str:
    """Human/diagnostic version, e.g. '0.3.4' or '0.3.4+abc1234'."""
    return f"{VERSION}+{BUILD_SHA[:7]}" if BUILD_SHA else VERSION
