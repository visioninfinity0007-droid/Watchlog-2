#!/usr/bin/env python3
"""Production entrypoint for the packaged WatchLog Site Agent.

Normal agent commands delegate to :mod:`analytics_agent`, with production
policies layered in here:

* recorder-native smart events are retained without requiring a second local
  person/vehicle inference gate;
* explicit incident-footage requests are serviced outbound-only by the site
  agent when the recorder exposes a validated playback/export path;
* Dahua archive search/download is installed explicitly so the frozen build
  includes the read-only recorded-media implementation;
* recording/storage current truth is refreshed separately from the immutable
  transition ledger, with Dahua recording positively proven from archive media.

The NSIS installer's explicit ``--setup`` command remains strict and exits after
recorder + enrollment validation so the background scheduled task owns the
long-running process.
"""
from __future__ import annotations

import sys

import analytics_agent as app
import dahua_archive
import incident_evidence
import native_event_collector
import recording_current
from drivers.native_recorder import NativeDahuaDriver

_ORIGINAL_SETUP = app.analytics_setup.run
_ORIGINAL_RUN = app.enhanced_cmd_run


def _strict_setup(*args, **kwargs):
    values = _ORIGINAL_SETUP(*args, **kwargs)
    if not values:
        raise SystemExit(1)
    return values


def _setup_validation_complete(cfg, state, cloud, once, device=None, channels=None):
    app.core.log(
        "setup validation complete: recorder + WatchLog enrollment proven; "
        "returning control to installer"
    )


def main() -> None:
    # The packaged release uses recorder-native smart-event precedence while
    # the source/prototype collector stays available for isolated regression
    # tests and older development flows.
    app.core.collector = native_event_collector.collector

    # The driver registry returns NativeDahuaDriver, which historically carried
    # its own unvalidated loadfile implementation. Route BOTH the base and the
    # registered wrapper through the hardened search-before-download path so
    # there is one clip implementation and one channel-index rule.
    dahua_archive.install()
    NativeDahuaDriver.get_clip = dahua_archive.get_clip

    # Durable transitions remain immutable/change-only; current proof is a
    # separate snapshot path and uses archive media for positive Dahua truth.
    recording_current.install(app.core)

    explicit_setup = "--setup" in sys.argv
    if explicit_setup:
        app.analytics_setup.run = _strict_setup
        app.enhanced_cmd_run = _setup_validation_complete
    else:
        app.enhanced_cmd_run = incident_evidence.wrap_cmd_run(_ORIGINAL_RUN)
    app.main()


if __name__ == "__main__":
    main()
