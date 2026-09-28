# Visual Snapshot Analysis — Office

Use after reading the tenant context and office site-type policy.

## Window
- Today = current configured working day and must be marked incomplete before close.
- Yesterday = latest completed configured working day; skip non-working days.
- Daily office activity uses the configured open→close window.
- After-hours activity is analyzed separately, not mixed into normal office activity.

## Sequence
1. Enumerate every snapshot/event in the window and coverage gaps.
2. Use canonical physical cameras only.
3. Confirm camera role/mapping before role-specific interpretation.
4. Analyze each camera chronologically.
5. Build movement/dwell episodes before discussing patterns.
6. Preserve source ID + local timestamp for material observations.

## Allowed extraction
- visible presence;
- activity detections / episodes;
- opening/closing evidence when entrance mapping is confirmed;
- reception waiting/activity when reception mapping is confirmed;
- management/restricted-area presence when mapping is confirmed;
- corridor/entrance movement;
- parking/vehicle activity where relevant;
- after-hours presence;
- monitoring and image-quality issues.

Never turn detections into unique people or visitors. Never infer identity, employee status, intent, productivity, demographics or wrongdoing.

## Quality and recommendations
Assess repeated glare, obstruction, occlusion, weak angle, blind spots, timestamp/coverage gaps and mapping ambiguity.
Recommendations require repeated evidence or a confirmed configuration/health problem.

## Reporting order
1. Coverage/data quality.
2. Security attention.
3. Opening/closing and normal office activity.
4. Entrance/reception/management/restricted areas only where mapped.
5. After-hours activity.
6. Parking/perimeter if applicable.
7. Monitoring/camera quality.
8. Evidence-backed improvements.


## Al-Khalid Main Site override
The recorder exposes 8 canonical physical cameras, but legacy ONVIF profile labels conflicted.
Do not assign Reception, Directors Office, Armory Gate or Admin Entrance to a numbered camera until the current physical view is visually re-confirmed.
Until then, report general visible activity by camera number and mapping confidence; do not manufacture role-specific conclusions.
