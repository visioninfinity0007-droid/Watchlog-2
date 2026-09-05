"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import ui from "../portal.module.css";

const REFRESH_MS = 10000;
const GOAL_LABEL = {
  visitor_flow: "Visitor Flow",
  vehicle_flow: "Vehicle Flow",
  boundary_monitoring: "Boundary Monitoring",
  zone_activity: "Zone Activity",
  dwell: "Dwell / Time in Zone",
  checkout_activity: "Checkout Activity",
  after_hours: "After-Hours Activity",
};
const QSR_CAMERA_ROLES = [
  "Entrance / exit",
  "Queue / order area",
  "Checkout / collection area",
  "Kitchen / service boundary",
  "Loading / service entrance",
  "Parking / perimeter",
];

function liveness(lastSeen) {
  if (!lastSeen) return ["s-unk", "not connected"];
  const age = (Date.now() - Date.parse(lastSeen)) / 1000;
  if (age < 180) return ["s-ok", "online"];
  if (age < 1800) return ["s-warn", "needs attention"];
  return ["s-bad", "offline"];
}

function ago(ts) {
  if (!ts) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function humanType(value) {
  return String(value || "event")
    .replace(/^analytic_/, "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function eventSite(event) {
  return event?.site || event?.site_name || event?.location || "";
}

function matchSite(item, selectedSite) {
  if (selectedSite === "all") return true;
  const site = item?.site || item?.site_name || item?.location || "";
  return site === selectedSite;
}

function number(value) {
  return Number(value || 0).toLocaleString();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function missingRpc(error, name) {
  return !!error && new RegExp(`${name}|schema cache|function`, "i").test(error.message || "");
}

export default function ControlRoom() {
  const [data, setData] = useState(null);
  const [studio, setStudio] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [healthDetails, setHealthDetails] = useState({ cameras: [], faults: [] });
  const [layouts, setLayouts] = useState({ items: [], can_manage: false });
  const [layoutsAvailable, setLayoutsAvailable] = useState(true);
  const [layoutHydrated, setLayoutHydrated] = useState(false);
  const [selectedLayoutId, setSelectedLayoutId] = useState("");
  const [layoutName, setLayoutName] = useState("Operations view");
  const [gridSize, setGridSize] = useState(2);
  const [layoutCameraIds, setLayoutCameraIds] = useState(["", "", "", ""]);
  const [layoutBusy, setLayoutBusy] = useState(false);
  const [layoutNote, setLayoutNote] = useState("");
  const [layoutError, setLayoutError] = useState("");
  const [cameraShots, setCameraShots] = useState({});
  const [error, setError] = useState("");
  const [analyticsError, setAnalyticsError] = useState("");
  const [email, setEmail] = useState("");
  const [stamp, setStamp] = useState("");
  const [selectedSite, setSelectedSite] = useState("all");
  const [shots, setShots] = useState({});
  const [isFullscreen, setIsFullscreen] = useState(false);
  const cameraShotLoads = useRef(new Set());
  const operationsRef = useRef(null);

  const load = useCallback(async () => {
    const guard = await requireTenant();
    if (!guard) return;
    setEmail(guard.session.user.email || "");
    const sb = supabase();
    const [overviewResponse, studioResponse, healthResponse, layoutResponse] = await Promise.all([
      sb.rpc("wl_portal_overview", { p_days: 1 }),
      sb.rpc("wl_analytics_studio"),
      sb.rpc("wl_site_health_details", { p_days: 1 }),
      sb.rpc("wl_control_room_layouts"),
    ]);

    const primaryError = overviewResponse.error || studioResponse.error || healthResponse.error;
    if (primaryError) {
      setError(say(primaryError));
      return;
    }
    if (!overviewResponse.data?.tenant) {
      location.replace("/onboarding/");
      return;
    }

    if (layoutResponse.error) {
      if (missingRpc(layoutResponse.error, "wl_control_room_layouts")) {
        setLayoutsAvailable(false);
        setLayouts({ items: [], can_manage: studioResponse.data?.can_manage !== false });
        setLayoutError("");
      } else {
        setLayoutsAvailable(true);
        setLayoutError(say(layoutResponse.error));
      }
    } else {
      setLayoutsAvailable(true);
      setLayouts(layoutResponse.data || { items: [], can_manage: false });
      setLayoutError("");
    }

    const sites = studioResponse.data?.sites || [];
    const selected = selectedSite === "all" ? null : sites.find((site) => site.name === selectedSite);
    let analyticsResult = { summary: {}, by_rule: [], daily: [] };
    let analyticsRpcError = null;
    if (selectedSite === "all" || selected?.id) {
      const response = await sb.rpc("wl_analytics_overview", {
        p_days: 1,
        p_site_id: selected?.id || null,
      });
      analyticsResult = response.data || analyticsResult;
      analyticsRpcError = response.error;
    }

    setData(overviewResponse.data);
    setStudio(studioResponse.data || { sites: [] });
    setHealthDetails(healthResponse.data || { cameras: [], faults: [] });
    setAnalytics(analyticsResult);
    setError("");
    setAnalyticsError(analyticsRpcError ? say(analyticsRpcError) : "");
    setStamp(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  }, [selectedSite]);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const onFullscreen = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFullscreen);
    return () => document.removeEventListener("fullscreenchange", onFullscreen);
  }, []);

  const model = useMemo(() => {
    const agents = data?.agents || [];
    const health = data?.health || {};
    const recent = data?.recent || [];
    const offlineAgents = health.offline_agents || [];
    const silentCameras = health.silent_cameras || [];
    const faults24 = health.faults_24h || [];

    const names = new Set();
    agents.forEach((agent) => agent?.site && names.add(agent.site));
    offlineAgents.forEach((agent) => agent?.site && names.add(agent.site));
    silentCameras.forEach((camera) => camera?.site && names.add(camera.site));
    faults24.forEach((fault) => fault?.site && names.add(fault.site));
    recent.forEach((event) => eventSite(event) && names.add(eventSite(event)));
    const sites = [...names].sort((a, b) => a.localeCompare(b));

    const filteredAgents = selectedSite === "all" ? agents : agents.filter((agent) => agent.site === selectedSite);
    const filteredOffline = offlineAgents.filter((item) => matchSite(item, selectedSite));
    const filteredSilent = silentCameras.filter((item) => matchSite(item, selectedSite));
    const filteredFaults = faults24.filter((item) => matchSite(item, selectedSite));
    const filteredRecent = selectedSite === "all" ? recent : recent.filter((event) => eventSite(event) === selectedSite);
    const onlineConnections = filteredAgents.filter((agent) => liveness(agent.last_seen_at)[0] === "s-ok").length;

    const queue = [
      ...filteredOffline.map((item) => ({
        key: `offline-${item.agent_id || item.site || "connection"}`,
        severity: "critical",
        title: `${item.site || "Site"} connection offline`,
        detail: item.hostname ? `${item.hostname} has stopped reporting.` : "The WatchLog connection has stopped reporting.",
        when: item.last_seen_at,
        href: "/site-health/",
      })),
      ...filteredSilent.map((item) => ({
        key: `silent-${item.camera_id || item.camera || item.site || "camera"}`,
        severity: "attention",
        title: `${item.camera || "Camera"} is quiet${item.site ? ` at ${item.site}` : ""}`,
        detail: "No recent camera activity has been observed for this configured source.",
        when: item.last_seen_at || item.last_event_at,
        href: "/site-health/",
      })),
      ...filteredFaults.map((item) => ({
        key: `fault-${item.event_id || item.camera || item.site || "fault"}`,
        severity: "attention",
        title: `${item.camera || "Camera system"} fault${item.site ? ` at ${item.site}` : ""}`,
        detail: humanType(item.event_type || item.type || "camera system fault"),
        when: item.device_ts || item.created_at,
        href: "/site-health/",
      })),
    ].slice(0, 12);

    const rowSites = selectedSite === "all" ? sites : sites.filter((site) => site === selectedSite);
    const siteRows = rowSites.map((site) => {
      const list = agents.filter((agent) => agent.site === site);
      const online = list.filter((agent) => liveness(agent.last_seen_at)[0] === "s-ok").length;
      const silent = silentCameras.filter((camera) => camera.site === site).length;
      const faults = faults24.filter((fault) => fault.site === site).length;
      const recentCount = recent.filter((event) => eventSite(event) === site).length;
      return { site, connections: list.length, online, silent, faults, recentCount };
    });

    return {
      agents: filteredAgents,
      recent: filteredRecent,
      allRecent: recent,
      sites,
      queue,
      siteRows,
      onlineConnections,
      silentCount: filteredSilent.length,
    };
  }, [data, selectedSite]);

  const cameraInventory = useMemo(() => {
    const healthById = new Map((healthDetails?.cameras || []).map((camera) => [camera.id, camera]));
    const healthByName = new Map((healthDetails?.cameras || []).map((camera) => [`${camera.site}::${camera.camera}`, camera]));
    const cameras = [];
    for (const site of studio?.sites || []) {
      for (const camera of site.cameras || []) {
        const health = healthById.get(camera.id) || healthByName.get(`${site.name}::${camera.name}`) || null;
        const recentCount = model.allRecent.filter((event) => eventSite(event) === site.name && event.camera === camera.name).length;
        cameras.push({ ...camera, siteName: site.name, siteId: site.id, health, recentCount });
      }
    }
    return cameras;
  }, [healthDetails, model.allRecent, studio]);

  const camerasBySite = useMemo(() => {
    const grouped = new Map();
    for (const camera of cameraInventory) {
      if (!grouped.has(camera.siteName)) grouped.set(camera.siteName, []);
      grouped.get(camera.siteName).push(camera);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [cameraInventory]);

  const analyticsModel = useMemo(() => {
    const sites = studio?.sites || [];
    const scopedSites = selectedSite === "all" ? sites : sites.filter((site) => site.name === selectedSite);
    const configuredRules = scopedSites.reduce((total, site) => total + (site.cameras || []).reduce((cameraTotal, camera) => cameraTotal + (camera.rules || []).filter((rule) => rule.enabled).length, 0), 0);
    return { configuredRules, summary: analytics?.summary || {}, active: analytics?.by_rule || [] };
  }, [analytics, selectedSite, studio]);

  function normalizedSlots(ids, size) {
    return Array.from({ length: size * size }, (_, index) => ids?.[index] || "");
  }

  function openLayout(layout) {
    setSelectedLayoutId(layout?.id || "");
    setLayoutName(layout?.name || "Operations view");
    const size = Number(layout?.grid_size || 2);
    setGridSize(size);
    setLayoutCameraIds(normalizedSlots(layout?.camera_ids || [], size));
    setLayoutNote("");
    setLayoutError("");
  }

  function newLayout() {
    setSelectedLayoutId("");
    setLayoutName("Operations view");
    setGridSize(2);
    setLayoutCameraIds(normalizedSlots([], 2));
    setLayoutNote("");
    setLayoutError("");
  }

  useEffect(() => {
    if (layoutHydrated || !layoutsAvailable) return;
    const first = layouts?.items?.[0];
    if (first) openLayout(first); else newLayout();
    setLayoutHydrated(true);
  }, [layoutHydrated, layouts, layoutsAvailable]);

  function changeGridSize(next) {
    const size = Number(next);
    setGridSize(size);
    setLayoutCameraIds((current) => normalizedSlots(current.filter(Boolean), size));
    setLayoutNote("");
  }

  function setLayoutSlot(index, cameraId) {
    setLayoutError("");
    setLayoutNote("");
    setLayoutCameraIds((current) => {
      const next = normalizedSlots(current, gridSize);
      if (cameraId && next.some((value, slot) => slot !== index && value === cameraId)) {
        setLayoutError("That camera is already in this layout.");
        return current;
      }
      next[index] = cameraId;
      return next;
    });
  }

  async function saveLayout() {
    if (!layoutsAvailable || !layouts?.can_manage || !layoutName.trim()) return;
    setLayoutBusy(true);
    setLayoutError("");
    setLayoutNote("");
    const cameraIds = layoutCameraIds.filter(Boolean);
    const { data: saved, error: saveError } = await supabase().rpc("wl_save_control_room_layout", {
      p_name: layoutName.trim(),
      p_grid_size: gridSize,
      p_camera_ids: cameraIds,
      p_layout_id: selectedLayoutId || null,
    });
    if (saveError) {
      setLayoutError(say(saveError));
    } else if (saved) {
      setLayouts((current) => ({ ...current, items: [saved, ...(current.items || []).filter((item) => item.id !== saved.id)] }));
      openLayout(saved);
      setLayoutNote("Control Room layout saved.");
    }
    setLayoutBusy(false);
  }

  async function deleteLayout() {
    if (!selectedLayoutId || !layouts?.can_manage) return;
    if (!confirm("Delete this Control Room layout?")) return;
    setLayoutBusy(true);
    setLayoutError("");
    const { error: deleteError } = await supabase().rpc("wl_delete_control_room_layout", { p_layout_id: selectedLayoutId });
    if (deleteError) {
      setLayoutError(say(deleteError));
    } else {
      setLayouts((current) => ({ ...current, items: (current.items || []).filter((item) => item.id !== selectedLayoutId) }));
      newLayout();
      setLayoutNote("Layout deleted.");
    }
    setLayoutBusy(false);
  }

  const displayedCameraIds = useMemo(() => layoutCameraIds.filter(Boolean), [layoutCameraIds]);

  useEffect(() => {
    let live = true;
    for (const cameraId of displayedCameraIds) {
      if (cameraShotLoads.current.has(cameraId)) continue;
      cameraShotLoads.current.add(cameraId);
      (async () => {
        const { data: shot, error: shotError } = await supabase().rpc("wl_camera_config_snapshot", { p_camera_id: cameraId });
        if (!live) return;
        if (shotError || !shot?.image_b64) {
          setCameraShots((current) => ({ ...current, [cameraId]: { image: false, capturedAt: null, loading: false } }));
          return;
        }
        setCameraShots((current) => ({ ...current, [cameraId]: {
          image: `data:${shot.content_type || "image/jpeg"};base64,${shot.image_b64}`,
          capturedAt: shot.captured_at || null,
          loading: false,
        } }));
      })();
    }
    return () => { live = false; };
  }, [displayedCameraIds]);

  async function requestFreshStill(camera) {
    if (!camera || studio?.can_manage === false) return;
    const previous = cameraShots[camera.id]?.capturedAt || null;
    setCameraShots((current) => ({ ...current, [camera.id]: { ...(current[camera.id] || {}), loading: true } }));
    setLayoutError("");
    const { error: requestError } = await supabase().rpc("wl_request_config_snapshot", { p_camera_id: camera.id });
    if (requestError) {
      setCameraShots((current) => ({ ...current, [camera.id]: { ...(current[camera.id] || {}), loading: false } }));
      setLayoutError(say(requestError));
      return;
    }
    for (let attempt = 0; attempt < 6; attempt += 1) {
      await sleep(2500);
      const { data: shot, error: shotError } = await supabase().rpc("wl_camera_config_snapshot", { p_camera_id: camera.id });
      if (shotError) {
        setLayoutError(say(shotError));
        break;
      }
      if (shot?.image_b64 && (!previous || shot.captured_at !== previous)) {
        setCameraShots((current) => ({ ...current, [camera.id]: {
          image: `data:${shot.content_type || "image/jpeg"};base64,${shot.image_b64}`,
          capturedAt: shot.captured_at || null,
          loading: false,
        } }));
        setLayoutNote(`Fresh requested still received from ${camera.name || "camera"}.`);
        return;
      }
    }
    setCameraShots((current) => ({ ...current, [camera.id]: { ...(current[camera.id] || {}), loading: false } }));
    setLayoutNote("The requested still is still on its way. Check again in a moment.");
  }

  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) await operationsRef.current?.requestFullscreen();
      else await document.exitFullscreen();
    } catch {
      setLayoutError("Fullscreen mode is not available in this browser.");
    }
  }

  useEffect(() => {
    const targets = model.recent.filter((event) => event.has_snapshot && event.event_id && shots[event.event_id] === undefined).slice(0, 6);
    if (!targets.length) return;
    let live = true;
    targets.forEach(async (event) => {
      const { data: shot } = await supabase().rpc("wl_portal_snapshot", { p_event_id: event.event_id });
      if (!live) return;
      const value = shot?.image_b64 ? `data:${shot.content_type || "image/jpeg"};base64,${shot.image_b64}` : false;
      setShots((current) => ({ ...current, [event.event_id]: value }));
    });
    return () => { live = false; };
  }, [model.recent, shots]);

  if (!data && !error) return <div className="center"><p className="muted">Loading Control Room...</p></div>;

  const totalCameras = data?.totals?.cameras || 0;
  const eventCount = selectedSite === "all" ? (data?.totals?.events || 0) : model.recent.length;
  const currentSites = selectedSite === "all" ? (data?.totals?.sites || model.sites.length) : 1;
  const qsr = analyticsModel.summary;

  return <div className="shell">
    <Nav active="Control Room" email={email} right={<>
      <span className="pill s-warn hide-sm">Pilot</span>
      <span className="muted hide-sm" style={{ fontSize: "var(--font-size-xs)" }}>{stamp ? `updated ${stamp}` : ""}</span>
    </>} />
    <main className="main">
      <header className={ui.pageHead}>
        <div>
          <div className={ui.eyebrow}>Control Room Pilot</div>
          <h1>Run the day across every branch from one operational view.</h1>
          <p>Prioritize site connectivity, camera health, recent events and configured analytics. This pilot does not provide a live video wall or continuous cloud video.</p>
        </div>
        <div className={ui.headActions}>
          <select value={selectedSite} onChange={(event) => setSelectedSite(event.target.value)} aria-label="Filter Control Room by site">
            <option value="all">All sites</option>
            {model.sites.map((site) => <option key={site} value={site}>{site}</option>)}
          </select>
          <button className="secondary" onClick={load}>Refresh</button>
        </div>
      </header>

      {error && <div className="err">{error}</div>}
      {analyticsError && <div className="banner"><b>Analytics could not refresh.</b><div className="muted" style={{ fontSize: "var(--font-size-sm)", marginTop: 4 }}>{analyticsError}</div></div>}

      <div className={ui.callout}><span className={ui.statusDot} /><div><strong>QSR operating lens</strong><p>Use the same branch cameras for people flow, configured queue and checkout-zone activity, after-hours movement, site health and scheduled reporting. Exact transactions and till reconciliation are not inferred from CCTV alone.</p></div></div>

      <section className={ui.metricGrid} aria-label="Control Room summary">
        <div className={ui.metric}><div className={ui.metricValue}>{currentSites}</div><div className={ui.metricLabel}>{selectedSite === "all" ? "Sites in view" : "Selected site"}</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{model.onlineConnections} / {model.agents.length}</div><div className={ui.metricLabel}>Connections online</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{selectedSite === "all" ? totalCameras : model.silentCount ? `${model.silentCount} quiet` : "Clear"}</div><div className={ui.metricLabel}>{selectedSite === "all" ? "Cameras" : "Camera attention"}</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{eventCount}</div><div className={ui.metricLabel}>{selectedSite === "all" ? "Events in 24 hours" : "Recent site events"}</div></div>
      </section>

      <div className={ui.sectionHead}><div><h2>Saved camera layouts</h2><p>Arrange requested camera stills into 2×2, 3×3 or 4×4 operating views. Tiles are not live video.</p></div><div className={ui.inlineActions}><button className="secondary" onClick={newLayout}>New layout</button><button className="secondary" onClick={toggleFullscreen}>{isFullscreen ? "Exit fullscreen" : "Fullscreen operations"}</button></div></div>
      {!layoutsAvailable ? <div className={ui.callout}><span className={ui.statusDot}/><div><strong>Saved layouts are finishing deployment.</strong><p>The Control Room operational view is available, but saved camera layouts need the matching backend update before they can be used.</p></div></div> : <>
        {layoutError && <div className="err">{layoutError}</div>}
        {layoutNote && <div className="ok-note">{layoutNote}</div>}
        <section className={ui.card} style={{ marginBottom: 18 }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(180px,.8fr) minmax(220px,1.3fr) minmax(150px,.55fr) auto", gap: 12, alignItems: "end" }}>
            <label><span className="muted" style={{ display: "block", fontSize: 11, marginBottom: 6 }}>SAVED VIEW</span><select value={selectedLayoutId} onChange={(event) => { const layout = (layouts.items || []).find((item) => item.id === event.target.value); if (layout) openLayout(layout); else newLayout(); }}><option value="">Unsaved layout</option>{(layouts.items || []).map((layout) => <option key={layout.id} value={layout.id}>{layout.name}</option>)}</select></label>
            <label><span className="muted" style={{ display: "block", fontSize: 11, marginBottom: 6 }}>LAYOUT NAME</span><input value={layoutName} maxLength={80} disabled={!layouts.can_manage} onChange={(event) => setLayoutName(event.target.value)} /></label>
            <label><span className="muted" style={{ display: "block", fontSize: 11, marginBottom: 6 }}>GRID</span><select value={gridSize} disabled={!layouts.can_manage} onChange={(event) => changeGridSize(event.target.value)}><option value={2}>2 × 2</option><option value={3}>3 × 3</option><option value={4}>4 × 4</option></select></label>
            <div className={ui.inlineActions}>{layouts.can_manage ? <><button className="primary" disabled={layoutBusy || !layoutName.trim()} onClick={saveLayout}>{layoutBusy ? "Saving..." : "Save layout"}</button>{selectedLayoutId && <button className="secondary" disabled={layoutBusy} onClick={deleteLayout}>Delete</button>}</> : <span className="muted">Read-only</span>}</div>
          </div>
        </section>

        <section ref={operationsRef} className={ui.card} style={{ background: "var(--color-canvas)", overflow: "auto", padding: isFullscreen ? 18 : 14 }}>
          <div style={{ minWidth: gridSize * 235, display: "grid", gridTemplateColumns: `repeat(${gridSize}, minmax(220px,1fr))`, gap: 12 }}>
            {normalizedSlots(layoutCameraIds, gridSize).map((cameraId, index) => {
              const camera = cameraInventory.find((item) => item.id === cameraId) || null;
              const shot = camera ? cameraShots[camera.id] : null;
              const state = camera?.health?.activity_state || "unknown";
              const statusClass = state === "active" ? "s-ok" : state === "silent" ? "s-warn" : state === "never" ? "s-unk" : "s-unk";
              return <article key={index} style={{ minHeight: 245, border: "1px solid var(--color-line-dark)", borderRadius: 12, overflow: "hidden", background: "var(--color-panel)" }}>
                {camera ? <>
                  <div style={{ aspectRatio: "16 / 9", background: "#050a12", display: "grid", placeItems: "center", overflow: "hidden" }}>
                    {shot?.image ? <img src={shot.image} alt={`requested still from ${camera.name || "camera"}`} style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : <div className="muted" style={{ textAlign: "center", padding: 18 }}>{shot?.loading ? "Waiting for requested still..." : "No requested still available"}</div>}
                  </div>
                  <div style={{ padding: 13 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "flex-start" }}><div><strong>{camera.name || `Camera ${camera.channel}`}</strong><div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{camera.siteName} · {humanType(camera.purpose || "custom")}</div></div><span className={`pill ${statusClass}`}>{state === "active" ? "recent" : state === "silent" ? "quiet" : state === "never" ? "not seen" : "health unknown"}</span></div>
                    <div className="muted" style={{ fontSize: 11, marginTop: 9 }}>{camera.health?.last_activity_at ? `Last activity ${ago(camera.health.last_activity_at)}` : "No camera activity timestamp yet"}{camera.recentCount ? ` · ${camera.recentCount} recent event${camera.recentCount === 1 ? "" : "s"}` : ""}</div>
                    <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{shot?.capturedAt ? `Requested still captured ${ago(shot.capturedAt)}` : "Still image is shown only after an explicit request."}</div>
                    <div className={ui.inlineActions} style={{ marginTop: 10, justifyContent: "space-between" }}><select value={cameraId} disabled={!layouts.can_manage} onChange={(event) => setLayoutSlot(index, event.target.value)} style={{ flex: 1, minWidth: 0 }}><option value="">Empty slot</option>{camerasBySite.map(([site, cameras]) => <optgroup key={site} label={site}>{cameras.map((item) => <option key={item.id} value={item.id}>{item.name || `Camera ${item.channel}`}</option>)}</optgroup>)}</select>{studio?.can_manage !== false && <button className="secondary" disabled={shot?.loading} onClick={() => requestFreshStill(camera)}>{shot?.loading ? "Requesting..." : "Request fresh still"}</button>}</div>
                  </div>
                </> : <div style={{ height: "100%", minHeight: 245, display: "grid", placeItems: "center", padding: 16 }}><div style={{ width: "100%" }}><div className="muted" style={{ textAlign: "center", marginBottom: 10 }}>Empty camera slot</div><select value="" disabled={!layouts.can_manage} onChange={(event) => setLayoutSlot(index, event.target.value)}><option value="">Choose camera...</option>{camerasBySite.map(([site, cameras]) => <optgroup key={site} label={site}>{cameras.map((item) => <option key={item.id} value={item.id}>{item.name || `Camera ${item.channel}`}</option>)}</optgroup>)}</select></div></div>}
              </article>;
            })}
          </div>
        </section>
        <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>Camera tiles use previously requested or explicitly refreshed still images. They must not be interpreted as continuous or live video.</div>
      </>}

      <div className={ui.sectionHead}><div><h2>QSR camera roles</h2><p>Use these roles when planning a branch layout, then set each real camera purpose in Analytics Setup.</p></div><a className={ui.secondaryLink} href="/analytics/studio/">Open Analytics Setup</a></div>
      <section className={ui.card}><div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{QSR_CAMERA_ROLES.map((role) => <span key={role} className="pill s-unk">{role}</span>)}</div></section>

      <div className={ui.sectionHead}><div><h2>QSR activity</h2><p>Configured analytics only, measured over the last 24 hours. Checkout-zone values describe people presence, not sales.</p></div><a className={ui.secondaryLink} href="/analytics/">Open Analytics</a></div>
      {analyticsModel.configuredRules === 0 ? <div className={ui.emptyCard}>No analytics setup is configured in this view yet. Add entrance, queue, checkout-zone or after-hours rules in Analytics Setup before using these numbers operationally.</div> : <>
        <section className={ui.metricGrid} aria-label="QSR analytics summary">
          <div className={ui.metric}><div className={ui.metricValue}>{number(qsr.visitor_in)}</div><div className={ui.metricLabel}>Visitor entries</div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{number(qsr.checkout_peak)}</div><div className={ui.metricLabel}>Checkout-zone peak</div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{number(qsr.zone_entries)}</div><div className={ui.metricLabel}>Zone entries</div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{number(qsr.after_hours)}</div><div className={ui.metricLabel}>After-hours signals</div></div>
        </section>
        <div className={ui.sectionHead}><div><h2>Most active analytics</h2><p>Which configured branch measurements produced the most activity in this view.</p></div></div>
        <section className={ui.card}>{analyticsModel.active.length === 0 ? <div className={ui.emptyCard}>Analytics is configured, but no measurement activity has been received in the last 24 hours.</div> : <div className={ui.splitList}>{analyticsModel.active.slice(0, 6).map((item) => <div className={ui.listRow} key={item.rule_id}><span className="pill s-ok">{number(item.count)}</span><div><strong>{item.name || GOAL_LABEL[item.analytic_key] || humanType(item.rule_type)}</strong><small>{[item.site, item.camera, GOAL_LABEL[item.analytic_key]].filter(Boolean).join(" · ")}</small></div></div>)}</div>}</section>
      </>}

      <div className={ui.sectionHead}><div><h2>Operational queue</h2><p>Connection and camera-system issues that should be checked first.</p></div><a className={ui.secondaryLink} href="/site-health/">Open Site Health</a></div>
      <section className={ui.twoCol}>
        <div className={ui.card}>{model.queue.length === 0 ? <div className={ui.emptyCard}>No current connection, quiet-camera or camera-system items need attention in this view.</div> : <div className={ui.splitList}>{model.queue.map((item) => <a key={item.key} className={ui.listRow} href={item.href} style={{ color: "inherit", textDecoration: "none" }}><span className={`pill ${item.severity === "critical" ? "s-bad" : "s-warn"}`}>{item.severity === "critical" ? "Act now" : "Check"}</span><div><strong>{item.title}</strong><small>{item.detail}{item.when ? ` · ${ago(item.when)}` : ""}</small></div></a>)}</div>}</div>
        <div className={ui.featureCard}><div className={ui.eyebrow}>Pilot boundary</div><h3>Operational awareness, not a surveillance wall.</h3><p>The current Control Room combines tenant-safe health, event and analytics data already used elsewhere in WatchLog. Recorded video remains on the recorder. Multi-camera live viewing and recorder clip retrieval require separate field validation before they can move out of pilot scope.</p><div className={ui.inlineActions}><a className={ui.secondaryLink} href="/analytics/">Analytics</a><a className={ui.primaryLink} href="/incidents/">Review incidents</a></div></div>
      </section>

      <div className={ui.sectionHead}><div><h2>Branch status</h2><p>Compare site connectivity and recent attention signals without leaving the Control Room.</p></div></div>
      <section className={ui.card}><div className={ui.tableWrap}>{model.siteRows.length === 0 ? <div className={ui.emptyCard}>No connected sites yet.</div> : <table><thead><tr><th>Site</th><th>Connections</th><th>Online</th><th>Quiet cameras</th><th>Faults 24h</th><th>Recent events</th></tr></thead><tbody>{model.siteRows.map((row) => { const healthy = row.connections > 0 && row.online === row.connections && row.silent === 0 && row.faults === 0; return <tr key={row.site}><td><button className="ghost small" style={{ width: "auto", margin: 0, padding: 0, color: "inherit" }} onClick={() => setSelectedSite(row.site)}><b>{row.site}</b></button></td><td className="mono">{row.connections}</td><td><span className={`pill ${healthy ? "s-ok" : row.online ? "s-warn" : "s-bad"}`}>{row.online} / {row.connections}</span></td><td className="mono">{row.silent}</td><td className="mono">{row.faults}</td><td className="mono">{row.recentCount}</td></tr>; })}</tbody></table>}</div></section>

      <div className={ui.sectionHead}><div><h2>Recent activity</h2><p>Latest tenant events in the current site view, with approved incident stills where available.</p></div><a className={ui.secondaryLink} href="/incidents/">View all incidents</a></div>
      <section className={ui.card}>{model.recent.length === 0 ? <div className={ui.emptyCard}>No recent events are available for this site filter yet.</div> : <div className={ui.splitList}>{model.recent.slice(0, 12).map((event, index) => { const shot = event.event_id ? shots[event.event_id] : undefined; return <a className={ui.listRow} href="/incidents/" key={event.event_id || `${event.device_ts}-${index}`} style={{ color: "inherit", textDecoration: "none" }}>{event.has_snapshot ? shot ? <img src={shot} alt={`still from ${event.camera || "camera"}`} style={{ width: 82, height: 52, objectFit: "cover", borderRadius: 8, border: "1px solid var(--color-line-dark)", flex: "none" }} /> : <span style={{ width: 82, height: 52, borderRadius: 8, border: "1px solid var(--color-line-dark)", display: "grid", placeItems: "center", flex: "none" }} className="muted">{shot === false ? "no image" : "loading"}</span> : <span className="pill s-ok">Event</span>}<div><strong>{humanType(event.event_type)}</strong><small>{[eventSite(event), event.camera].filter(Boolean).join(" · ") || "Camera event"}{event.device_ts ? ` · ${ago(event.device_ts)}` : ""}</small></div></a>; })}</div>}</section>
    </main>
  </div>;
}
