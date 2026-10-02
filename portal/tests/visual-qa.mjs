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
  recorder: { identified: true, vendor: "Hikvision", model: "NVR" },
  capability_known: true,
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
    action_items: [],
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

const rpcData = {
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
  wl_my_daily_intelligence: { coverage: { coverage_ratio: 0.96 } },
  wl_analytics_overview: analytics,
  wl_restaurant_site_config: { enabled: false },
  wl_my_site_context: null,
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

async function installFixture(page) {
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
      const data = Object.prototype.hasOwnProperty.call(rpcData, name) ? rpcData[name] : null;
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
  { slug: "home", path: `/home/?site=${SITE_ID}`, ready: "What matters now, what needs attention" },
  { slug: "attention", path: `/notifications/?site=${SITE_ID}`, ready: "What needs your attention" },
  { slug: "insights", path: `/analytics/?site=${SITE_ID}`, ready: "What is happening at Harbour Branch?" },
  { slug: "reports", path: `/reports/?view=yesterday&site=${SITE_ID}`, ready: "Management report" },
  { slug: "ask", path: `/ai/?site=${SITE_ID}`, ready: "Ask WatchLog about Harbour Branch" },
  { slug: "cameras", path: `/control-room/?site=${SITE_ID}`, ready: "See the site by business area" },
  { slug: "saved-video", path: `/archive/?site=${SITE_ID}`, ready: "Find earlier activity at Harbour Branch" },
  { slug: "setup", path: `/setup/?site=${SITE_ID}`, ready: "Set up Harbour Branch with WatchLog" },
  { slug: "settings", path: `/settings/?site=${SITE_ID}`, ready: "Account and sites" },
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
  for (const viewport of viewports) {
    const contextBrowser = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      deviceScaleFactor: 1,
    });
    const page = await contextBrowser.newPage();
    const pageErrors = [];
    page.on("pageerror", error => pageErrors.push(String(error.message || error)));
    await installFixture(page);

    for (const route of routes) {
      const target = BASE + route.path;
      await page.goto(target, { waitUntil: "domcontentloaded" });
      await page.getByText(route.ready, { exact: false }).first().waitFor({ state: "visible", timeout: 15000 });
      await page.waitForTimeout(500);
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
  console.log(`Visual QA captured ${manifest.length} screenshots across ${routes.length} owner routes with no uncaught page errors.`);
}
