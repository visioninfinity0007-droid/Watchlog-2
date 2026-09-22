#!/usr/bin/env python3
"""Production entrypoint for the packaged WatchLog Site Connector.

Normal agent commands delegate to :mod:`watchlog_agent`, with production
connector policies layered in here:

* recorder-native events and snapshots are forwarded without local WatchLog AI;
* explicit incident-footage requests are serviced outbound-only by the Site
  Connector when the recorder exposes a validated playback/export path;
* Dahua archive search/download is installed explicitly so the frozen build
  includes the read-only recorded-media implementation;
* recording/storage current truth is refreshed separately from the immutable
  transition ledger, with Dahua recording positively proven from archive media;
* Site Control always performs its outbound poll in the final package, while
  the SERVER-side per-site gate remains the sole execution authority.

The NSIS installer's explicit ``--setup`` command remains strict and exits after
recorder + enrollment validation so the background Site Connector owns the
long-running process. WatchLog inference/analytics runs server-side.
"""
from __future__ import annotations

import sys

import watchlog_agent as app
import connector_capabilities
import connector_event_collector
import dahua_archive
import incident_evidence
import recording_current
from drivers.native_recorder import NativeDahuaDriver

_ORIGINAL_SETUP = app.setup_wizard.run
_ORIGINAL_RUN = app.cmd_run
_ORIGINAL_CONFIG_INIT = app.Config.__init__


def _strict_setup(*args, **kwargs):
    values = _ORIGINAL_SETUP(*args, **kwargs)
    if not values:
        raise SystemExit(1)
    return values


def _setup_validation_complete(cfg, state, cloud, once, device=None, channels=None):
    app.log(
        "setup validation complete: recorder + WatchLog enrollment proven; "
        "returning control to installer"
    )


def _production_config_init(self, *args, **kwargs):
    """Final-package policy: poll Site Control; server decides if it is enabled.

    The poll is outbound-only and agent-authenticated. Migration 0090 makes
    ``wl_agent_claim_command`` return no command while the site's explicit
    server gate is false, so no customer recorder capability is auto-enabled.
    """
    _ORIGINAL_CONFIG_INIT(self, *args, **kwargs)
    # Site Connector policy: local AI is intentionally disabled. The edge owns
    # connectivity/evidence; WatchLog cloud owns inference and intelligence.
    self.detect = False
    self.recovery_ai_enabled = False
    self.site_control_enabled = True
    self.connector_mode = True
    self.connector_require_clip_acceptance = True
    self.connector_accept_clip_seconds = 10


def main() -> None:
    # The packaged release uses recorder-native smart-event precedence while
    # the source/prototype collector stays available for isolated regression
    # tests and older development flows.
    app.collector = connector_event_collector.collector

    # The driver registry returns NativeDahuaDriver, which historically carried
    # its own unvalidated loadfile implementation. Route BOTH the base and the
    # registered wrapper through the hardened search-before-download path so
    # there is one clip implementation and one channel-index rule.
    dahua_archive.install()
    NativeDahuaDriver.get_clip = dahua_archive.get_clip

    # Durable transitions remain immutable/change-only; current proof is a
    # separate snapshot path and uses archive media for positive Dahua truth.
    recording_current.install(app)

    # Do not depend on a hand-edited watchlog.ini switch. This only enables the
    # harmless poll; the database gate decides whether commands can be claimed.
    app.Config.__init__ = _production_config_init

    explicit_setup = "--setup" in sys.argv
    if explicit_setup:
        app.setup_wizard.run = _strict_setup
        app.cmd_run = _setup_validation_complete
    else:
        runtime = incident_evidence.wrap_cmd_run(_ORIGINAL_RUN)
        app.cmd_run = connector_capabilities.wrap_cmd_run(runtime)
    app.main()


if __name__ == "__main__":
    main()
