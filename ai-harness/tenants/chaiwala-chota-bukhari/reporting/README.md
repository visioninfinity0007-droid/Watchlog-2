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

summary.md is the management narrative: the main business story, up to four decision-useful metrics, the strongest operating/security implications and the top actions. It must be easy to scan and must not read like a technical data dump.

detailed-report.md is the evidence-backed report: window definition, camera coverage, visually extracted observations, time/floor/area breakdowns, quality limitations, repeated patterns, recommendations and source traceability.

Every report separates observed facts, derived/estimated metrics, unsupported claims, and missing coverage.

The report archive is a governed context artifact. Live factual values remain database-authoritative.


## Client report experience

Restaurant daily reports follow `ai-harness/skills/restaurant-daily-business-report.md`.

The rendered report should:
- use the site name as the title;
- lead with 2–3 pointers and business KPIs;
- include a useful interactive visual when a supported trend exists;
- group camera evidence into business functions;
- keep security after the business story unless urgent;
- show only the top actions by default;
- move observation-window and camera-quality caveats into secondary/expandable detail;
- never expose processing internals or duplicate the same insight across multiple card sections.
