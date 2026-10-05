#!/usr/bin/env python3
"""U-3: the shipped run loop honours spool_max_rows.

watchlog.ini's spool_max_rows sizes the local event buffer per deployment (an Edge box can
buffer a longer outage). core.cmd_run passed it to Spool, but the packaged loop,
analytics_agent.enhanced_cmd_run, built Spool(cfg.spool_path) and silently used the
200 000-row default, so a site configured for a longer buffer evicted events early.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import watchlog_agent as core  # noqa: E402


class _Built(Exception):
    pass


def test_enhanced_cmd_run_passes_spool_max_rows(monkeypatch, tmp_path):
    seen = {}

    def spool(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        raise _Built                      # stop before any worker starts

    monkeypatch.setattr(core.vision, "build", lambda _cfg, _log: None)
    monkeypatch.setattr(analytics_agent, "Spool", spool)
    cfg = SimpleNamespace(spool_path=tmp_path / "spool.sqlite", spool_max_rows=750_000)
    with pytest.raises(_Built):
        analytics_agent.enhanced_cmd_run(cfg, {}, object(), once=False, channels=[])
    bound = list(seen["args"]) + list(seen["kwargs"].values())
    assert bound == [cfg.spool_path, 750_000]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
