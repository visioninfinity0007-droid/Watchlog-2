"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav } from "../shell";
import ui from "../portal.module.css";

const REFRESH_MS = 10000;

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

export default function ControlRoom() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [stamp, setStamp] = useState("");
  const [selectedSite, setSelectedSite] = useState("all");

  const load = useCallback(async () => {
    const sb = supabase();
    const { data: auth } = await sb.auth.getSession();
    if (!auth?.session) {
      location.replace("/login/");
      return;
    }
    setEmail(auth.session.user.email || "");
    const { data: result, error: rpcError } = await sb.rpc("wl_portal_overview", { p_days: 1 });
    if (rpcError) {
      setError(say(rpcError));
      return;
    }
    if (!result?.tenant) {
      location.replace("/onboarding/");
      return;
    }
    setData(result);
    setError("");
    setStamp(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

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
    const filteredRecent = selectedSite === "all"
      ? recent
      : recent.filter((event) => eventSite(event) === selectedSite);

    const onlineConnections = filteredAgents.filter((agent) => liveness(agent.last_seen_at)[0] === "s-ok").length;
    const sitesNeedingAttention = new Set([
      ...filteredOffline.map((item) => item.site).filter(Boolean),
      ...filteredSilent.map((item) => item.site).filter(Boolean),
      ...filteredFaults.map((item) => item.site).filter(Boolean),
    ]).size;

    const queue = [
      ...filteredOffline.map((item) => ({
        key: `offline-${item.agent_id || item.site || Math.random()}`,
        severity: "critical",
        title: `${item.site || "Site"} connection offline`,
        detail: item.hostname ? `${item.hostname} has stopped reporting.` : "The WatchLog connection has stopped reporting.",
        when: item.last_seen_at,
        href: "/site-health/",
      })),
      ...filteredSilent.map((item) => ({
        key: `silent-${item.camera_id || item.camera || Math.random()}`,
        severity: "attention",
        title: `${item.camera || "Camera"} is quiet${item.site ? ` at ${item.site}` : ""}`,
        detail: "No recent camera activity has been observed for this configured source.",
        when: item.last_seen_at || item.last_event_at,
        href: "/site-health/",
      })),
      ...filteredFaults.map((item) => ({
        key: `fault-${item.event_id || item.camera || Math.random()}`,
        severity: "attention",
        title: `${item.camera || "Camera system"} fault${item.site ? ` at ${item.site}` : ""}`,
        detail: humanType(item.event_type || item.type || "camera system fault"),
        when: item.device_ts || item.created_at,
        href: "/site-health/",
      })),
    ].slice(0, 12);

    const siteRows = sites.map((site) => {
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
      sites,
      queue,
      siteRows,
      onlineConnections,
      sitesNeedingAttention,
      silentCount: filteredSilent.length,
      faultCount: filteredFaults.length,
    };
  }, [data, selectedSite]);

  if (!data && !error) return <div className="center"><p className="muted">Loading Control Room...</p></div>;

  const totalCameras = data?.totals?.cameras || 0;
  const eventCount = selectedSite === "all" ? (data?.totals?.events || 0) : model.recent.length;
  const currentSites = selectedSite === "all" ? (data?.totals?.sites || model.sites.length) : 1;

  return <div className="shell">
    <Nav
      active="Control Room"
      email={email}
      right={<>
        <span className="pill s-warn hide-sm">Pilot</span>
        <span className="muted hide-sm" style={{ fontSize: "var(--font-size-xs)" }}>{stamp ? `updated ${stamp}` : ""}</span>
      </>}
    />
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

      <div className={ui.callout}>
        <span className={ui.statusDot} />
        <div>
          <strong>QSR operating lens</strong>
          <p>Use the same branch cameras for people flow, configured queue and checkout-zone activity, after-hours movement, site health and scheduled reporting. Exact transactions and till reconciliation are not inferred from CCTV alone.</p>
        </div>
      </div>

      <section className={ui.metricGrid} aria-label="Control Room summary">
        <div className={ui.metric}><div className={ui.metricValue}>{currentSites}</div><div className={ui.metricLabel}>{selectedSite === "all" ? "Sites in view" : "Selected site"}</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{model.onlineConnections} / {model.agents.length}</div><div className={ui.metricLabel}>Connections online</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{selectedSite === "all" ? totalCameras : model.silentCount ? `${model.silentCount} quiet` : "Clear"}</div><div className={ui.metricLabel}>{selectedSite === "all" ? "Cameras" : "Camera attention"}</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{eventCount}</div><div className={ui.metricLabel}>{selectedSite === "all" ? "Events in 24 hours" : "Recent site events"}</div></div>
      </section>

      <div className={ui.sectionHead}><div><h2>Operational queue</h2><p>Connection and camera-system issues that should be checked first.</p></div><a className={ui.secondaryLink} href="/site-health/">Open Site Health</a></div>
      <section className={ui.twoCol}>
        <div className={ui.card}>
          {model.queue.length === 0 ? <div className={ui.emptyCard}>No current connection, quiet-camera or camera-system items need attention in this view.</div> : <div className={ui.splitList}>{model.queue.map((item) => <a key={item.key} className={ui.listRow} href={item.href} style={{ color: "inherit", textDecoration: "none" }}>
            <span className={`pill ${item.severity === "critical" ? "s-bad" : "s-warn"}`}>{item.severity === "critical" ? "Act now" : "Check"}</span>
            <div><strong>{item.title}</strong><small>{item.detail}{item.when ? ` · ${ago(item.when)}` : ""}</small></div>
          </a>)}</div>}
        </div>
        <div className={ui.featureCard}>
          <div className={ui.eyebrow}>Pilot boundary</div>
          <h3>Operational awareness, not a surveillance wall.</h3>
          <p>The current Control Room combines tenant-safe health, event and analytics data already used elsewhere in WatchLog. Recorded video remains on the recorder. Multi-camera live viewing and recorder clip retrieval require separate field validation before they can move out of pilot scope.</p>
          <div className={ui.inlineActions}><a className={ui.secondaryLink} href="/analytics/">Analytics</a><a className={ui.primaryLink} href="/incidents/">Review incidents</a></div>
        </div>
      </section>

      <div className={ui.sectionHead}><div><h2>Branch status</h2><p>Compare site connectivity and recent attention signals without leaving the Control Room.</p></div></div>
      <section className={ui.card}>
        <div className={ui.tableWrap}>
          {model.siteRows.length === 0 ? <div className={ui.emptyCard}>No connected sites yet.</div> : <table>
            <thead><tr><th>Site</th><th>Connections</th><th>Online</th><th>Quiet cameras</th><th>Faults 24h</th><th>Recent events</th></tr></thead>
            <tbody>{model.siteRows.map((row) => {
              const healthy = row.connections > 0 && row.online === row.connections && row.silent === 0 && row.faults === 0;
              return <tr key={row.site}>
                <td><button className="ghost small" style={{ width: "auto", margin: 0, padding: 0, color: "inherit" }} onClick={() => setSelectedSite(row.site)}><b>{row.site}</b></button></td>
                <td className="mono">{row.connections}</td>
                <td><span className={`pill ${healthy ? "s-ok" : row.online ? "s-warn" : "s-bad"}`}>{row.online} / {row.connections}</span></td>
                <td className="mono">{row.silent}</td>
                <td className="mono">{row.faults}</td>
                <td className="mono">{row.recentCount}</td>
              </tr>;
            })}</tbody>
          </table>}
        </div>
      </section>

      <div className={ui.sectionHead}><div><h2>Recent activity</h2><p>Latest tenant events in the current site view.</p></div><a className={ui.secondaryLink} href="/incidents/">View all incidents</a></div>
      <section className={ui.card}>
        {model.recent.length === 0 ? <div className={ui.emptyCard}>No recent events are available for this site filter yet.</div> : <div className={ui.splitList}>{model.recent.slice(0, 12).map((event, index) => <a className={ui.listRow} href="/incidents/" key={event.event_id || `${event.device_ts}-${index}`} style={{ color: "inherit", textDecoration: "none" }}>
          <span className="pill s-ok">Event</span>
          <div><strong>{humanType(event.event_type)}</strong><small>{[eventSite(event), event.camera].filter(Boolean).join(" · ") || "Camera event"}{event.device_ts ? ` · ${ago(event.device_ts)}` : ""}</small></div>
        </a>)}</div>}
      </section>
    </main>
  </div>;
}
