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
- manual review status is persisted when manual review was used.


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
- table utilization where defensible;
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
