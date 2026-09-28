# Visual Snapshot Analysis Method — Chai Wala / Restaurant

Use this method whenever a model is asked to create a restaurant report from stored camera snapshots.

For the client-facing report composition and portal UX, also follow `ai-harness/skills/restaurant-daily-business-report.md`.

## 1. Resolve the reporting window first

Read tenant context before images.

For Chai Wala:
- timezone: Asia/Karachi;
- service day: 16:00 to 04:00 next day;
- service date = date on which 16:00 opening occurs;
- Yesterday = latest completed service day, not midnight-to-midnight.

List every snapshot in the window before interpreting any image. Record total count, first/last snapshot, count by camera and uncovered periods.

## 2. Use canonical physical cameras only

Do not analyze hidden transport/profile duplicates as extra cameras. Camera role determines what may be extracted.

## 3. Analyze every snapshot camera-by-camera and chronologically

Do not sample a few frames and extrapolate when the task requests all snapshots.

Recommended order:
1. Floor 1
2. Floor 2
3. Shop Front
4. Kitchen
5. Cash Counter
6. Back Entrance
7. Office View
8. Office Camera

Preserve snapshot ID and local capture time for traceability.

## 4. Dining-floor extraction

For Floor 1 / Floor 2 extract only visually defensible:
- concurrent visible diners;
- occupied calibrated table anchors;
- visible party size per table;
- food visibly present;
- drinks visibly present where clear;
- staff visibly present;
- clearing/reset state;
- adjacent tables visibly combined into one party;
- people/table visibility confidence;
- glare, overexposure, occlusion, obstruction and camera-angle quality.

Tables are movable. Preserve calibrated table identity inside its expected zone. Joined adjacent tables keep underlying keys and share one combined_group. If movement makes identity uncertain, lower confidence and recommend recalibration rather than guessing.

## 5. Other camera roles

Shop Front: service/handoff pressure and staff pickup activity; never customer footfall.
Kitchen: visible operational pressure/congestion; never food quality, order accuracy or staff productivity.
Cash Counter: attended/unattended and visible interactions; never sales/revenue/transaction count.
Back Entrance: service/delivery/access activity; not customer footfall.
Office views: security/presence only; not restaurant customer-volume metrics.

## 6. Build sequences before derived metrics

Do not derive a session from one frame.

Across chronological frames:
- start a table session on a defensible empty/unknown to occupied transition;
- estimated covers = peak visible party size once per estimated session;
- served session = food becomes visibly present at least once;
- observed time to food = first occupied/seated evidence to first food-visible evidence;
- minimum observed dwell = first occupied to last observed occupied frame.

Label session/cover metrics as estimated or observed-derived.

## 7. Site-wide totals

Only combine Floor 1 and Floor 2 when observations are sufficiently time-aligned.

Before combining, check whether the two cameras overlap physically. When they show the same seating area from different angles, de-duplicate the same party/table instead of adding both camera counts. If overlap cannot be resolved confidently, report a range or report the camera zones separately.

Never call visible diners footfall or unique customers. True footfall is unsupported until a clean validated entry counting line exists.

## 8. Image-quality and improvement pass

Assess repeated glare, overexposure, occlusion, obstruction, weak camera angle, blind spots, table-anchor drift and low count confidence.

A single bad frame is not enough for a physical recommendation.

Model confidence is not measured accuracy. Publish a customer-count accuracy percentage only after representative frames are manually counted and compared with WatchLog.

## 9. Client-facing report boundary

The client report is an owner/management product, not a technical audit.

Client-facing output should lead with:
- demand timing and customer/table use;
- service-flow continuity;
- kitchen/handoff/counter operations;
- security/access exceptions;
- closing/opening discipline;
- customer-facing presentation;
- practical, evidence-backed actions.

Do not expose implementation details such as snapshot counts, worker names, queue status, model/provider names, RPC/function names, processing attempts, database table names, egress policy, internal IDs or pipeline failures in the owner report.

If evidence is incomplete, describe the business observation window in plain language, for example: "This brief covers the observed evening period from 8:42 PM onward." Keep the technical reason and exact audit inventory in an internal-only audit.

A completed human/manual visual review may be used as the authoritative daily business brief even when automated structured extraction is unavailable. Do not place an internal "processing" state above a completed reviewed report.

## 10. Reporting order

Use the dedicated restaurant report skill for the rendered client experience.

For Chai Wala the default order is:
1. Site name + service date.
2. What mattered: 2–3 business pointers.
3. Maximum four business KPIs.
4. Interactive demand/service visual when the evidence supports a trend.
5. Dining and service operations grouped by business function, not by camera.
6. Security/control exceptions.
7. Top three actions.
8. Expandable secondary actions, evidence-window notes and visibility/camera improvements.

Do not repeat the same finding in multiple major sections. Do not place technical processing or coverage state above a completed business report.

## 11. Truth rules

Missing coverage = unknown, not zero.
One frame = observation, not trend.
No identity, demographics, emotion, intent or wrongdoing inference.
No sales/revenue/order count without transactional data.
Keep observed, estimated and unsupported claims separate.
