# Analytics Studio v1 data model

This is the canonical data contract used by the migration and portal/agent code.

## Entities

### site_profiles
One row per WatchLog site.

Fields:
- site_id
- tenant_id
- site_type
- timezone
- operating_hours_json
- analytics_config_version
- created_at
- updated_at

### camera_profiles
One row per WatchLog camera.

Fields:
- camera_id
- tenant_id
- site_id
- purpose
- display_name
- config_snapshot_path
- analytics_enabled
- created_at
- updated_at

### monitoring_schedules
Reusable site schedules.

Fields:
- id
- tenant_id
- site_id
- name
- timezone
- schedule_json
- enabled

### monitoring_rules
One camera can have many rules.

Fields:
- id
- tenant_id
- site_id
- camera_id
- rule_type
- name
- object_classes
- geometry_json
- direction_json
- schedule_id
- dwell_seconds
- enabled
- severity
- config_version

### analytic_events
Business measurements emitted by the Site Agent. These are not incidents.

Fields:
- id
- tenant_id
- site_id
- camera_id
- monitoring_rule_id
- event_type
- object_class
- track_key
- direction
- occurred_at
- duration_seconds
- metadata_json
- dedupe_key

### analytic_daily
Daily aggregate facts by rule/site/camera.

Fields:
- tenant_id
- site_id
- camera_id
- monitoring_rule_id
- local_date
- metric
- object_class
- direction
- value

## Geometry convention

All coordinates are normalized to the source frame:

- x: 0.0 to 1.0
- y: 0.0 to 1.0

Line example:

```json
{"type":"line","points":[[0.12,0.55],[0.88,0.55]]}
```

Polygon example:

```json
{"type":"polygon","points":[[0.2,0.2],[0.8,0.2],[0.85,0.8],[0.15,0.8]]}
```

The agent converts these into pixel coordinates for its current frame size.

## Event semantics

Measurement event examples:
- line_crossing
- zone_entry
- zone_exit
- dwell_completed
- schedule_activity

Incidents remain reserved for events worth human review. A monitoring rule may optionally promote a measurement to an incident when its severity/conditions require it.
