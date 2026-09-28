# Tenant Reporting Contract

Each tenant/site uses:

ai-harness/tenants/<tenant-site>/
  context.yaml
  reporting/
    README.md
    methods/
      visual-snapshot-analysis.md
    daily-reports/
      README.md
      YYYY-MM-DD/
        summary.md
        detailed-report.md

## Date semantics

The folder date is the configured business/service-day key, not automatically midnight-to-midnight.

- Office: Yesterday = latest completed configured working day.
- Overnight restaurant: the service day is named by the date on which opening occurs.
- Non-working days are skipped when a window is defined as working days.
- Missing coverage is never converted into zero activity.

## Report files

summary.md is the management brief: what happened, what needs attention, key metrics, coverage confidence, and priority recommendations.

detailed-report.md is the evidence-backed report: window definition, camera coverage, visually extracted observations, time/floor/area breakdowns, quality limitations, repeated patterns, recommendations and source traceability.

Every report separates observed facts, derived/estimated metrics, unsupported claims, and missing coverage.

The report archive is a governed context artifact. Live factual values remain database-authoritative.
