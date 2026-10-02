"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { requireTenant } from "../shell";
import { OwnerPage, Lead, Section, Row, Metrics, Status, RailSection, Stat, Summary, Empty, Loading, Notice, AskLinks, num } from "../owner/ui";
import styles from "./legacy.module.css";

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
const COMMON_CAMERA_ROLES = [
  "Entrance / exit",
  "Queue / service area",
  "Collection / handoff area",
  "Restricted / service boundary",
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
    setStamp(new Date().toISOString());
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

  const selectedSiteRow = selectedSite === "all" ? null : (studio?.sites || []).find((site) => site.name === selectedSite) || null;
  const askSiteId = selectedSiteRow?.id || "";
  const scopeLabel = selectedSite === "all" ? "All sites" : selectedSite;
  const updatedText = stamp ? `updated ${ago(stamp)}` : "";
  const headActions = <>
    <select value={selectedSite} onChange={(event) => setSelectedSite(event.target.value)} aria-label="Filter Control Room by site">
      <option value="all">All sites</option>
      {model.sites.map((site) => <option key={site} value={site}>{site}</option>)}
    </select>
    <button type="button" className="ow-btn quiet" onClick={load}>Refresh</button>
  </>;

  if (!data && !error) return <OwnerPage active="Control Room" email={email} kicker={["Control Room", scopeLabel]} title="See what needs attention across every site." actions={headActions}>
    <Loading label="Loading Control Room" />
  </OwnerPage>;

  const totalCameras = num(data?.totals?.cameras);
  const eventCount = selectedSite === "all" ? num(data?.totals?.events) : model.recent.length;
  const currentSites = selectedSite === "all" ? (data?.totals?.sites || model.sites.length) : 1;
  const activity = analyticsModel.summary;
  const metricValue = (value) => (analyticsError ? null : num(value) === null ? null : number(value));
  const connectionValue = model.agents.length ? `${model.onlineConnections} of ${model.agents.length}` : data ? "None reporting" : null;
  const cameraValue = selectedSite === "all" ? (totalCameras === null ? null : number(totalCameras)) : data ? String(model.silentCount) : null;
  const eventValue = eventCount === null || !data ? null : number(eventCount);

  const critical = model.queue.filter((item) => item.severity === "critical");
  const checks = model.queue.filter((item) => item.severity !== "critical");
  let lead;
  if (!data) {
    lead = { tone: "unknown", title: "Site status could not be checked.", body: "Nothing here is shown as healthy until WatchLog can confirm it." };
  } else if (critical.length) {
    lead = { tone: "bad", title: `${critical.length} site connection${critical.length === 1 ? " is" : "s are"} offline`, body: `Start with ${critical[0].title}.` };
  } else if (checks.length) {
    lead = { tone: "warn", title: `${checks.length} camera item${checks.length === 1 ? " needs" : "s need"} a check`, body: `Start with ${checks[0].title}.` };
  } else if (!model.agents.length) {
    lead = { tone: "unknown", title: "No site connection is reporting in this view.", body: "Camera health cannot be verified until a site connection reports." };
  } else if (model.onlineConnections < model.agents.length) {
    lead = { tone: "warn", title: `${model.onlineConnections} of ${model.agents.length} site connections are reporting`, body: "The others have not reported in the last few minutes." };
  } else {
    lead = { tone: "ok", title: "Every site connection is reporting.", body: "No quiet camera or camera-system fault in the last 24 hours." };
  }

  function siteState(row) {
    const offline = (data?.agents || []).filter((agent) => agent.site === row.site && liveness(agent.last_seen_at)[0] === "s-bad").length;
    if (!row.connections) return { tone: "unknown", word: "No connection" };
    if (row.online === row.connections && row.silent === 0 && row.faults === 0) return { tone: "ok", word: "Healthy" };
    if (row.online === row.connections) return { tone: "warn", word: "Check cameras" };
    if (offline === row.connections) return { tone: "bad", word: "Offline" };
    return { tone: "warn", word: "Partly reporting" };
  }

  const cameraOptions = camerasBySite.map(([site, cameras]) => <optgroup key={site} label={site}>{cameras.map((item) => <option key={item.id} value={item.id}>{item.name || `Camera ${item.channel}`}</option>)}</optgroup>);
  const recentShown = model.recent.slice(0, 12);

  const rail = <>
    <RailSection label={selectedSite === "all" ? "Across every site" : selectedSite}>
      <Stat label={selectedSite === "all" ? "Sites in view" : "Selected site"} value={String(currentSites)} />
      <Stat label="Connections online" value={connectionValue} />
      <Stat label={selectedSite === "all" ? "Cameras" : "Quiet cameras"} value={cameraValue} />
      <Stat label={selectedSite === "all" ? "Events in 24 hours" : "Recent site events"} value={eventValue} />
    </RailSection>
    <RailSection label="Ask WatchLog">
      <AskLinks siteId={askSiteId} prompts={["Which site needs my attention first?", "What changed across my sites in the last 24 hours?"]} />
    </RailSection>
  </>;

  const summary = <Summary items={[
    { value: String(currentSites), label: selectedSite === "all" ? "Sites in view" : "Selected site" },
    { value: connectionValue ?? "Not available", muted: !model.agents.length, label: "Connections online" },
    { value: data ? String(model.queue.length) : "Not available", muted: !data, label: "Need attention" },
    { value: eventValue ?? "Not available", muted: eventValue === null, label: selectedSite === "all" ? "Events in 24 hours" : "Recent site events" },
  ]} />;

  return <OwnerPage active="Control Room" email={email}
    kicker={["Control Room", scopeLabel, updatedText]}
    title="See what needs attention across every site."
    actions={headActions}
    rail={rail}
    summary={summary}>
    {error && <Notice tone="bad">{error}</Notice>}
    {analyticsError && <Notice tone="warn"><div><b>Analytics could not refresh.</b> {analyticsError}</div></Notice>}

    <Lead tone={lead.tone} title={lead.title} body={lead.body} />

    <Section first title="Needs attention first" count={model.queue.length || null} action={<a href="/site-health/">Open Site Health</a>}>
      {!data ? <Empty title="Not available">Site connection and camera status could not be checked.</Empty>
        : model.queue.length === 0 ? <Empty title="No connection or camera item needs attention in this view.">Anything WatchLog cannot verify stays marked as not verified.</Empty>
        : <div className="ow-rows">{model.queue.map((item) => <Row key={item.key}
          tone={item.severity === "critical" ? "bad" : "warn"}
          title={item.title}
          body={item.detail}
          meta={[<Status key="state" tone={item.severity === "critical" ? "bad" : "warn"}>{item.severity === "critical" ? "Act now" : "Check"}</Status>, item.when ? ago(item.when) : null]}
          action={<a className="ow-btn small quiet" href={item.href}>Open</a>} />)}</div>}
    </Section>

    <Section title="Site status" count={model.siteRows.length || null} note="Last 24 hours. Choose a site to focus the whole view.">
      {model.siteRows.length === 0 ? <Empty title="No connected sites yet." />
        : <div className="ow-rows">{model.siteRows.map((row) => {
          const state = siteState(row);
          return <Row key={row.site} tone={state.tone}
            title={row.site}
            meta={[
              <Status key="state" tone={state.tone}>{state.word}</Status>,
              `${row.online} of ${row.connections} connection${row.connections === 1 ? "" : "s"} online`,
              `${row.silent} quiet camera${row.silent === 1 ? "" : "s"}`,
              `${row.faults} fault${row.faults === 1 ? "" : "s"} in 24h`,
              `${row.recentCount} recent event${row.recentCount === 1 ? "" : "s"}`,
            ]}
            action={selectedSite === row.site
              ? <button type="button" className="ow-btn small quiet" onClick={() => setSelectedSite("all")}>All sites</button>
              : <button type="button" className="ow-btn small quiet" onClick={() => setSelectedSite(row.site)}>Focus</button>} />;
        })}</div>}
    </Section>

    <Section title="Recent activity" count={recentShown.length || null} note="Latest events in this view, with incident evidence where available." action={<a href="/incidents/">View all incidents</a>}>
      {recentShown.length === 0 ? <Empty title="No recent events in this view yet." />
        : <div>{recentShown.map((event, index) => {
          const shot = event.event_id ? shots[event.event_id] : undefined;
          return <a className={styles.activity} href="/incidents/" key={event.event_id || `${event.device_ts}-${index}`}>
            <i className={`${styles.tick} ${event.has_snapshot && shot ? styles.verified : ""}`} aria-hidden="true" />
            {event.has_snapshot && shot
              ? <img className={styles.thumb} src={shot} alt={`Evidence from ${event.camera || "camera"}`} />
              : <span className={styles.thumb}>{event.has_snapshot ? (shot === false ? "No image" : "Loading") : "No image"}</span>}
            <div>
              <h3>{humanType(event.event_type)}</h3>
              <p>{[eventSite(event), event.camera].filter(Boolean).join(" · ") || "Camera event"}{event.device_ts ? ` · ${ago(event.device_ts)}` : ""}</p>
            </div>
            <span className={styles.go}>Review</span>
          </a>;
        })}</div>}
    </Section>

    <Section title="Activity analytics" note="Last 24 hours from your activity rules. Occupancy describes people presence, not sales." action={<a href="/analytics/">Open Analytics</a>}>
      {analyticsModel.configuredRules === 0
        ? <Empty title="No activity rules are set up in this view yet.">Add entrance, queue, occupancy or after-hours rules before relying on these numbers.</Empty>
        : <>
          <Metrics items={[
            { value: metricValue(activity.visitor_in), label: "Visitor entries" },
            { value: metricValue(activity.checkout_peak), label: "Area occupancy peak" },
            { value: metricValue(activity.zone_entries), label: "Zone entries" },
            { value: metricValue(activity.after_hours), label: "After-hours signals" },
          ]} />
          <div className="ow-label" style={{ margin: "18px 0 6px" }}>Most active rules</div>
          {analyticsModel.active.length === 0
            ? <Empty title="No rule activity in the last 24 hours.">Your activity rules are set up; nothing has been recorded yet in this view.</Empty>
            : <div className="ow-rows">{analyticsModel.active.slice(0, 6).map((item) => <Row compact key={item.rule_id}
              tone="verified"
              title={item.name || GOAL_LABEL[item.analytic_key] || humanType(item.rule_type)}
              meta={[`${number(item.count)} recorded`, ...[item.site, item.camera, GOAL_LABEL[item.analytic_key]].filter(Boolean)]} />)}</div>}
        </>}
    </Section>

    <details className="ow-details" style={{ marginTop: 28 }}>
      <summary>Saved camera layouts{layouts?.items?.length ? ` · ${layouts.items.length} saved` : ""}</summary>
      <div>
        <p className="ow-muted" style={{ fontSize: 12.5, marginBottom: 10 }}>Tiles are not live video. They show requested camera views, not a live video wall or continuous cloud video, and each tile says how recent its view is.</p>
        <div className={styles.buttons} style={{ marginBottom: 12 }}>
          <button type="button" className="ow-btn quiet small" onClick={newLayout}>New layout</button>
          <button type="button" className="ow-btn quiet small" onClick={toggleFullscreen}>{isFullscreen ? "Exit fullscreen" : "Fullscreen operations"}</button>
        </div>
        {!layoutsAvailable ? <Notice>Saved layouts are temporarily unavailable. You can continue using the rest of Control Room.</Notice> : <>
          {layoutError && <Notice tone="bad">{layoutError}</Notice>}
          {layoutNote && <Notice tone="ok">{layoutNote}</Notice>}
          <div className={styles.controls}>
            <label className={`ow-field ${styles.grow}`}>Saved view
              <select value={selectedLayoutId} onChange={(event) => { const layout = (layouts.items || []).find((item) => item.id === event.target.value); if (layout) openLayout(layout); else newLayout(); }}>
                <option value="">Unsaved layout</option>
                {(layouts.items || []).map((layout) => <option key={layout.id} value={layout.id}>{layout.name}</option>)}
              </select>
            </label>
            <label className={`ow-field ${styles.grow}`}>Layout name
              <input value={layoutName} maxLength={80} disabled={!layouts.can_manage} onChange={(event) => setLayoutName(event.target.value)} />
            </label>
            <label className="ow-field">Grid
              <select value={gridSize} disabled={!layouts.can_manage} onChange={(event) => changeGridSize(event.target.value)}>
                <option value={2}>2 × 2</option>
                <option value={3}>3 × 3</option>
                <option value={4}>4 × 4</option>
              </select>
            </label>
            <div className={styles.buttons}>
              {layouts.can_manage ? <>
                <button type="button" className="ow-btn" disabled={layoutBusy || !layoutName.trim()} onClick={saveLayout}>{layoutBusy ? "Saving..." : "Save layout"}</button>
                {selectedLayoutId && <button type="button" className="ow-btn danger" disabled={layoutBusy} onClick={deleteLayout}>Delete</button>}
              </> : <span className="ow-muted">Read-only</span>}
            </div>
          </div>

          <section ref={operationsRef} className={styles.wall} aria-label="Camera layout">
            <div className={styles.tiles} style={{ "--cols": gridSize }}>
              {normalizedSlots(layoutCameraIds, gridSize).map((cameraId, index) => {
                const camera = cameraInventory.find((item) => item.id === cameraId) || null;
                const shot = camera ? cameraShots[camera.id] : null;
                const state = camera?.health?.activity_state || "unknown";
                const stateTone = state === "active" ? "ok" : state === "silent" ? "warn" : "unknown";
                const stateWord = state === "active" ? "Recent activity" : state === "silent" ? "Quiet" : state === "never" ? "Not seen" : "Health unknown";
                return <article key={index} className={styles.tile}>
                  {camera ? <>
                    <div className={styles.view}>
                      {shot?.image ? <img src={shot.image} alt={`Requested view from ${camera.name || "camera"}`} /> : <span>{shot?.loading ? "Waiting for the requested view..." : "No requested view available"}</span>}
                    </div>
                    <div className={styles.tileBody}>
                      <div className={styles.tileHead}>
                        <div><h3>{camera.name || `Camera ${camera.channel}`}</h3><p>{camera.siteName} · {camera.purpose ? humanType(camera.purpose) : "Purpose not set"}</p></div>
                        <Status tone={stateTone}>{stateWord}</Status>
                      </div>
                      <div className={styles.tileMeta}>{camera.health?.last_activity_at ? `Last activity ${ago(camera.health.last_activity_at)}` : "No camera activity time yet"}{camera.recentCount ? ` · ${camera.recentCount} recent event${camera.recentCount === 1 ? "" : "s"}` : ""}</div>
                      <div className={styles.tileMeta}>{shot?.capturedAt ? `Requested view captured ${ago(shot.capturedAt)}` : "Still image is shown only after an explicit request."}</div>
                      <div className={styles.tileAct}>
                        <select aria-label={`Camera for slot ${index + 1}`} value={cameraId} disabled={!layouts.can_manage} onChange={(event) => setLayoutSlot(index, event.target.value)}>
                          <option value="">Empty slot</option>
                          {cameraOptions}
                        </select>
                        {studio?.can_manage !== false && <button type="button" className="ow-btn small quiet" disabled={shot?.loading} onClick={() => requestFreshStill(camera)}>{shot?.loading ? "Requesting..." : "Request fresh still"}</button>}
                      </div>
                    </div>
                  </> : <div className={styles.empty}>
                    <span>Empty camera slot</span>
                    <select aria-label={`Camera for slot ${index + 1}`} value="" disabled={!layouts.can_manage} onChange={(event) => setLayoutSlot(index, event.target.value)}>
                      <option value="">Choose camera...</option>
                      {cameraOptions}
                    </select>
                  </div>}
                </article>;
              })}
            </div>
          </section>
        </>}
      </div>
    </details>

    <details className="ow-details">
      <summary>Common camera purposes</summary>
      <div>
        <div className={styles.purposes}>{COMMON_CAMERA_ROLES.map((role) => <span key={role} className="ow-pill">{role}</span>)}</div>
        <a href="/analytics/studio/">Set each camera&apos;s purpose in Activity Rules</a>
      </div>
    </details>
  </OwnerPage>;
}
