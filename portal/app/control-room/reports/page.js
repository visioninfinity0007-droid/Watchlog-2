"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { requireTenant } from "../../shell";
import { OwnerPage, Lead, Section, Metrics, Status, RailSection, Stat, Summary, HBars, AskLinks, Empty, Loading, Notice } from "../../owner/ui";

const WINDOWS = [1, 7, 30];
const GOAL_LABEL = {
  visitor_flow: "Visitor Flow",
  vehicle_flow: "Vehicle Flow",
  boundary_monitoring: "Boundary Monitoring",
  zone_activity: "Zone Activity",
  dwell: "Dwell / Time in Zone",
  checkout_activity: "Checkout Activity",
  after_hours: "After-Hours Activity",
};

function number(value) {
  return Number(value || 0).toLocaleString();
}

// Unknown stays unknown: a missing aggregate is "Not available", never 0.
function known(value) {
  return value === null || value === undefined || value === "" ? null : number(value);
}

function windowLabel(windowDays) {
  return `Last ${windowDays} day${windowDays === 1 ? "" : "s"}`;
}

function ago(ts) {
  if (!ts) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function lastActivity(camera) {
  if (!camera.health) return "Not reported";
  return camera.health.last_activity_at ? ago(camera.health.last_activity_at) : "No activity seen";
}

function human(value) {
  return String(value || "Custom")
    .replace(/^analytic_/, "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function activityStatus(state) {
  if (state === "active") return <Status tone="ok">Recent activity</Status>;
  if (state === "silent") return <Status tone="warn">Quiet 24h+</Status>;
  if (state === "never") return <Status tone="unknown">Not seen</Status>;
  return <Status tone="unknown">Not verified</Status>;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

export default function ControlRoomReports() {
  const [email, setEmail] = useState("");
  const [days, setDays] = useState(7);
  const [siteId, setSiteId] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [studio, setStudio] = useState(null);
  const [overview, setOverview] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [health, setHealth] = useState({ cameras: [], faults: [] });
  const [error, setError] = useState("");
  const [stamp, setStamp] = useState("");

  const load = useCallback(async () => {
    const guard = await requireTenant();
    if (!guard) return;
    setEmail(guard.session.user.email || "");
    const sb = supabase();
    const [studioResponse, overviewResponse, healthResponse] = await Promise.all([
      sb.rpc("wl_analytics_studio"),
      sb.rpc("wl_portal_overview", { p_days: days }),
      sb.rpc("wl_site_health_details", { p_days: 1 }),
    ]);
    if (studioResponse.error || overviewResponse.error || healthResponse.error) {
      setError(say(studioResponse.error || overviewResponse.error || healthResponse.error));
      return;
    }
    const sites = studioResponse.data?.sites || [];
    const selectedSite = siteId ? sites.find((site) => site.id === siteId) : null;
    const analyticsResponse = await sb.rpc("wl_analytics_overview", {
      p_days: days,
      p_site_id: selectedSite?.id || null,
    });
    if (analyticsResponse.error) {
      setError(say(analyticsResponse.error));
      return;
    }
    setStudio(studioResponse.data || { sites: [] });
    setOverview(overviewResponse.data || {});
    setHealth(healthResponse.data || { cameras: [], faults: [] });
    setAnalytics(analyticsResponse.data || { summary: {}, by_rule: [], daily: [] });
    setError("");
    setStamp(new Date().toLocaleString());
  }, [days, siteId]);

  useEffect(() => { load(); }, [load]);

  const sites = studio?.sites || [];
  const selectedSite = siteId ? sites.find((site) => site.id === siteId) || null : null;
  const availableCameras = selectedSite ? selectedSite.cameras || [] : sites.flatMap((site) => (site.cameras || []).map((camera) => ({ ...camera, siteName: site.name, siteId: site.id })));
  const selectedCamera = cameraId ? availableCameras.find((camera) => camera.id === cameraId) || null : null;

  useEffect(() => {
    if (cameraId && !availableCameras.some((camera) => camera.id === cameraId)) setCameraId("");
  }, [availableCameras, cameraId]);

  const model = useMemo(() => {
    const byRule = analytics?.by_rule || [];
    const selectedCameraSite = selectedSite?.name || selectedCamera?.siteName || "";
    const scopedRules = selectedCamera
      ? byRule.filter((item) => item.camera === selectedCamera.name && item.site === selectedCameraSite)
      : byRule;
    const analyticsSignals = scopedRules.reduce((total, item) => total + Number(item.count || 0), 0);
    const scopedSites = selectedSite ? [selectedSite] : sites;
    const scopedCameraInventory = selectedCamera
      ? scopedSites.flatMap((site) => (site.cameras || []).filter((camera) => camera.id === selectedCamera.id).map((camera) => ({ ...camera, siteName: site.name })))
      : scopedSites.flatMap((site) => (site.cameras || []).map((camera) => ({ ...camera, siteName: site.name })));
    const healthById = new Map((health?.cameras || []).map((camera) => [camera.id, camera]));
    const healthByName = new Map((health?.cameras || []).map((camera) => [`${camera.site}::${camera.camera}`, camera]));
    const cameras = scopedCameraInventory.map((camera) => ({
      ...camera,
      health: healthById.get(camera.id) || healthByName.get(`${camera.siteName}::${camera.name}`) || null,
      signals: scopedRules.filter((item) => item.camera === camera.name && item.site === camera.siteName).reduce((total, item) => total + Number(item.count || 0), 0),
      configured: (camera.rules || []).filter((rule) => rule.enabled).length,
    }));
    const agents = overview?.agents || [];
    const siteRows = scopedSites.map((site) => {
      const siteAgents = agents.filter((agent) => agent.site === site.name);
      const online = siteAgents.filter((agent) => {
        if (!agent.last_seen_at) return false;
        return (Date.now() - Date.parse(agent.last_seen_at)) / 1000 < 180;
      }).length;
      const siteCameras = cameras.filter((camera) => camera.siteName === site.name);
      const quiet = siteCameras.filter((camera) => camera.health?.activity_state === "silent" || camera.health?.activity_state === "never").length;
      const unverified = siteCameras.filter((camera) => !["active", "silent", "never"].includes(camera.health?.activity_state)).length;
      const signals = scopedRules.filter((item) => item.site === site.name).reduce((total, item) => total + Number(item.count || 0), 0);
      return { id: site.id, name: site.name, cameras: siteCameras.length, online, connections: siteAgents.length, quiet, unverified, signals };
    });
    return { scopedRules, analyticsSignals, cameras, siteRows };
  }, [analytics, health, overview, selectedCamera, selectedSite, sites]);

  const summary = analytics?.summary || {};
  const title = selectedCamera
    ? `${selectedCamera.name || `Camera ${selectedCamera.channel}`} report`
    : selectedSite
      ? `${selectedSite.name} report`
      : "Collective operations report";
  const ready = Boolean(overview && analytics);

  // Decision first: what in this scope needs a look, before the supporting tables.
  const quietCameras = model.cameras.filter((camera) => camera.health?.activity_state === "silent" || camera.health?.activity_state === "never").length;
  const unverifiedCameras = model.cameras.filter((camera) => !["active", "silent", "never"].includes(camera.health?.activity_state)).length;
  const offlineSites = model.siteRows.filter((site) => site.connections > 0 && site.online < site.connections).length;
  const unconnectedSites = model.siteRows.filter((site) => site.connections === 0).length;
  const configuredTotal = model.cameras.reduce((total, camera) => total + camera.configured, 0);
  let lead;
  if (!model.cameras.length) {
    lead = { tone: "unknown", title: "No cameras are available in this report scope.", body: "" };
  } else if (quietCameras || offlineSites) {
    const parts = [];
    if (quietCameras) parts.push(plural(quietCameras, "camera quiet or not seen", "cameras quiet or not seen"));
    if (offlineSites) parts.push(plural(offlineSites, "site connection offline", "site connections offline"));
    lead = { tone: "warn", title: parts.join(" · "), body: `${number(model.analyticsSignals)} analytics signals in the ${windowLabel(days).toLowerCase()}.` };
  } else if (unverifiedCameras) {
    lead = { tone: "unknown", title: `${plural(unverifiedCameras, "camera", "cameras")} could not be verified`, body: "Activity for these cameras was not reported in the last day." };
  } else if (unconnectedSites) {
    lead = { tone: "unknown", title: `${plural(unconnectedSites, "site has", "sites have")} no site connection reported`, body: "Camera activity is shown, but connection status could not be verified." };
  } else {
    lead = { tone: "ok", title: "Every camera in scope reported recent activity", body: `${number(model.analyticsSignals)} analytics signals in the ${windowLabel(days).toLowerCase()}.` };
  }

  const siteStatus = (site) => {
    if (site.quiet) return <Status tone="warn">{site.quiet} quiet</Status>;
    if (!site.cameras) return <Status tone="unknown">No cameras</Status>;
    if (site.unverified) return <Status tone="unknown">{site.unverified} not verified</Status>;
    return <Status tone="ok">Clear</Status>;
  };
  const connectionStatus = (site) => {
    if (!site.connections) return <Status tone="unknown">Not reported</Status>;
    return <Status tone={site.online === site.connections ? "ok" : "warn"}>{site.online} / {site.connections} online</Status>;
  };

  const rail = ready ? <>
    <RailSection label="Report scope">
      <Stat label="Window" value={windowLabel(days)} />
      <Stat label="Site" value={selectedSite?.name || "All sites"} />
      <Stat label="Camera" value={selectedCamera ? (selectedCamera.name || `Camera ${selectedCamera.channel}`) : "All cameras"} />
      <Stat label="Refreshed" value={stamp || null} />
    </RailSection>
    <RailSection label="Camera attention">
      <Stat label="Quiet or not seen" value={String(quietCameras)} />
      <Stat label="Not verified" value={String(unverifiedCameras)} />
      <Stat label="Connections offline" value={String(offlineSites)} />
    </RailSection>
    <RailSection label="Ask WatchLog">
      <AskLinks siteId={siteId} prompts={["Which cameras need attention in this report, and why?", "What changed in activity across my sites this period?"]} />
    </RailSection>
  </> : null;

  return <OwnerPage active="Control Room" email={email} siteId={siteId}
    kicker={["Cameras & Evidence", windowLabel(days)]}
    title={title}
    actions={<>
      <a className="ow-btn quiet" href="/control-room/">Back to Control Room</a>
      <button type="button" className="ow-btn quiet" onClick={() => window.print()}>Print / save PDF</button>
    </>}
    rail={rail}
    summary={ready ? <Summary items={[
      { value: String(selectedSite ? 1 : model.siteRows.length), label: "Sites in scope" },
      { value: String(quietCameras), label: "Cameras quiet or not seen" },
      { value: number(model.analyticsSignals), label: "Analytics signals" },
    ]} /> : null}>
    {error && <Notice tone="bad">{error}</Notice>}

    <div className="ow-tabs" role="tablist" aria-label="Report window">
      {WINDOWS.map((windowDays) => <button key={windowDays} type="button" role="tab" aria-selected={days === windowDays} className="ow-tab" onClick={() => setDays(windowDays)}>{windowLabel(windowDays)}</button>)}
    </div>
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: 12, marginBottom: 20 }}>
      <label className="ow-field" style={{ flex: "1 1 200px" }}>Site<select value={siteId} onChange={(event) => { setSiteId(event.target.value); setCameraId(""); }}><option value="">All sites</option>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
      <label className="ow-field" style={{ flex: "1 1 240px" }}>Camera<select value={cameraId} onChange={(event) => setCameraId(event.target.value)}><option value="">All cameras in scope</option>{availableCameras.map((camera) => <option key={camera.id} value={camera.id}>{camera.siteName ? `${camera.siteName} · ` : ""}{camera.name || `Camera ${camera.channel}`}</option>)}</select></label>
      <button type="button" className="ow-btn quiet" onClick={load}>Refresh</button>
    </div>

    {!ready ? <Loading label="Loading report" /> : <>
      <Lead tone={lead.tone} title={lead.title} body={lead.body || undefined} />

      <Metrics items={[
        { label: "Sites in scope", value: String(selectedSite ? 1 : model.siteRows.length) },
        { label: "Cameras in scope", value: String(selectedCamera ? 1 : model.cameras.length) },
        { label: "Configured analytics", value: String(configuredTotal) },
        { label: "Analytics signals", value: number(model.analyticsSignals), primary: true },
      ]} />

      <Section title={selectedSite ? "Site operations" : "Site comparison"} count={model.siteRows.length || null} note="Current site connection and camera health, with analytics activity for the window.">
        {model.siteRows.length ? <table className="ow-table"><thead><tr><th>Site</th><th>Connections</th><th>Cameras</th><th>Camera attention</th><th>Analytics signals</th></tr></thead><tbody>{model.siteRows.map((site) => <tr key={site.id}><td><b>{site.name}</b></td><td>{connectionStatus(site)}</td><td className="ow-mono">{site.cameras}</td><td>{siteStatus(site)}</td><td className="ow-mono">{number(site.signals)}</td></tr>)}</tbody></table> : <Empty title="No sites are available in this report scope." />}
      </Section>

      <Section title={selectedCamera ? "Camera detail" : "Camera-wise report"} count={model.cameras.length || null} note="Purpose, current activity and analytics volume by camera.">
        {model.cameras.length ? <table className="ow-table"><thead><tr><th>Camera</th><th>Purpose</th><th>Health</th><th>Last activity</th><th>Configured</th><th>Signals</th></tr></thead><tbody>{model.cameras.map((camera) => <tr key={camera.id}><td><b>{camera.name || `Camera ${camera.channel}`}</b><small>{camera.siteName}</small></td><td>{camera.purpose ? human(camera.purpose) : <span className="ow-muted">Purpose not set</span>}</td><td>{activityStatus(camera.health?.activity_state)}</td><td className="ow-muted">{lastActivity(camera)}</td><td className="ow-mono">{camera.configured}</td><td className="ow-mono">{number(camera.signals)}</td></tr>)}</tbody></table> : <Empty title="No cameras are available in this report scope." />}
      </Section>

      {!selectedCamera && <Section title="Activity summary" note="Totals from your configured analytics for this scope and window.">
        <Metrics items={[
          { label: "Visitor entries", value: known(summary.visitor_in) },
          { label: "Visitor exits", value: known(summary.visitor_out) },
          { label: "Area occupancy peak", value: known(summary.checkout_peak) },
          { label: "After-hours signals", value: known(summary.after_hours) },
        ]} />
        <div style={{ marginTop: 12 }}><Notice><span><strong>Occupancy values are people presence, not sales.</strong> WatchLog does not infer completed transactions or point-of-sale totals from CCTV alone.</span></Notice></div>
      </Section>}

      <Section title="Analytics breakdown" count={model.scopedRules.length || null} note="Configured measurements ranked by activity. These are analytics events, not transactions.">
        {model.scopedRules.length ? <HBars items={model.scopedRules.slice(0, 20).map((item) => ({
          key: `${item.rule_id}-${item.site}-${item.camera}`,
          label: item.name || GOAL_LABEL[item.analytic_key] || human(item.rule_type),
          note: [item.site, item.camera, GOAL_LABEL[item.analytic_key]].filter(Boolean).join(" · "),
          value: Number(item.count || 0),
        }))} /> : <Empty title="No analytics measurements were received for this scope in the selected window." />}
      </Section>

      <p className="ow-muted" style={{ marginTop: 24, fontSize: 12 }}>Generated from your WatchLog data · refreshed {stamp || "when data loads"}. This is an operational report, not a certified safety or transaction record.</p>
    </>}
  </OwnerPage>;
}
