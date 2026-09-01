# Analytics Studio v1 test plan

## Unit tests

- line side/orientation math
- directional crossing
- polygon point-in-zone
- zone entry/exit transitions
- dwell timer
- schedule inclusion/exclusion
- per-track deduplication
- config version handling

## Integration tests

- authenticated member reads/writes only own site analytics
- cross-tenant rule access denied
- valid agent pulls only its site's config
- valid agent uploads only its own site's analytic events
- duplicate analytic event rejected/idempotent
- daily aggregate computation
- report analytics section

## UI tests

- site type presets
- camera purpose selection
- rule type selection
- class selection
- line draw
- polygon draw
- schedule selection
- analytics filters
- empty/loading/error states

## Regression

- incident ingest
- report entitlement
- billing authorization
- team/trial
- tenant isolation
