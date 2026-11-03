# Skill — Restaurant Daily Business Report

Trigger: create, review, publish, redesign or regenerate a restaurant/cafe daily service-day report from WatchLog evidence.

Chai Wala - Chota Bukhari is the reference implementation.

## Goal

Produce a report a business owner can understand in under a minute, then explore for more detail.

The report is a business product, not a database, AI or camera-system status page.

## Required inputs

- tenant/site context and configured business/service day;
- canonical camera roles and known camera overlap;
- completed visual review or structured restaurant observations;
- saved report date and relevant business truth boundaries.

Do not ask the user to repeat facts already available from the tenant context, database or governed reporting method.

## Client-facing hierarchy

Use this order unless the evidence clearly requires a different one:

1. **Site name + report date**
   - Use the actual site name as the page title.
   - Do not add labels such as "Owner's Daily Business Brief", "management reading" or another duplicate report title.

2. **What mattered**
   - 2–3 concise pointers.
   - State the business story first: demand, service, closing discipline, security exception or opportunity.

3. **Headline KPIs**
   - Maximum four.
   - Only metrics a manager can act on.
   - Clearly label estimates.
   - Never fill the top row with coverage/process/technical metrics.

4. **One meaningful visual**
   - Prefer a demand timeline, hourly trend, table-utilization chart or comparison chart.
   - Make it interactive where the portal supports it: selectable points, hover/click detail, tooltips or a clear drill-down.
   - If the evidence supports only relative demand, chart relative demand. Do not invent precise counts.

5. **Operations**
   - Group related areas instead of creating one card per camera.
   - For restaurants normally use: dining/customer flow, handoff/frontage, kitchen/back of house, cash counter.
   - Each area gets one conclusion and, if useful, one takeaway.

6. **Security & control**
   - Only matters that affect management action or assurance.
   - Routine movement stays routine.
   - Do not place security before the business story unless there is a serious incident.

7. **Next actions**
   - Show the top three actions prominently.
   - Put lower-priority improvements behind progressive disclosure.
   - Every action must trace to evidence in the report.

8. **How to read the figures / visibility notes**
   - Keep caveats, observation-window limits and camera-quality improvements at the bottom or behind expandable controls.
   - Never make coverage the headline unless the lack of coverage itself is the main management issue.

## UX rules

- Do not render a long undifferentiated grid of cards.
- Do not repeat the same fact in summary, insights, key areas and actions.
- Use bullets/pointers for scanability.
- Use whitespace and section hierarchy so the owner can identify the main story in seconds.
- Prefer 1–2 sentence narrative blocks over dense paragraphs.
- Use progressive disclosure for supporting detail.
- A completed reviewed report must replace any "processing", "analytics configured" or waiting state for that service day.
- Do not expose snapshot counts, queue state, worker/model/provider names, database/RPC details, internal IDs or processing failures to the client.
- Do not call a relative chart a customer-count chart if it is only demand intensity.
- Do not create decorative charts. Every chart must answer a management question.

## Restaurant truth rules

- Visible diners are not unique footfall.
- Estimated covers are not POS covers.
- Floor-camera totals require overlap de-duplication where views overlap.
- A service/handoff camera is not a customer-entrance counter.
- Cash-counter footage does not provide sales or revenue.
- Kitchen footage does not prove food quality or staff productivity.
- Missing earlier/later periods are unknown, not zero.
- If a precise metric cannot be defended, use a range or qualitative/relative presentation.

## Restaurant business-intelligence contract

For every restaurant/cafe tenant, convert defensible dining-floor observations into a management layer, not only peak occupancy.

When calibrated table/session evidence supports it, include:
- **estimated dining covers** for the represented period, derived from de-duplicated table sessions;
- **estimated table sessions** and the number of qualifying sessions;
- **average party size** and **party-size mix** using neutral buckets: 1 person, 2 people, 3–4 people, 5+ people;
- **largest visible party** and joined-table parties where defensible;
- **new table sessions by time period** so management can see when fresh demand arrived;
- **table turnover/utilization** where table identity and session continuity are reliable;
- **served-session rate** where food becomes clearly visible during a defensible occupied session;
- **minimum observed dwell** with sample size;
- **time to first visible table-service interaction** and **time to first clearly visible served items**, each with sample size;
- **service slowdown versus demand** only when enough qualifying sessions exist for a defensible comparison.

Evidence gates:
- Never estimate a full-service-day cover total across material unverified trading time. Use **estimated covers in the represented period** instead.
- Never add overlapping dining-camera counts or sessions. Reconcile them to the same physical party/table first.
- For movable/joined tables, preserve the physical table identities and one shared combined-party/session interpretation.
- Every session-derived average, median, percentage or distribution must show the qualifying sample size.
- If the evidence does not support a metric, show **Unknown/Unavailable** with the reason instead of silently omitting it or inventing a value.
- A visible table-service action does not establish a person's identity, employment status, attendance or productivity.
- Do not infer gender, age, ethnicity or other customer demographics from appearance. Segment demand by party size, time, table/zone and service behaviour instead.

Preferred owner-facing business questions:
- Roughly how many dining covers were represented today?
- How many table sessions were observed, and what was the usual party size?
- When did new parties arrive most heavily?
- Which periods and tables had the strongest turnover or utilization?
- How quickly did tables receive a first visible service interaction?
- How quickly did clearly visible served items appear?
- Did service responsiveness weaken as demand increased?
- Did closing activity begin while meaningful demand remained?

## Chai Wala reference rules

- Service day: 16:00 to 04:00 next day, Asia/Karachi.
- Floor 1 and Floor 2 overlap and must be de-duplicated for site-wide customer/table readings.
- Shop Front is waiter/takeaway/car-side handoff, not a customer entrance.
- Tables are movable; joined tables remain underlying table identities with a shared combined-party interpretation.
- Closing behaviour is commercially relevant when visible trading capacity is reduced materially before the configured close.

## Publication state

When a complete manual/human-assisted visual review has been performed:
- persist the saved business report;
- mark the reviewed evidence batch complete/processed;
- store technical traceability in an internal-only audit;
- make the completed business report authoritative in the client portal;
- do not leave the client-facing page in an automated processing/waiting state.

## Completion gate

Do not call a daily restaurant report finished until:
- the title is the site name, with the service date clearly visible;
- the main business story is understandable without scrolling through every section;
- there is no duplicated insight across major sections;
- the top metrics are business metrics, not implementation metrics;
- at least one useful visual is present when the evidence supports a trend;
- the top three actions are obvious;
- caveats and camera-quality detail are secondary;
- internal WatchLog workings are absent from the client-facing output;
- manual review status is persisted when manual review was used;
- supported restaurant session/cover/party/service metrics are included, or each unsupported metric is explicitly marked Unknown/Unavailable with its evidence reason;
- every session-derived average/median/rate shows its qualifying sample size;
- no appearance-derived gender, age or demographic breakdown is present.


## B2B SaaS report model

Use a two-altitude model:

### Executive altitude
The default Overview should answer, without scrolling through raw detail:
- What happened?
- Is the business healthy or is something abnormal?
- What changed during the service day?
- Is there a security exception?
- What should management do next?

The Overview should use:
- one concise executive narrative;
- a maximum-four KPI strip;
- one primary trend visual;
- one compact security posture panel;
- the top three actions.

### Operational altitude
Business and Security views are drill-downs, not duplicated versions of Overview.

**Business** should explain:
- customer/demand pattern;
- estimated covers and table sessions for the represented period where defensible;
- party-size mix, new-session timing and largest/combined parties where defensible;
- table utilization and turnover where defensible;
- service responsiveness with qualifying sample sizes;
- service/handoff flow;
- kitchen/counter behavior;
- opening/closing discipline;
- customer-facing presentation.

**Security** should be exception-based:
- critical/attention/clear posture;
- restricted/office access;
- rear/service access;
- after-hours or unusual movement;
- safety/access obstructions;
- visibility improvements only where they materially affect security assurance.

Do not show routine camera-by-camera observations unless they contribute to one of these questions.

## Visualization rules

Every visual must answer one explicit management question.

Good examples:
- "When was customer demand strongest?" → line/area demand trend.
- "Which periods were most active?" → hourly distribution.
- "Where is capacity being used?" → table/floor utilization.
- "What security events need attention?" → exception timeline.
- "Did the operation close earlier than planned?" → annotated service-day timeline.

For an executive report:
- prefer one dominant chart over many small charts;
- annotate peaks, drops and exceptions;
- use tooltips/click states for detail;
- compare with a previous equivalent period only when the comparison is truly like-for-like;
- never imply numerical precision that the visual evidence cannot support.

## Unified period contract

Today, Yesterday, Last 7 days and Last 30 days are time-window selectors for the same reporting product. They must not switch to unrelated renderers or different information architecture.

Every window uses the same local views:
- **Overview** — management story, up to four decision KPIs, one primary period-appropriate visual, compact security posture and top actions.
- **Business** — demand, table/service flow and operating detail.
- **Security** — exception-based security and access-control detail.

Period-specific questions:
- **Today:** what is happening now, what needs attention now, and what should management do during the current service day?
- **Yesterday:** what happened in the completed service day, what was abnormal, and what should change next?
- **Last 7 days:** what repeated or changed across the week, and is any comparison with the previous week genuinely like-for-like?
- **Last 30 days:** what patterns are becoming operationally meaningful, without presenting sparse history as a complete month?

Evidence sufficiency is part of the product:
- do not render a complete-looking 7-day or 30-day trend when only a small fraction of the period is represented;
- withhold period comparisons unless both periods have enough represented service days;
- keep completed daily reports accessible even when there is not enough history for a period trend;
- missing days are unknown, not zero demand;
- report confidence/visibility belongs in progressive disclosure unless the monitoring gap is itself the main management issue.

AI explanation must not become a second report below the structured report. The deterministic report is authoritative; Ask WatchLog is the place for additional explanation.

## Navigation and interaction

A completed restaurant report should expose three local views:
- **Overview** — executive story, KPI strip, main chart, security posture, actions.
- **Business** — operational demand/service detail.
- **Security** — exception-based security detail.

Keep the service-window tabs (Today / Yesterday / 7 days / 30 days) separate from these local report views.

Interaction should reveal depth, not create novelty:
- selectable chart points;
- hover/click detail;
- expandable caveats and secondary recommendations;
- drill-down from overview to business/security detail.

## Visual hierarchy

Use a modern enterprise SaaS visual hierarchy:
- site name and date establish place and period;
- strongest outcome or exception gets the largest visual weight;
- KPI strip uses numbers plus plain-language labels;
- related information shares a surface;
- use borders, spacing and neutral surfaces more than heavy shadows;
- reserve accent/status colors for meaning, not decoration;
- avoid a wall of equally weighted cards;
- avoid repeating section headings that say the same thing.

On mobile, preserve the story order and convert multi-column surfaces to a single readable flow without hiding the primary actions.

## Security/business linkage

Physical-security insight is most valuable when linked to business consequence.

Examples:
- rear-route obstruction → closing/safety/access risk;
- early shuttering → reduced visible trading capacity;
- handoff congestion → service-channel risk;
- office access exception → management/security attention;
- camera glare/occlusion → weaker confidence in customer/security analytics.

Do not turn the report into a surveillance-system health page. Technical device health belongs elsewhere unless it directly changes confidence in a business/security conclusion.


## Recommendation feedback loop

Recommendations are not finished when they are merely displayed.

Every actionable recommendation in a saved report must have a stable machine-readable `id` in `action_items`. The ID must remain stable across wording changes for the same recommendation concept within that report revision lineage.

For each recommendation, the client-facing report should provide:

- **We'll do this** — the client accepts the recommendation.
- **Need help** — the client wants WatchLog / the service team to help implement or clarify it.
- **Not now** — the recommendation is understood but deferred.
- **Not relevant** — the recommendation does not fit the client's operation.
- **Add comment** — optional free-text context from the client.
- **Discuss with WatchLog** — opens Watch AI with the specific recommendation prefilled so the client can ask why it matters, what options exist, or how to implement it.

Client responses must be persisted against:
- the exact report;
- report revision;
- site and tenant;
- stable recommendation ID;
- authenticated client user;
- response code;
- optional client comment;
- timestamp.

Do not bury this feedback mechanism in a generic form at the bottom of the report. Put it directly beneath the recommendation being discussed.

### Internal follow-up

Recommendation responses must feed an internal WatchLog queue.

The team workflow is:
1. **New** — client response received and awaiting review.
2. **In progress** — a WatchLog team member is following up.
3. **Resolved** — the requested support/change has been handled.
4. **Closed** — no further action is required.

Internal team notes are private and must never be exposed in the client portal.

Accepted recommendations and requests for help should be treated as implementation signals. "Not relevant" and client comments are product/context feedback and should inform future tenant recommendations so the same unsuitable advice is not repeatedly surfaced without new evidence.

### Report payload requirement

Preferred `action_items` shape:

```json
{
  "id": "stable-recommendation-id",
  "title": "Short recommendation title",
  "priority": "Priority",
  "body": "Evidence-backed action in plain business language."
}
```

Do not publish a new interactive recommendation without a stable `id`; otherwise the saved response cannot be safely attached to the recommendation.
