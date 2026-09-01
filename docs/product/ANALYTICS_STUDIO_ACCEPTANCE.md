# Analytics Studio v1 acceptance checklist

This file tracks the implementation gate for the `product/analytics-studio-v1` branch.

## Database

- [ ] Migration creates site profiles, camera purposes, monitoring rules, schedules, analytic events and aggregates.
- [ ] All tenant-scoped tables have RLS.
- [ ] Portal RPCs self-authorize tenant membership.
- [ ] Agent RPCs derive tenant/site from agent credentials.
- [ ] Config version increments on analytic configuration changes.

## Agent

- [ ] Agent pulls analytics config over outbound heartbeat/polling.
- [ ] Config persists locally for offline operation.
- [ ] Local tracking prevents frame-by-frame double counting.
- [ ] Line crossing supports direction.
- [ ] Zone entry supports class filtering.
- [ ] Dwell supports minimum duration.
- [ ] Schedule rules use site-local timezone.
- [ ] Analytic events spool offline and upload idempotently.

## Portal

- [ ] Site type selector.
- [ ] Camera purpose selector.
- [ ] Monitoring pack recommendations.
- [ ] Analytics navigation.
- [ ] Camera analytics editor.
- [ ] Line/polygon editor on configuration still.
- [ ] Schedule editor.
- [ ] Analytics overview with date/site/camera filters.
- [ ] Visitor Flow, Vehicle Flow, Zone Activity and After-Hours summaries.

## Installer

- [ ] Site type step.
- [ ] Camera-purpose step after discovery.
- [ ] Configuration saved/synced.
- [ ] Portal handoff for advanced analytics.

## Reporting

- [ ] Daily report includes enabled analytics.
- [ ] Measurement events stay separate from incidents.
- [ ] Reporting entitlement remains enforced.

## Regression

- [ ] Existing incidents continue to ingest.
- [ ] Existing tenant isolation remains green.
- [ ] Existing reporting remains idempotent.
- [ ] Billing/trial behavior unchanged.
