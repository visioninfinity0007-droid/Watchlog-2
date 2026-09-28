# Skill — Tenant Intelligence Setup

Trigger: configure WatchLog intelligence/reporting for a tenant/site, customize a new vertical, or reconcile an existing tenant context.

## Inputs
- tenant/site name or ID;
- canonical Git access;
- production database access;
- active-agent and camera inventory.

Do not ask the user to repeat facts available from Git or the database.

## Procedure

1. Inspect canonical Git and production DB before planning.
2. Determine the site type. Reuse an implemented policy such as office.yaml or restaurant.yaml. If none exists, create the reusable site-type policy first.
3. Understand stable business context: hours, working days, overnight/service-day behavior, business areas, sensitive areas, owner priorities, meaningful metrics and unsupported metrics.
4. Validate camera reality using canonical physical cameras only. If labels/mapping are uncertain, inspect current images and record mapping confidence rather than guessing.
5. Create ai-harness/tenants/<tenant-site>/context.yaml inheriting the site-type policy.
6. Create reporting/README.md, reporting/methods/visual-snapshot-analysis.md and reporting/daily-reports/README.md.
7. Define Today, Yesterday, Last 7 days and Last 30 days. Yesterday always means the latest completed configured working/service day.
8. Align site_business_context with the same semantic contract and report-layout profile.
9. Align customer AI. It should read like a natural management brief, separate observed/estimated/unsupported facts, and surface repeated evidence-backed recommendations. For restaurant daily reports, follow `ai-harness/skills/restaurant-daily-business-report.md` for client-facing hierarchy and UX.
10. Validate authorization, report dates, camera inventory, portal tabs, migration numbering and coverage truth.
11. Compare any mirror/handoff repo against canonical. Classify each difference as ported, already present/newer, or intentionally superseded.

## Visual setup pass

For analytics-heavy sites, inspect representative images for:
- camera role/area mapping;
- glare/overexposure;
- obstruction/occlusion;
- camera angle/blind spots;
- count/tracking confidence;
- business objects/areas relevant to the site type.

Never publish an accuracy percentage from model confidence alone. Accuracy requires human-ground-truth comparison.

## Deliverables

- reusable site-type policy;
- tenant context.yaml;
- reporting tree;
- reproducible visual-analysis method;
- report-window semantics;
- aligned runtime context;
- customer-facing report profile;
- report experience rules for hierarchy, charts, progressive disclosure and client/internal separation;
- recommendations in reports;
- canonical Git reconciliation record.


## Reusable invocation prompt

Use this prompt with the tenant/site substituted:

> Set up WatchLog intelligence for **<tenant> / <site>**. Work from canonical Git and the live database before planning. Identify the site type and inherit the correct reusable policy. Inspect the active agent, canonical physical cameras, current business context and representative images before assigning camera roles. Create or update the tenant folder under `ai-harness/tenants/<tenant-site>/` with `context.yaml`, `reporting/README.md`, `reporting/methods/visual-snapshot-analysis.md`, and the governed daily-report archive. Define Today, Yesterday, Last 7 days and Last 30 days using the tenant's configured business/service day. Align `site_business_context`, portal report profile and customer AI semantics. Never invent camera mapping, counts, unsupported metrics or accuracy. Recommendations must be evidence-backed and must appear in the management report. Reconcile any mirror/handoff repo back to canonical and record all exceptions.

## Completion gate

Do not call setup complete until:
- canonical Git and DB agree on site type/report profile;
- Yesterday resolves to the latest completed configured business/service day;
- reporting archive + visual method exist in the tenant folder;
- portal uses the intended four windows for the tenant type;
- the AI prompt/context contains the tenant semantic contract;
- recommendations are visible in reporting, not only chat;
- actionable recommendations use stable `action_items[].id` values so client feedback can be persisted;
- client-facing recommendations provide quick response choices, optional comments and a direct Discuss with WatchLog path;
- recommendation responses feed the internal WatchLog follow-up queue rather than disappearing into chat;
- completed reviewed reports do not expose processing/waiting UI or internal implementation details;
- the report hierarchy is scannable and avoids duplicated card dumps;
- authorization and coverage-truth checks pass;
- mirror/canonical reconciliation is documented.


## Recommendation feedback requirement

For any tenant report that gives actionable recommendations, prefer structured `action_items` over unkeyed text-only action lists.

Each actionable item should include:
- stable `id`;
- concise title;
- evidence-backed body;
- priority where useful.

Where the portal supports recommendation feedback, expose the same response loop used by the restaurant reference implementation:
- We'll do this;
- Need help;
- Not now;
- Not relevant;
- optional client comment;
- Discuss with WatchLog.

A saved response must remain attached to the exact report/recommendation and must be available to the WatchLog platform team for follow-up. Internal team notes remain private.
