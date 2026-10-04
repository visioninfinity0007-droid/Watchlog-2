import { chromium } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

const BASE = process.env.VISUAL_QA_BASE || "http://127.0.0.1:3100";
const SUPABASE = "https://visual-qa.supabase.co";
const SITE_ID = "11111111-1111-4111-8111-111111111111";
const SITE_2_ID = "22222222-2222-4222-8222-222222222222";
const TENANT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const USER_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const OUT = path.resolve("visual-qa-output");

const now = "2026-10-01T08:00:00Z";
const jwtPayload = Buffer.from(JSON.stringify({
  sub: USER_ID,
  email: "owner@example.com",
  aud: "authenticated",
  role: "authenticated",
  exp: 4102444800,
})).toString("base64url");
const accessToken = `eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.${jwtPayload}.visualqa`;
const session = {
  access_token: accessToken,
  refresh_token: "visual-qa-refresh",
  expires_in: 3600,
  expires_at: 4102444800,
  token_type: "bearer",
  user: {
    id: USER_ID,
    aud: "authenticated",
    role: "authenticated",
    email: "owner@example.com",
    email_confirmed_at: now,
    app_metadata: { provider: "email", providers: ["email"] },
    user_metadata: {},
    created_at: now,
  },
};

const sites = [
  {
    id: SITE_ID,
    name: "Harbour Branch",
    timezone: "Asia/Karachi",
    online: true,
    cameras: 6,
    events: 1842,
    setup_state: "ready",
    last_event: "2026-10-01T07:54:00Z",
  },
  {
    id: SITE_2_ID,
    name: "North Warehouse",
    timezone: "Asia/Karachi",
    online: false,
    cameras: 4,
    events: 731,
    setup_state: "ready",
    last_event: "2026-10-01T07:21:00Z",
  },
];

const context = {
  connectivity: { agent_online: true, last_seen: "2026-10-01T07:58:00Z" },
  coverage: { coverage_ratio: 0.96, classes: { live_seconds: 82920, recovered_seconds: 0, unverified_seconds: 3480 } },
  recorder: { identified: true, vendor: "Hikvision", model: "NVR" },
  capability_known: true,
  business_context: { site_type: "office", open_time: "09:00:00", close_time: "18:00:00", working_days: [1, 2, 3, 4, 5] },
  cameras: [
    { id: "c1", channel: 1, name: "Main entrance", monitor: true, health_state: "operational", recording_state: "recording", purpose: "entrance" },
    { id: "c2", channel: 2, name: "Service area", monitor: true, health_state: "operational", recording_state: "recording", purpose: "queue" },
    { id: "c3", channel: 3, name: "Rear access", monitor: true, health_state: "degraded", recording_state: "unknown", purpose: null },
    { id: "c4", channel: 4, name: "Office", monitor: true, health_state: "operational", recording_state: "recording", purpose: "management" },
  ],
  faults: [
    { id: "fault-1", camera: "Rear access", reason: "not_recording", severity: "warning" },
  ],
  onboarding: {
    steps: [
      { key: "connection", label: "WatchLog connected", done: true },
      { key: "recorder", label: "Camera system connected", done: true },
      { key: "cameras", label: "Cameras mapped", done: true },
      { key: "approval", label: "Setup approved", done: true },
      { key: "monitoring", label: "Monitoring active", done: true },
    ],
  },
};

const notifications = [
  {
    id: "n1",
    kind: "health",
    severity: "warning",
    read: false,
    title: "Rear access recording is not confirmed",
    body: "WatchLog can see the camera, but recording status still needs verification.",
    created_at: "2026-10-01T07:42:00Z",
    site_name: "Harbour Branch",
    href: `/site-health/?site=${SITE_ID}`,
  },
  {
    id: "n2",
    kind: "incident",
    severity: "attention",
    read: false,
    title: "After-hours activity needs review",
    body: "Activity was recorded near the rear access outside the configured working period.",
    created_at: "2026-10-01T06:58:00Z",
    site_name: "Harbour Branch",
    href: `/incidents/?site=${SITE_ID}`,
  },
  {
    id: "n3",
    kind: "report",
    severity: "info",
    read: false,
    title: "Yesterday's management report is ready",
    body: "The completed report is available with monitoring coverage and key activity.",
    created_at: "2026-10-01T05:30:00Z",
    site_name: "Harbour Branch",
    href: `/reports/?view=yesterday&site=${SITE_ID}`,
  },
];

const analytics = {
  summary: { visitor_in: 42, vehicles_in: 7, zone_entries: 58, after_hours: 1 },
  by_rule: [
    { rule_id: "r1", count: 42, name: "Visitor flow", camera: "Main entrance", rule_type: "line_crossing", analytic_key: "visitor_flow" },
    { rule_id: "r2", count: 18, name: "Service area activity", camera: "Service area", rule_type: "zone_presence", analytic_key: "zone_activity" },
    { rule_id: "r3", count: 7, name: "Vehicle flow", camera: "Rear access", rule_type: "line_crossing", analytic_key: "vehicle_flow" },
  ],
};

const reportSnapshot = {
  report_id: "report-visual-qa",
  report_date: "2026-09-30",
  payload: {
    report_date: "2026-09-30",
    site_name: "Harbour Branch",
    title: "Daily management brief",
    ai_summary: "The site remained broadly stable. One rear-access recording check needs follow-up, while the rest of the monitored areas were represented normally.",
    metrics: [
      { label: "Monitoring coverage", value: "96%", note: "Verified portion of the reporting period" },
      { label: "Visitor entries", value: "42", note: "Configured entrance-rule detections" },
      { label: "After-hours activity", value: "1", note: "One item needs review" },
      { label: "Cameras healthy", value: "3/4", note: "One camera remains not fully verified" },
    ],
    incidents: [
      { title: "Rear access recording needs verification", body: "Recording status could not be confirmed for part of the period.", severity: "attention" },
    ],
    site_insights: [
      { title: "Entrance activity was concentrated in business hours", body: "Configured entrance rules recorded most activity during the normal working period.", severity: "none" },
      { title: "Service area remained active through the afternoon", body: "Activity was sustained without a separate security exception.", severity: "none" },
    ],
    coverage: {
      status: "Mostly verified",
      period: "23h 02m verified",
      summary: "WatchLog verified most of the reporting period.",
      note: "58 minutes could not be verified and are not treated as quiet time.",
    },
    camera_coverage: [
      { camera: "Main entrance", status: "Covered", period: "Business hours", management_view: "Entrance flow", assessment: "The entrance was represented consistently." },
      { camera: "Rear access", status: "Needs attention", period: "Partial", management_view: "Rear access", assessment: "Recording verification is incomplete for part of the period." },
    ],
    action_items: [
      { id: "rec-1", title: "Confirm rear-access recording", body: "Check the recorder channel for Rear access so the full working period is verifiable.", priority: "Priority" },
      { id: "rec-2", title: "Review the after-hours entry", body: "Open the evidence for the 9:41 pm rear-access activity and confirm whether it was expected.", priority: "Next" },
    ],
    priority_actions: [],
  },
};

const tableData = {
  cameras: [
    { id: "c1", name: "Main entrance", channel: 1, site_id: SITE_ID },
    { id: "c2", name: "Service area", channel: 2, site_id: SITE_ID },
    { id: "c3", name: "Rear access", channel: 3, site_id: SITE_ID },
  ],
  monitoring_rules: [
    { id: "r1", name: "Visitor flow", site_id: SITE_ID },
    { id: "r2", name: "After-hours activity", site_id: SITE_ID },
  ],
  archive_scans: [
    {
      id: "scan-1",
      site_id: SITE_ID,
      status: "complete",
      requested_at: "2026-10-01T07:10:00Z",
      from_ts: "2026-09-30T18:00:00Z",
      to_ts: "2026-09-30T20:00:00Z",
      camera_ids: ["c1","c3"],
    },
    {
      id: "scan-2",
      site_id: SITE_ID,
      status: "queued",
      requested_at: "2026-10-01T06:15:00Z",
      from_ts: "2026-09-29T17:00:00Z",
      to_ts: "2026-09-29T18:30:00Z",
      camera_ids: ["c2"],
    },
  ],
  camera_snapshot_signals: [],
};

// Visual-QA fixture only: a governed office working-day period with one unobserved day, so the
// owner surfaces can be checked rendering a gap (never a zero) and a qualified comparison.
const officeDays = [
  ["2026-09-22", 0.94, 96, 0], ["2026-09-23", 0.97, 104, 1], ["2026-09-24", 0.91, 88, 0],
  ["2026-09-25", 0, 0, 0], ["2026-09-28", 0.95, 121, 2], ["2026-09-29", 0.92, 97, 0], ["2026-09-30", 0.96, 106, 1],
];
const officePeriod = {
  enabled: true,
  schema: "office-period-v1",
  window_type: "completed_working_days",
  period: { start_date: "2026-09-22", end_date: "2026-09-30", previous_start_date: "2026-09-11", previous_end_date: "2026-09-21" },
  summary: { days: 7, observed_days: 6, avg_coverage_ratio: 0.807, incidents_total: 3, critical_total: 0, after_hours_total: 4, activity_detections: 612 },
  previous_period: { days: 7, observed_days: 7, avg_coverage_ratio: 0.912, incidents_total: 2, critical_total: 0, after_hours_total: 1, activity_detections: 574 },
  comparison: { coverage_delta_points: -10.5, incidents_delta: 1, critical_delta: 0, after_hours_delta: 3, activity_detections_delta: 38 },
  daily: officeDays.map(([date, coverage_ratio, activity_detections, after_hours_count]) => ({
    date, working_day: true, coverage_ratio, incidents_total: after_hours_count, critical: 0, after_hours_count, activity_detections,
  })),
};

// Visual-QA fixture only: the multi-site Control Room overview (one site connection quiet).
const portalOverview = {
  tenant: { id: TENANT_ID, name: "Visual QA tenant" },
  totals: { events: 2573, sites: 2, cameras: 10 },
  agents: [
    { site: "Harbour Branch", hostname: "HB-OFFICE-PC", last_seen_at: "2026-10-01T07:58:00Z" },
    { site: "North Warehouse", hostname: "NW-STORE-PC", last_seen_at: "2026-10-01T05:21:00Z" },
  ],
  recent: [
    { event_id: "ev-1", site: "Harbour Branch", camera: "Main entrance", event_type: "person", device_ts: "2026-10-01T07:41:00Z", has_snapshot: false },
    { event_id: "ev-2", site: "Harbour Branch", camera: "Rear access", event_type: "vehicle", device_ts: "2026-10-01T06:58:00Z", has_snapshot: false },
    { event_id: "ev-3", site: "North Warehouse", camera: "Loading bay", event_type: "person", device_ts: "2026-10-01T05:02:00Z", has_snapshot: false },
  ],
  health: {
    offline_agents: [{ site: "North Warehouse", hostname: "NW-STORE-PC", last_seen_at: "2026-10-01T05:21:00Z" }],
    silent_cameras: [{ site: "Harbour Branch", camera: "Rear access", last_event_at: "2026-09-30T21:41:00Z" }],
  },
  sites: [{ id: SITE_ID, name: "Harbour Branch" }, { id: SITE_2_ID, name: "North Warehouse" }],
};

const rpcData = {
  wl_portal_overview: portalOverview,
  // Camera Settings: mixed capability evidence so verified / documented / unsupported / unknown all render.
  wl_my_site_diagnosis: {
    site_control_enabled: true, role: "owner", tiers: { recommend: true, approve: true },
    recorder: { identified: true, vendor: "Hikvision", model: "NVR" },
    connectivity: { agent_online: true, last_seen: "2026-10-01T07:58:00Z" },
    cameras: [
      { channel: 1, name: "Main entrance", purpose: "entrance" }, { channel: 2, name: "Service area", purpose: "queue" },
      { channel: 3, name: "Rear access", purpose: null }, { channel: 4, name: "Office", purpose: "management" },
    ],
    capabilities: [
      { capability: "time_sync", verdict: "supported", evidence_class: "FIELD_VERIFIED" },
      { capability: "motion_detection", verdict: "supported", evidence_class: "OFFICIAL_DOCUMENTED" },
      { capability: "line_crossing", verdict: "unsupported", evidence_class: "UNSUPPORTED", reason: "This recorder does not offer line crossing." },
      { capability: "face_detection", verdict: "unknown", evidence_class: "UNKNOWN" },
    ],
  },
  wl_onboarding_status: context.onboarding,
  wl_members: [
    { user_id: USER_ID, email: "owner@example.com", role: "owner", is_you: true, joined: "2026-08-02T09:00:00Z" },
    { user_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", email: "operations@example.com", role: "admin", is_you: false, joined: "2026-08-20T09:00:00Z" },
  ],
  wl_invitations: [
    { id: "inv-1", email: "manager@example.com", role: "viewer", created_at: "2026-09-29T09:00:00Z", expires_at: "2026-10-06T09:00:00Z", expired: false },
  ],
  wl_office_period: officePeriod,
  wl_my_account: { account_status: "active" },
  wl_my_tenant: TENANT_ID,
  wl_platform_me: {},
  wl_sites: sites,
  wl_ai_conversations: [
    { id: "conv-1", title: "Morning overview", site_id: SITE_ID, site_name: "Harbour Branch" },
    { id: "conv-2", title: "Recording check", site_id: SITE_ID, site_name: "Harbour Branch" },
  ],
  wl_ai_context: context,
  wl_notifications: notifications,
  wl_my_daily_intelligence: {
    meta: { date: "2026-09-30" },
    coverage: { coverage_ratio: 0.96, classes: { live_seconds: 82920, recovered_seconds: 0, unverified_seconds: 3480 } },
    attention: { incidents_total: 1, critical: 0, warning: 1 },
    after_hours: { count: 1, verified: true },
    day_boundaries: { opening_at: "08:52", closing_at: "18:41", confidence: "high" },
    access_windows: [
      ["Main entrance", "entrance", "08:52", "09:05"], ["Main entrance", "entrance", "09:20", "09:40"], ["Main entrance", "entrance", "11:00", "11:30"],
      ["Main entrance", "entrance", "13:05", "13:40"], ["Main entrance", "entrance", "17:45", "18:41"],
      ["Office", "management", "09:30", "12:30"], ["Office", "management", "14:00", "17:30"],
    ].map(([camera, purpose, start, end]) => ({ camera, purpose, type: "presence", object_class: "person", start, end, detections: 6 })),
    office: {
      coverage: { person_events: 106 },
      peak_hour: { hour: 11, count: 19 },
      by_area: [
        { camera: "Main entrance", events: 42 },
        { camera: "Service area", events: 31 },
        { camera: "Office", events: 22 },
        { camera: "Rear access", events: 11 },
      ],
    },
    restricted: [],
    incidents: [],
  },
  wl_analytics_overview: analytics,
  wl_restaurant_site_config: { enabled: false },
  wl_my_site_context: { site_type: "office", reporting_prefs: { report_layout_profile: "office_ops_v1" } },
  wl_my_report_window: {
    saved_reports: [
      { report_id: "report-visual-qa", service_date: "2026-09-30", summary: "Completed management report", highlights: ["Rear-access recording verification needs follow-up."] },
    ],
  },
  wl_my_last_completed_business_date: "2026-09-30",
  wl_my_report_snapshot: reportSnapshot,
  wl_analytics_studio: {
    can_manage: true,
    sites: [
      {
        id: SITE_ID,
        name: "Harbour Branch",
        runtime_capabilities: ["archive_processing","operations_evidence_still"],
        cameras: context.cameras,
        schedules: [],
      },
    ],
    primitives: [],
  },
  wl_camera_config_snapshot: null,
  wl_my_role: "owner",
  wl_ai_site_egress: { site_id: SITE_ID, external_egress_allowed: false, external_text_egress_allowed: true },
};

function rpcName(url) {
  const marker = "/rest/v1/rpc/";
  const i = url.pathname.indexOf(marker);
  return i >= 0 ? decodeURIComponent(url.pathname.slice(i + marker.length)) : "";
}

// Visual-QA fixture only: a restaurant site with a governed 7-day service period (one service day
// not observed) so restaurant semantics (estimated covers, visible diners, observed time to food)
// and gap rendering are checked separately from the office path.
const restaurantDaily = [
  ["2026-09-24", 61, 0.82], ["2026-09-25", 74, 0.86], ["2026-09-26", 92, 0.9], ["2026-09-27", null, 0],
  ["2026-09-28", 58, 0.79], ["2026-09-29", 66, 0.84], ["2026-09-30", 71, 0.88],
];
const restaurantHourly = ["16:00","17:00","18:00","19:00","20:00","21:00","22:00","23:00","00:00","01:00"].map((h, i) => ({
  local_hour: h, samples: i === 9 ? 0 : 12, peak_visible_customers: [6, 9, 14, 22, 27, 24, 18, 11, 7, 0][i], peak_occupied_tables: [3, 4, 6, 9, 11, 10, 8, 5, 3, 0][i],
}));
const restaurantOverrides = {
  wl_restaurant_site_config: { enabled: true, report_layout_profile: "chaiwala_restaurant_ops_v1" },
  wl_my_site_context: { site_type: "restaurant" },
  wl_office_period: { enabled: false, site_type: "restaurant" },
  wl_restaurant_day: {
    enabled: true, service_date: "2026-09-30",
    data_quality: { camera_observations: 118, table_observations: 96, business_analytics_coverage_ratio: 0.88 },
    sessions: { estimated_covers: 71, served_sessions: 24, median_observed_time_to_food_minutes: 14 },
    hourly: restaurantHourly, floors: [], tables: [],
  },
  wl_my_daily_intelligence: { coverage: { coverage_ratio: 0.88 }, incidents: [], attention: { incidents_total: 0 } },
  wl_my_report_snapshot: {
    report_id: "restaurant-report-visual-qa", report_date: "2026-09-30",
    payload: {
      report_date: "2026-09-30", site_type: "restaurant", coverage: { coverage_ratio: 0.88 },
      metrics: [
        { label: "Estimated covers", value: "71" }, { label: "Peak visible diners", value: "27" },
        { label: "Observed time to food", value: "14 min" }, { label: "Served table sessions", value: "24" },
      ],
      highlights: ["Visible demand peaked around 8 pm, when 27 diners were visible at once."],
      incidents: [], action_items: [
        { id: "rest-rec-1", title: "Add a server at 7–9 pm", body: "Observed time to food rose during the busiest two hours.", priority: "Priority" },
      ],
    },
  },
  wl_my_report_window: {
    saved_reports: restaurantDaily.filter(d => d[1] !== null).map(([d]) => ({ report_id: "r-" + d, service_date: d, summary: "Completed service day" })),
    structured_restaurant_metrics: {
      enabled: true, schema: "restaurant-period-v2", days: 7,
      period: { start_service_date: "2026-09-24", end_service_date: "2026-09-30" },
      summary: { expected_service_days: 7, observed_service_days: 6, total_estimated_covers: 422, avg_estimated_covers_per_observed_day: 70.3, served_sessions: 141, median_observed_time_to_food_minutes: 13.5, avg_coverage_ratio: 0.727, busiest_day: { service_date: "2026-09-26", estimated_covers: 92 }, busiest_hour: { local_hour: "20:00" } },
      previous_period: { observed_service_days: 7, total_estimated_covers: 395, avg_estimated_covers_per_observed_day: 56.4, median_observed_time_to_food_minutes: 12.1, avg_coverage_ratio: 0.81 },
      comparison: { estimated_covers_delta: 27, estimated_covers_pct: 6.8, served_sessions_delta: 9, median_time_to_food_delta_minutes: 1.4, coverage_delta_points: -8.3 },
      daily: restaurantDaily.map(([service_date, covers, coverage_ratio]) => ({
        service_date, camera_observations: covers === null ? 0 : 110, table_observations: covers === null ? 0 : 90,
        estimated_covers: covers ?? 0, coverage_ratio,
      })),
      hour_profile: restaurantHourly.slice(0, 9).map(h => ({ local_hour: h.local_hour, observed_days: 6, avg_peak_visible_diners: Math.round(h.peak_visible_customers * 0.9 * 10) / 10 })),
    },
  },
};

// Visual-QA fixtures only (Phase 28): one governed business day per site type, built from activity
// episodes on cameras with configured purposes. Coverage is fully verified so operational-gap claims are
// allowed; the period comparison is enabled:false until migration 0143 is applied in production.
const hm = m => String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
const toM = s => Number(s.slice(0, 2)) * 60 + Number(s.slice(3, 5));
function windows(camera, purpose, ranges, objectClass = "person") {
  return ranges.map(([s, e]) => ({ camera, purpose, type: "presence", object_class: objectClass, start: s, end: e, dwell_seconds: (toM(e) - toM(s)) * 60, detections: 6 }));
}
// n short episodes spread through each hour (retail traffic shape).
function hourly(camera, purpose, counts, minutes) {
  const out = [];
  for (const [h, n] of Object.entries(counts)) for (let i = 0; i < n; i++) {
    const s = Number(h) * 60 + Math.floor((i * 60) / n) + 2;
    out.push({ camera, purpose, type: "presence", object_class: "person", start: hm(s), end: hm(s + minutes), dwell_seconds: minutes * 60, detections: 4 });
  }
  return out;
}
const verifiedCoverage = { coverage_ratio: 1, classes: { live_seconds: 86400, recovered_seconds: 0, unverified_seconds: 0 }, gaps: [] };
function siteCamera(id, channel, name, purpose) {
  return { id, channel, name, purpose, monitor: true, health_state: "operational", recording_state: "recording" };
}
function siteTypeOverrides({ type, name, cameras, open, close, episodes, opening, closing }) {
  const byArea = cameras.map(c => ({ camera: c.name, events: episodes.filter(e => e.camera === c.name).reduce((n, e) => n + e.detections, 0) })).filter(a => a.events > 0).sort((a, b) => b.events - a.events);
  const perHour = {};
  for (const e of episodes) { const h = Number(e.start.slice(0, 2)); perHour[h] = (perHour[h] || 0) + e.detections; }
  const peak = Object.entries(perHour).sort((a, b) => b[1] - a[1])[0];
  const site = { ...sites[0], name, site_type: type };
  return {
    wl_sites: [site, ...sites.slice(1)],
    wl_ai_context: { ...context, cameras, faults: [], business_context: { site_type: type, open_time: open + ":00", close_time: close + ":00", working_days: [1, 2, 3, 4, 5, 6] }, coverage: { coverage_ratio: 1, classes: verifiedCoverage.classes } },
    wl_my_site_context: { site_type: type },
    wl_office_period: { enabled: false, site_type: type },
    wl_my_daily_intelligence: {
      meta: { date: "2026-09-30", timezone: "Asia/Karachi" },
      coverage: verifiedCoverage,
      attention: { incidents_total: 0, critical: 0, warning: 0 },
      after_hours: { count: 0, verified: true },
      day_boundaries: { opening_at: opening, closing_at: closing, confidence: "high", after_hours: { count: 0, verified: true } },
      office: { coverage: { person_events: byArea.reduce((n, a) => n + a.events, 0), first: opening, last: closing }, peak_hour: { hour: Number(peak[0]), count: peak[1] }, by_area: byArea },
      access_windows: episodes.sort((a, b) => a.start.localeCompare(b.start)),
      restricted: [], incidents: [],
    },
    wl_my_report_snapshot: {
      report_id: "report-" + type, report_date: "2026-09-30",
      payload: {
        report_date: "2026-09-30", site_name: name, title: "Daily management brief", site_type: type,
        ai_summary: "Monitoring covered the full " + (type === "factory" ? "shift day" : type === "retail" ? "trading day" : "working day") + " and no security exception was recorded.",
        metrics: [{ label: "Monitoring coverage", value: "100%" }],
        incidents: [], site_insights: [], camera_coverage: [],
        coverage: { status: "Verified", period: "24h verified", summary: "WatchLog verified the whole reporting period." },
        action_items: [{ id: type + "-rec-1", title: "Review the operating pattern below", body: "Use the area timeline to confirm the day ran as planned.", priority: "Next" }],
        priority_actions: [],
      },
    },
  };
}

const warehouseCameras = [
  siteCamera("w1", 1, "Main gate", "gate"), siteCamera("w2", 2, "Truck yard", "yard"),
  siteCamera("w3", 3, "Dock 1", "loading_dock"), siteCamera("w4", 4, "Dock 2", "loading_dock"),
  siteCamera("w5", 5, "Receiving bay", "receiving"), siteCamera("w6", 6, "Dispatch lane", "dispatch"),
  siteCamera("w7", 7, "Bonded store", "restricted_storage"),
];
const warehouseOverrides = siteTypeOverrides({
  type: "warehouse", name: "Port Qasim Warehouse", cameras: warehouseCameras, open: "08:00", close: "18:00", opening: "07:58", closing: "18:06",
  episodes: [
    ...windows("Main gate", "gate", [["07:55", "08:05"], ["12:30", "12:40"], ["17:55", "18:06"]]),
    ...windows("Main gate", "gate", [["08:10", "08:25"], ["10:05", "10:12"], ["13:30", "13:40"], ["15:50", "16:20"]], "vehicle"),
    ...windows("Truck yard", "yard", [["08:30", "09:10"], ["14:20", "15:05"]], "vehicle"),
    ...windows("Dock 1", "loading_dock", [["08:40", "09:50"], ["10:10", "11:40"], ["14:05", "15:30"], ["16:00", "17:20"]]),
    ...windows("Dock 1", "loading_dock", [["08:45", "09:40"], ["14:10", "15:20"]], "vehicle"),
    ...windows("Dock 2", "loading_dock", [["09:00", "10:30"], ["10:45", "11:35"], ["14:10", "16:40"]]),
    ...windows("Receiving bay", "receiving", [["08:20", "09:30"], ["10:00", "11:20"], ["12:00", "13:40"], ["14:30", "16:30"], ["17:00", "17:40"]]),
    ...windows("Dispatch lane", "dispatch", [["09:30", "11:00"], ["11:30", "12:30"], ["13:00", "14:00"], ["14:30", "17:45"]]),
    ...windows("Bonded store", "restricted_storage", [["10:15", "10:20"]]),
  ],
});

// Visual-QA fixture only: the site-neutral period (site-period-v1) a warehouse receives once migration
// 0143 is applied. One working day not observed. Factory and retail keep the pre-migration state (no
// wl_site_period available), so their period views show the honest "not available yet" state.
const whDays = [["2026-09-23", 0.98, 38, 9], ["2026-09-24", 0.97, 41, 8], ["2026-09-25", 0, 0, 0], ["2026-09-26", 0.99, 44, 11],
  ["2026-09-27", 0.96, 36, 7], ["2026-09-29", 1, 39, 10], ["2026-09-30", 1, 31, 8]];
warehouseOverrides.wl_site_period = {
  enabled: true, schema: "site-period-v1", site_type: "warehouse", window_type: "completed_working_days",
  period: { start_date: "2026-09-23", end_date: "2026-09-30", previous_start_date: "2026-09-15", previous_end_date: "2026-09-22" },
  summary: {
    days: 7, observed_days: 6, avg_coverage_ratio: 0.843, incidents_total: 1, critical_total: 0, after_hours_total: 2,
    activity_episodes: 229, activity_detections: 1374, vehicle_episodes: 53,
    by_purpose: [
      { purpose: "loading_dock", episodes: 58, vehicle_episodes: 12, detections: 348 }, { purpose: "receiving", episodes: 41, vehicle_episodes: 0, detections: 246 },
      { purpose: "dispatch", episodes: 33, vehicle_episodes: 0, detections: 198 }, { purpose: "gate", episodes: 30, vehicle_episodes: 27, detections: 180 },
      { purpose: "yard", episodes: 0, vehicle_episodes: 14, detections: 0 }, { purpose: "restricted_storage", episodes: 4, vehicle_episodes: 0, detections: 24 },
    ],
  },
  previous_period: {
    days: 7, observed_days: 7, avg_coverage_ratio: 0.97, incidents_total: 0, critical_total: 0, after_hours_total: 1,
    activity_episodes: 251, activity_detections: 1506, vehicle_episodes: 61,
    by_purpose: [
      { purpose: "loading_dock", episodes: 74, vehicle_episodes: 15 }, { purpose: "receiving", episodes: 39 }, { purpose: "dispatch", episodes: 36 },
      { purpose: "gate", episodes: 31, vehicle_episodes: 30 }, { purpose: "yard", vehicle_episodes: 16 }, { purpose: "restricted_storage", episodes: 2 },
    ],
  },
  comparison: { coverage_delta_points: -12.7, incidents_delta: 1, critical_delta: 0, after_hours_delta: 1, activity_episodes_delta: -22, activity_detections_delta: -132, vehicle_episodes_delta: -8 },
  daily: whDays.map(([date, coverage_ratio, activity_episodes, vehicle_episodes]) => ({ date, working_day: true, coverage_ratio, activity_episodes, vehicle_episodes, after_hours_count: 0, incidents_total: 0 })),
  measurement_notes: [],
};

const factoryCameras = [
  siteCamera("f1", 1, "Factory gate", "gate"), siteCamera("f2", 2, "Line A", "production_zone"),
  siteCamera("f3", 3, "Assembly hall", "assembly"), siteCamera("f4", 4, "Packing", "packing"),
  siteCamera("f5", 5, "Raw material store", "raw_materials"), siteCamera("f6", 6, "Finished goods", "finished_goods"),
  siteCamera("f7", 7, "Dispatch", "dispatch"), siteCamera("f8", 8, "Maintenance bay", "maintenance"),
];
const factoryOverrides = siteTypeOverrides({
  type: "factory", name: "Lahore Plant", cameras: factoryCameras, open: "07:00", close: "19:00", opening: "06:52", closing: "19:08",
  episodes: [
    ...windows("Factory gate", "gate", [["06:50", "07:10"], ["18:55", "19:08"]]),
    ...windows("Factory gate", "gate", [["09:00", "09:20"], ["15:00", "15:30"]], "vehicle"),
    ...windows("Line A", "production_zone", [["07:05", "12:55"], ["14:15", "18:50"]]),
    ...windows("Assembly hall", "assembly", [["07:20", "12:40"], ["13:30", "18:40"]]),
    ...windows("Packing", "packing", [["08:00", "11:00"], ["11:30", "17:30"]]),
    ...windows("Raw material store", "raw_materials", [["07:30", "07:50"], ["10:00", "10:20"], ["14:00", "14:25"]]),
    ...windows("Finished goods", "finished_goods", [["11:00", "11:30"], ["16:00", "16:40"]]),
    ...windows("Dispatch", "dispatch", [["16:30", "17:30"]]),
    ...windows("Dispatch", "dispatch", [["16:35", "17:20"]], "vehicle"),
    ...windows("Maintenance bay", "maintenance", [["13:00", "13:50"]]),
  ],
});

const retailCameras = [
  siteCamera("s1", 1, "Store entrance", "entrance"), siteCamera("s2", 2, "Sales floor", "sales_floor"),
  siteCamera("s3", 3, "Promotion stand", "promotional_zone"), siteCamera("s4", 4, "Checkout", "checkout"),
  siteCamera("s5", 5, "Stock room", "stock_room"),
];
const retailOverrides = siteTypeOverrides({
  type: "retail", name: "Gulberg Store", cameras: retailCameras, open: "10:00", close: "21:00", opening: "09:48", closing: "21:12",
  episodes: [
    ...hourly("Store entrance", "entrance", { 10: 3, 11: 4, 12: 6, 13: 7, 14: 5, 15: 5, 16: 6, 17: 9, 18: 12, 19: 10, 20: 6 }, 2),
    ...hourly("Sales floor", "sales_floor", { 10: 2, 11: 2, 12: 3, 13: 4, 14: 3, 15: 3, 16: 3, 17: 5, 18: 6, 19: 5, 20: 3 }, 9),
    ...hourly("Promotion stand", "promotional_zone", { 12: 1, 13: 2, 17: 2, 18: 3, 19: 1 }, 4),
    ...hourly("Checkout", "checkout", { 10: 1, 11: 2, 12: 4, 13: 4, 14: 3, 15: 3, 16: 4, 17: 6, 18: 8, 19: 7, 20: 4 }, 4),
    ...windows("Stock room", "stock_room", [["09:30", "09:50"], ["15:00", "15:15"]]),
  ],
});

async function installFixture(page, overrides = {}) {
  const fixture = { ...rpcData, ...overrides };
  await page.addInitScript(({ session, siteId, siteName }) => {
    localStorage.setItem("sb-visual-qa-auth-token", JSON.stringify(session));
    localStorage.setItem("watchlog:selected-site-id", siteId);
    localStorage.setItem("watchlog:selected-site-name", siteName);
  }, { session, siteId: SITE_ID, siteName: "Harbour Branch" });

  await page.route("https://fonts.googleapis.com/**", route => route.abort());
  await page.route("https://fonts.gstatic.com/**", route => route.abort());

  await page.route(`${SUPABASE}/**`, async route => {
    const req = route.request();
    const url = new URL(req.url());

    if (url.pathname.startsWith("/rest/v1/rpc/")) {
      const name = rpcName(url);
      const data = Object.prototype.hasOwnProperty.call(fixture, name) ? fixture[name] : null;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(data),
        headers: { "access-control-allow-origin": "*" },
      });
      return;
    }

    if (url.pathname === "/auth/v1/user") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(session.user) });
      return;
    }

    if (url.pathname.startsWith("/rest/v1/")) {
      const table = decodeURIComponent(url.pathname.slice("/rest/v1/".length)).split("/")[0];
      const data = Object.prototype.hasOwnProperty.call(tableData, table) ? tableData[table] : [];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
      return;
    }

    if (url.pathname.startsWith("/functions/v1/")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conversation_id: "visual-qa-conversation",
          answer: "Visual QA fixture answer.",
        }),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

const routes = [
  { slug: "home", path: `/home/?site=${SITE_ID}`, ready: "Needs attention" },
  { slug: "attention", path: `/notifications/?site=${SITE_ID}`, ready: "What needs your attention" },
  { slug: "insights", path: `/analytics/?site=${SITE_ID}`, ready: "Where activity came from" },
  { slug: "reports", path: `/reports/?view=yesterday&site=${SITE_ID}`, ready: "Recommended action" },
  { slug: "ask", path: `/ai/?site=${SITE_ID}`, ready: "Ask WatchLog about Harbour Branch" },
  { slug: "cameras", path: `/control-room/?site=${SITE_ID}`, ready: "Cameras & evidence at Harbour Branch" },
  { slug: "saved-video", path: `/archive/?site=${SITE_ID}`, ready: "Find earlier activity at Harbour Branch" },
  { slug: "setup", path: `/setup/?site=${SITE_ID}`, ready: "Set up Harbour Branch with WatchLog" },
  { slug: "settings", path: `/settings/?site=${SITE_ID}`, ready: "Account and sites" },
  { slug: "health", path: `/site-health/?site=${SITE_ID}`, ready: "Can WatchLog observe this site?" },
  { slug: "incidents", path: `/incidents/?site=${SITE_ID}`, ready: "Security review for" },
  { slug: "delivery", path: `/reports/delivery/?site=${SITE_ID}`, ready: "Report delivery" },
  { slug: "team", path: `/team/?site=${SITE_ID}`, ready: "Who has access" },
  { slug: "activity-rules", path: `/analytics/studio/?site=${SITE_ID}`, ready: "Activity Rules" },
  { slug: "camera-settings", path: `/site-control/?site=${SITE_ID}`, ready: "Camera channels" },
  { slug: "account", path: `/settings/account/?site=${SITE_ID}`, ready: "Account and plan" },
  { slug: "control-room-all-sites", path: `/control-room/advanced/?site=${SITE_ID}`, ready: "See what needs attention across every site." },
  { slug: "operations-report", path: `/control-room/reports/?site=${SITE_ID}`, ready: "operations report" },
  { slug: "evidence", path: `/incidents/evidence/?site=${SITE_ID}`, ready: "Camera evidence" },
];

// The restaurant scenario re-checks the surfaces whose semantics differ by site type.
const restaurantRoutes = [
  { slug: "home-restaurant", path: `/home/?site=${SITE_ID}`, ready: "Latest completed service day" },
  { slug: "insights-restaurant", path: `/analytics/?site=${SITE_ID}`, ready: "Service-day trend" },
  { slug: "reports-restaurant", path: `/reports/?view=yesterday&site=${SITE_ID}`, ready: "Demand pattern" },
  { slug: "reports-restaurant-week", path: `/reports/?view=week&site=${SITE_ID}`, ready: "Service-day trend" },
];
// System Health with coverage that really is complete: the only case where "No unverified period today"
// may appear.
const fullyVerifiedOverrides = {
  wl_ai_context: { ...context, coverage: { coverage_ratio: 1, classes: { live_seconds: 86400, recovered_seconds: 0, unverified_seconds: 0 } } },
};
const fullyVerifiedRoutes = [
  { slug: "health-fully-verified", path: `/site-health/?site=${SITE_ID}`, ready: "No unverified period today" },
];

// Multi-recorder visual truth fixture: two physical recorders both expose Channel 1.
// Recorder B is unavailable, so the UI should show one recorder root cause affecting its
// cameras while Recorder A remains available. Recent events intentionally share Channel 1
// and must stay attached to their canonical camera UUIDs.
const multiRecorderOverrides = {
  wl_ai_context: {
    ...context,
    facts_version: "watchlog-ai-context-v6",
    cameras: [
      { id: "c1", recorder_id: "rec-a", channel: 1, name: "Main entrance", monitor: true, health_state: "operational", recording_state: "recording", purpose: "entrance" },
      { id: "c2", recorder_id: "rec-a", channel: 2, name: "Service area", monitor: true, health_state: "operational", recording_state: "recording", purpose: "queue" },
      { id: "c3", recorder_id: "rec-b", channel: 1, name: "Rear access", monitor: true, health_state: "offline", recording_state: "unknown", purpose: "rear_access" },
      { id: "c4", recorder_id: "rec-b", channel: 2, name: "Storage corridor", monitor: true, health_state: "unknown", recording_state: "unknown", purpose: "storage" },
    ],
    faults: [
      { id: "recorder-fault", camera: "Rear access", reason: "nvr_unreachable", severity: "warning" },
    ],
    recent_events: [
      { event_id: "event-rec-a-ch1", camera_id: "c1", recorder_id: "rec-a", channel: 1, camera: "Main entrance", event_type: "person", device_ts: "2026-10-01T07:55:00Z", recovered: false },
      { event_id: "event-rec-b-ch1", camera_id: "c3", recorder_id: "rec-b", channel: 1, camera: "Rear access", event_type: "vehicle", device_ts: "2026-10-01T07:50:00Z", recovered: false },
    ],
  },
  wl_my_site_recorders: {
    enabled: true,
    site_id: SITE_ID,
    recorders: [
      { id: "rec-a", name: "Main building recorder", state: "healthy", issue: null, checked_at: "2026-10-01T07:58:00Z", camera_count: 2, camera_ids: ["c1","c2"] },
      { id: "rec-b", name: "Loading area recorder", state: "offline", issue: "connection", checked_at: "2026-10-01T07:57:00Z", camera_count: 2, camera_ids: ["c3","c4"] },
    ],
  },
};
const multiRecorderRoutes = [
  { slug: "health-multi-recorder", path: `/site-health/?site=${SITE_ID}`, ready: "Loading area recorder" },
  { slug: "cameras-multi-recorder", path: `/control-room/?site=${SITE_ID}`, ready: "Main building recorder" },
];
// Phase 28: each business site type renders its own operating story from the same governed day.
const siteTypeRoutes = (type, home, dayNoun, week = "A period comparison is not available for this site yet.") => [
  { slug: `home-${type}`, path: `/home/?site=${SITE_ID}`, ready: home },
  { slug: `insights-${type}`, path: `/analytics/?site=${SITE_ID}`, ready: `Latest completed ${dayNoun}` },
  { slug: `reports-${type}`, path: `/reports/?view=yesterday&site=${SITE_ID}`, ready: `${dayNoun[0].toUpperCase()}${dayNoun.slice(1)} activity` },
  { slug: `reports-${type}-week`, path: `/reports/?view=week&site=${SITE_ID}`, ready: week },
  { slug: `ask-${type}`, path: `/ai/?site=${SITE_ID}`, ready: "Ask WatchLog about" },
  { slug: `setup-${type}`, path: `/setup/?site=${SITE_ID}`, ready: "Set up" },
];
const warehouseRoutes = siteTypeRoutes("warehouse", "Dock and area comparison", "working day", "Area activity vs the previous period");
const factoryRoutes = siteTypeRoutes("factory", "Production areas", "shift day");
const retailRoutes = siteTypeRoutes("retail", "Checkout activity", "trading day");
const scenarios = [
  { name: "office", overrides: {}, routes },
  { name: "warehouse", overrides: warehouseOverrides, routes: warehouseRoutes },
  { name: "factory", overrides: factoryOverrides, routes: factoryRoutes },
  { name: "retail", overrides: retailOverrides, routes: retailRoutes },
  { name: "restaurant", overrides: restaurantOverrides, routes: restaurantRoutes },
  { name: "coverage-complete", overrides: fullyVerifiedOverrides, routes: fullyVerifiedRoutes },
  { name: "multi-recorder", overrides: multiRecorderOverrides, routes: multiRecorderRoutes },
];

const viewports = [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "mobile", width: 390, height: 844 },
];

await fs.rm(OUT, { recursive: true, force: true });
await fs.mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const manifest = [];

try {
  for (const scenario of scenarios) for (const viewport of viewports) {
    const contextBrowser = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      deviceScaleFactor: 1,
    });
    const page = await contextBrowser.newPage();
    // Freeze the clock at the fixture's "now" so time-relative views (last 24 hours) are deterministic.
    await page.clock.setFixedTime(new Date(now));
    const pageErrors = [];
    page.on("pageerror", error => pageErrors.push(String(error.message || error)));
    await installFixture(page, scenario.overrides);

    for (const route of scenario.routes) {
      const target = BASE + route.path;
      await page.goto(target, { waitUntil: "domcontentloaded" });
      await page.getByText(route.ready, { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
      await page.waitForTimeout(500);
      // Truth guard: a page must never claim no unverified period while also reporting unverified time.
      const bodyText = await page.locator("body").innerText();
      // An unverified AMOUNT ("58 min / 1h 05m / 4% of today could not be verified"), not the section heading.
      const unverifiedAmount = /\b\d+\s*(?:min|h\b[^\n]*?)\s*(?:\d+m\s*)?could not be verified|\b\d+% (?:of today )?could not be verified/i;
      if (/No unverified period today/i.test(bodyText) && unverifiedAmount.test(bodyText)) {
        pageErrors.push("contradictory coverage: 'No unverified period today' shown alongside unverified time");
      }
      const filename = `${route.slug}-${viewport.name}.png`;
      await page.screenshot({ path: path.join(OUT, filename), fullPage: true });
      manifest.push({
        route: route.slug,
        viewport: viewport.name,
        url: target,
        screenshot: filename,
        pageErrors: [...pageErrors],
      });
      pageErrors.length = 0;
    }

    await contextBrowser.close();
  }
} finally {
  await browser.close();
}

await fs.writeFile(path.join(OUT, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
const uncaught = manifest.flatMap(x => x.pageErrors.map(error => `${x.route}/${x.viewport}: ${error}`));
if (uncaught.length) {
  console.error("Visual QA page errors:\n" + uncaught.join("\n"));
  process.exitCode = 1;
} else {
  console.log(`Visual QA captured ${manifest.length} screenshots across ${scenarios.reduce((n, x) => n + x.routes.length, 0)} owner routes with no uncaught page errors.`);
}
