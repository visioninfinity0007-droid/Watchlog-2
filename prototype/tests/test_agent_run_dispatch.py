#!/usr/bin/env python3
"""Guard the PACKAGED agent's run dispatch against signature drift.

The release entrypoint (`release_agent.py`) rebinds `analytics_agent.enhanced_cmd_run`
to `incident_evidence.wrap_cmd_run(...)`, and `analytics_agent.main()` then sets
`core.cmd_run = enhanced_cmd_run`. `watchlog_agent.main()` calls that dispatch as:

    cmd_run(cfg, state, cloud, once=..., device=..., channels=...)

If the override's signature drifts from `watchlog_agent.cmd_run` (e.g. it does not
accept `channels`), the agent raises `TypeError` on EVERY normal start -> the
scheduled task + run-agent.ps1 restart it every ~15s -> a permanent crash loop with
no events, no heartbeat, no health, and NO diagnostic beyond one line. Critically,
`--version` and `--selftest` return BEFORE this call, so CI's exe checks stay green
while the shipped agent cannot run. This test binds the real dispatch the way
`main()` does, so that class of bug fails fast in the offline job.
"""
import inspect
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import watchlog_agent as core          # noqa: E402
import analytics_agent                 # noqa: E402
import incident_evidence               # noqa: E402
import release_agent                   # noqa: E402  (module import only; main() is gated)
import native_event_collector          # noqa: E402


def _binds_main_call(fn):
    """True iff fn accepts exactly the keyword call watchlog_agent.main() makes."""
    try:
        inspect.signature(fn).bind(object(), {}, object(), once=False, device=None, channels=[])
        return True, ""
    except TypeError as exc:
        return False, str(exc)


def main() -> int:
    problems = []

    # 1. The ORIGINAL core loop must accept the call main() makes (channels is real).
    ok, err = _binds_main_call(core.cmd_run)
    if not ok:
        problems.append(f"watchlog_agent.cmd_run rejects main()'s call: {err}")

    # 2. The PACKAGED normal-path dispatch: wrap_cmd_run(enhanced_cmd_run) is what
    #    core.cmd_run becomes. It MUST bind main()'s call or the agent crash-loops.
    wrapped = incident_evidence.wrap_cmd_run(analytics_agent.enhanced_cmd_run)
    ok, err = _binds_main_call(wrapped)
    if not ok:
        problems.append("packaged run dispatch wrap_cmd_run(enhanced_cmd_run) rejects "
                        f"main()'s call -> agent crash-loop on start: {err}")

    # 3. enhanced_cmd_run itself must accept channels (the wrapper forwards it).
    ok, err = _binds_main_call(analytics_agent.enhanced_cmd_run)
    if not ok:
        problems.append(f"analytics_agent.enhanced_cmd_run rejects channels: {err}")

    # 4. The --setup validation shim also sits behind the same call.
    ok, err = _binds_main_call(release_agent._setup_validation_complete)
    if not ok:
        problems.append(f"release_agent._setup_validation_complete rejects channels: {err}")

    # 5. enhanced_cmd_run passes a `holder` to the packaged collector for native-fault
    #    health; the packaged collector must accept it: collector(cfg, spool, stop, holder).
    try:
        inspect.signature(native_event_collector.collector).bind(object(), object(), object(), None)
    except TypeError as exc:
        problems.append(f"native_event_collector.collector rejects the health holder arg: {exc}")

    if problems:
        raise SystemExit("agent run-dispatch contract FAILED:\n- " + "\n- ".join(problems))
    print("agent run-dispatch contract: PASS (packaged dispatch binds main()'s call; "
          "no `channels` TypeError; collector accepts the health holder)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
