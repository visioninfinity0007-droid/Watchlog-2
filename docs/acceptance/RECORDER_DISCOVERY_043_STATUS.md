# WatchLog 0.4.3 Recorder Discovery Hotfix Status

## Problem
0.4.2 can miss a recorder on a multi-homed Windows site PC when the default internet route is on Wi-Fi but CCTV is connected through a separate Ethernet interface.

## Implemented fix
- Enumerate active IPv4 adapters, preferring the default-route interface first.
- Package `psutil` in the setup UI for reliable Windows adapter discovery, with stdlib fallback.
- Deduplicate and scan each represented private/link-local `/24`, bounded to eight automatic subnets.
- Never automatically sweep a public `/24`.
- Preserve explicit-subnet and manual recorder-IP paths.
- Keep direct recorder probing and vendor drivers unchanged.
- Pin PyInstaller 6.22.2 and explicitly include recorder discovery/vendor driver modules in the frozen setup executable.
- Add regression coverage to the existing recorder-hardening test suite.
- Bump release identity to 0.4.3.

## Evidence state
Source fix: IMPLEMENTED.
GitHub CI: NOT EXECUTED. Hosted Actions creates all six jobs but assigns no runner and executes zero steps; repeated runs show the same infrastructure-level failure.
Frozen 0.4.3 Windows installer: NOT BUILT.
Same-PC field verification: NOT RUN.
Public release: NOT AUTHORIZED BY EVIDENCE YET.

## Release gate
Do not publish 0.4.3 until GitHub hosted Actions runs normally and the Windows setup/build/release gates pass. After that, install the exact generated artifact on the same client PC that failed with 0.4.2 and prove Search Network detects the recorder, credentials validate, and channels enumerate.
