"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { Nav, requireTenant } from "../../shell";
import ui from "../../portal.module.css";

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

function ago(ts) {
  if (!ts) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function human(value) {
  return String(value || "Custom")
    .replace(/^analytic_/, "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function activityPill(state) {
  const cls = state === "active" ? "s-ok" : state === "silent" ? "s-warn" : "s-unk";
  const label = state === "active" ? "recent activity" : state === "silent" ? "quiet 24h+" : state === "never" ? "not seen" : "not verified";
  return <span className={`pill ${cls}`}>{label}</span>;
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
      const signals = scopedRules.filter((item) => item.site === site.name).reduce((total, item) => total + Number(item.count || 0), 0);
      return { id: site.id, name: site.name, cameras: siteCameras.length, online, connections: siteAgents.length, quiet, signals };
    });
    return { scopedRules, analyticsSignals, cameras, siteRows };
  }, [analytics, health, overview, selectedCamera, selectedSite, sites]);

  const summary = analytics?.summary || {};
  const title = selectedCamera
    ? `${selectedCamera.name || `Camera ${selectedCamera.channel}`} report`
    : selectedSite
      ? `${selectedSite.name} report`
      : "Collective operations report";
  const scopeCopy = selectedCamera
    ? "Camera-wise analytics signals and current camera health. Directional or occupancy totals are shown only when they can be supported by the scoped aggregate."
    : selectedSite
      ? "Site-level operational health and configured analytics for one site."
      : "A multi-site view of operational health and configured analytics across the account.";

  return <div className="shell">
    <Nav active="Control Room" email={email} />
    <main className="main">
      <header className={ui.pageHead}>
        <div>
          <div className={ui.eyebrow}>Control Room Reporting</div>
          <h1>{title}</h1>
          <p>{scopeCopy}</p>
        </div>
        <div className={ui.headActions}>
          <a className={ui.secondaryLink} href="/control-room/">Back to Control Room</a>
          <button className="secondary" onClick={() => window.print()}>Print / save PDF</button>
        </div>
      </header>

      {error && <div className="err">{error}</div>}

      <section className={ui.card} style={{ marginBottom: 18 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(160px,1fr)) auto", gap: 12, alignItems: "end" }}>
          <label><span className="muted" style={{ display: "block", fontSize: 11, marginBottom: 6 }}>WINDOW</span><select value={days} onChange={(event) => setDays(Number(event.target.value))}>{WINDOWS.map((windowDays) => <option key={windowDays} value={windowDays}>Last {windowDays} day{windowDays === 1 ? "" : "s"}</option>)}</select></label>
          <label><span className="muted" style={{ display: "block", fontSize: 11, marginBottom: 6 }}>SITE</span><select value={siteId} onChange={(event) => { setSiteId(event.target.value); setCameraId(""); }}><option value="">All sites</option>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
          <label><span className="muted" style={{ display: "block", fontSize: 11, marginBottom: 6 }}>CAMERA</span><select value={cameraId} onChange={(event) => setCameraId(event.target.value)}><option value="">All cameras in scope</option>{availableCameras.map((camera) => <option key={camera.id} value={camera.id}>{camera.siteName ? `${camera.siteName} · ` : ""}{camera.name || `Camera ${camera.channel}`}</option>)}</select></label>
          <button className="secondary" onClick={load}>Refresh</button>
        </div>
        <div className="muted" style={{ fontSize: 11, marginTop: 10 }}>Generated from your WatchLog data · refreshed {stamp || "when data loads"}. This is an operational report, not a certified safety or transaction record.</div>
      </section>

      {!overview || !analytics ? <div className="panel"><div className="empty">Loading report...</div></div> : <>
        <section className={ui.metricGrid} aria-label="Report summary">
          <div className={ui.metric}><div className={ui.metricValue}>{selectedSite ? 1 : model.siteRows.length}</div><div className={ui.metricLabel}>Sites in scope</div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{selectedCamera ? 1 : model.cameras.length}</div><div className={ui.metricLabel}>Cameras in scope</div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{model.cameras.reduce((total, camera) => total + camera.configured, 0)}</div><div className={ui.metricLabel}>Configured analytics</div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{number(model.analyticsSignals)}</div><div className={ui.metricLabel}>Analytics signals</div></div>
        </section>

        {!selectedCamera && <>
          <div className={ui.sectionHead}><div><h2>Activity summary</h2><p>Totals from your configured analytics for this scope and time window.</p></div></div>
          <section className={ui.metricGrid} aria-label="Activity summary">
            <div className={ui.metric}><div className={ui.metricValue}>{number(summary.visitor_in)}</div><div className={ui.metricLabel}>Visitor entries</div></div>
            <div className={ui.metric}><div className={ui.metricValue}>{number(summary.visitor_out)}</div><div className={ui.metricLabel}>Visitor exits</div></div>
            <div className={ui.metric}><div className={ui.metricValue}>{number(summary.checkout_peak)}</div><div className={ui.metricLabel}>Area occupancy peak</div></div>
            <div className={ui.metric}><div className={ui.metricValue}>{number(summary.after_hours)}</div><div className={ui.metricLabel}>After-hours signals</div></div>
          </section>
          <div className={ui.callout}><span className={ui.statusDot}/><div><strong>Occupancy values are people presence, not sales.</strong><p>WatchLog does not infer completed transactions or point-of-sale totals from CCTV alone.</p></div></div>
        </>}

        <div className={ui.sectionHead}><div><h2>{selectedSite ? "Site operations" : "Site comparison"}</h2><p>Current site connection/camera health alongside analytics activity in the selected report window.</p></div></div>
        <div className="panel"><div className={ui.tableWrap}><table><thead><tr><th>Site</th><th>Connections online</th><th>Cameras</th><th>Camera attention</th><th>Analytics signals</th></tr></thead><tbody>{model.siteRows.map((site) => <tr key={site.id}><td><b>{site.name}</b></td><td>{site.online} / {site.connections}</td><td>{site.cameras}</td><td><span className={`pill ${site.quiet ? "s-warn" : "s-ok"}`}>{site.quiet ? `${site.quiet} quiet` : "clear"}</span></td><td className="mono">{number(site.signals)}</td></tr>)}</tbody></table></div></div>

        <div className={ui.sectionHead}><div><h2>{selectedCamera ? "Camera detail" : "Camera-wise report"}</h2><p>Purpose, current activity state, last reported activity and analytics volume by camera.</p></div></div>
        <div className="panel"><div className={ui.tableWrap}>{model.cameras.length ? <table><thead><tr><th>Site</th><th>Camera</th><th>Purpose</th><th>Health</th><th>Last activity</th><th>Configured</th><th>Signals</th></tr></thead><tbody>{model.cameras.map((camera) => <tr key={camera.id}><td>{camera.siteName}</td><td><b>{camera.name || `Camera ${camera.channel}`}</b></td><td>{human(camera.purpose || "custom")}</td><td>{activityPill(camera.health?.activity_state)}</td><td>{ago(camera.health?.last_activity_at)}</td><td>{camera.configured}</td><td className="mono">{number(camera.signals)}</td></tr>)}</tbody></table> : <div className="empty">No cameras are available in this report scope.</div>}</div></div>

        <div className={ui.sectionHead}><div><h2>Analytics breakdown</h2><p>Configured measurements ranked by activity. These are analytics events, not transactions.</p></div></div>
        <section className={ui.card}>{model.scopedRules.length ? <div className={ui.splitList}>{model.scopedRules.slice(0, 20).map((item) => <div className={ui.listRow} key={`${item.rule_id}-${item.site}-${item.camera}`}><span className="pill s-ok">{number(item.count)}</span><div><strong>{item.name || GOAL_LABEL[item.analytic_key] || human(item.rule_type)}</strong><small>{[item.site, item.camera, GOAL_LABEL[item.analytic_key]].filter(Boolean).join(" · ")}</small></div></div>)}</div> : <div className={ui.emptyCard}>No analytics measurements were received for this scope in the selected window.</div>}</section>
      </>}
    </main>
  </div>;
}
