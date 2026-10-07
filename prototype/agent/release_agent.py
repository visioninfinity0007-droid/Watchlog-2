#!/usr/bin/env python3
"""Production entrypoint for the packaged WatchLog Site Agent.

Normal agent commands delegate to :mod:`analytics_agent`, with production
policies layered in here:

* recorder-native smart events are retained without requiring a second local
  person/vehicle inference gate;
* explicit incident-footage requests are serviced outbound-only by the site
  agent when the recorder exposes a validated playback/export path;
* Dahua and Hikvision archive search/download are installed explicitly so the frozen build
  includes the read-only recorded-media implementations;
* recording/storage current truth is refreshed separately from the immutable
  transition ledger, with Dahua recording positively proven from archive media;
* Site Control always performs its outbound poll in the final package, while
  the SERVER-side per-site gate remains the sole execution authority.

The console setup wizard (``--setup``, and the automatic wizard when no recorder is
configured) is retired in the packaged Agent: it wrote the recorder password in plain text into
watchlog.ini, replaced the whole INI and ignored the recorder registry. WatchLog Setup
(watchlog-setup-ui.exe) is the only way to configure a site; the packaged Agent refuses the
wizard, writes nothing and exits non-zero.
"""
from __future__ import annotations

import sys

import analytics_agent as app
import dahua_archive
import hikvision_archive
import incident_evidence
import native_event_collector
import recording_current
import remote_update
from drivers.native_recorder import NativeDahuaDriver

_ORIGINAL_RUN = app.enhanced_cmd_run
_ORIGINAL_CONFIG_INIT = app.Config.__init__

CONSOLE_SETUP_EXIT = 2
CONSOLE_SETUP_RETIRED = (
    "This WatchLog program does not set up recorders. Open WatchLog Setup from the Start menu "
    "(WatchLog Setup, or Manage Recorders) to set up or change this site.")


def _console_setup_retired(*args, **kwargs):
    """Refuse the console wizard before it asks or writes anything."""
    app.core.log("console setup refused: the packaged Agent is configured by WatchLog Setup")
    print(CONSOLE_SETUP_RETIRED, file=sys.stderr)
    raise SystemExit(CONSOLE_SETUP_EXIT)


def _setup_validation_complete(cfg, state, cloud, once, device=None, channels=None):
    # Unreachable while the console setup is retired (the wizard exits first); kept so an
    # explicit --setup can never fall through into the long-running runtime.
    app.core.log("console setup refused: not starting the runtime")


def _production_config_init(self, *args, **kwargs):
    """Final-package policy: poll Site Control; server decides if it is enabled.

    The poll is outbound-only and agent-authenticated. Migration 0090 makes
    ``wl_agent_claim_command`` return no command while the site's explicit
    server gate is false, so no customer recorder capability is auto-enabled.
    """
    _ORIGINAL_CONFIG_INIT(self, *args, **kwargs)
    self.site_control_enabled = True


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
    hikvision_archive.install()
    NativeDahuaDriver.get_clip = dahua_archive.get_clip

    # Durable transitions remain immutable/change-only; current proof is a
    # separate snapshot path and uses archive media for positive Dahua truth.
    recording_current.install(app.core)

    # Do not depend on a hand-edited watchlog.ini switch. This only enables the
    # harmless poll; the database gate decides whether commands can be claimed.
    app.Config.__init__ = _production_config_init

    # Both the explicit --setup and the automatic wizard (no recorder configured) go through
    # analytics_setup.run: refuse it in the packaged Agent.
    app.analytics_setup.run = _console_setup_retired
    explicit_setup = "--setup" in sys.argv
    if explicit_setup:
        app.enhanced_cmd_run = _setup_validation_complete
    else:
        runtime = incident_evidence.wrap_cmd_run(_ORIGINAL_RUN)
        app.enhanced_cmd_run = remote_update.wrap_cmd_run(runtime)
    app.main()


if __name__ == "__main__":
    main()
