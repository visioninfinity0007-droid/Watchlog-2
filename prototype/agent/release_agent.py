#!/usr/bin/env python3
"""Production entrypoint for the packaged WatchLog Site Agent.

Normal agent commands delegate unchanged to :mod:`analytics_agent`. The one
special case is the NSIS installer's explicit ``--setup`` command:

* cancelling or failing the interactive setup must return a non-zero process
  code so NSIS cannot mistake an incomplete setup for success;
* after the proven local configuration is saved, the command validates the
  enrollment code against WatchLog, syncs recorder/camera metadata, and then
  EXITS instead of falling through into the long-running agent loop.

The installer starts the long-running background task only after this process
returns success. This keeps one owner for process lifetime and makes installer
success deterministic.
"""
from __future__ import annotations

import sys

import analytics_agent as app

_ORIGINAL_SETUP = app.analytics_setup.run


def _strict_setup(*args, **kwargs):
    values = _ORIGINAL_SETUP(*args, **kwargs)
    if not values:
        # core.main historically returned 0 here. For an explicit installer
        # setup that is unsafe because NSIS uses the process exit code as its
        # success gate.
        raise SystemExit(1)
    return values


def _setup_validation_complete(cfg, state, cloud, once, device=None):
    """Replace the infinite runtime only for explicit ``--setup``.

    core.main has already re-opened the recorder, enrolled (or confirmed the
    existing identity), synced cameras and reported capabilities before it
    reaches cmd_run. Returning here therefore means the installation checks are
    complete; the NSIS-created background task owns the long-running process.
    """
    app.core.log(
        "setup validation complete: recorder + WatchLog enrollment proven; "
        "returning control to installer"
    )


def main() -> None:
    explicit_setup = "--setup" in sys.argv
    if explicit_setup:
        app.analytics_setup.run = _strict_setup
        app.enhanced_cmd_run = _setup_validation_complete
    app.main()


if __name__ == "__main__":
    main()
