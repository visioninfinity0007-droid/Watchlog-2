"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant, setupPill } from "../shell";
import styles from "./site-health.module.css";

const REFRESH_MS = 15000;

function liveness(lastSeen) {
  if (!lastSeen) return ["s-unk", "never seen"];
  const age = (Date.now() - Date.parse(lastSeen)) / 1000;
  if (age < 180) return ["s-ok", "online"];
  if (age < 1800) return ["s-warn", "stale"];
  return ["s-bad", "offline"];
}

function ago(ts) {
  if (!ts) return "Never";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function human(v) {
  if (!v) return "Fault";
  return String(v).replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

function issueLine(item, fallback) {
  const primary = item?.camera || item?.hostname || item?.device || item?.site || fallback;
  const secondary = [
    item?.site && item.site !== primary ? item.site : null,
    item?.event_type || item?.type ? human(item.event_type || item.type) : null,
    item?.device_ts || item?.last_seen_at || item?.last_activity_at
      ? ago(item.device_ts || item.last_seen_at || item.last_activity_at) : null,
  ].filter(Boolean).join(" · ");
  return [primary, secondary];
}

function IssueCard({ title, copy, items, fallback }) {
  return <section className={styles.issueCard}>
    <h3>{title}</h3><p>{copy}</p>
    {items.length ? <div className={styles.issueList}>{items.slice(0, 8).map((item, i) => {
      const [primary, secondary] = issueLine(item, fallback);
      return <div className={styles.issue} key={item.event_id || item.id || `${primary}-${i}`}>
        <i /><div><strong>{primary}</strong>{secondary && <small>{secondary}</small>}</div>
      </div>;
    })}</div> : <div className={styles.clear}>No issue in this category.</div>}
  </section>;
}

export default function SiteHealth() {
  const [email, setEmail] = useState("");
  const [overview, setOverview] = useState(null);
  const [sites, setSites] = useState([]);
  const [details, setDetails] = useState({ cameras: [], faults: [] });
  const [error, setError] = useState("");
  const [stamp, setStamp] = useState("");

  const load = useCallback(async () => {
    const guard = await requireTenant();
    if (!guard) return;
    setEmail(guard.session.user.email || "");
    const sb = supabase();
    const [o, s, h] = await Promise.all([
      sb.rpc("wl_portal_overview", { p_days: 7 }),
      sb.rpc("wl_sites"),
      sb.rpc("wl_site_health_details", { p_days: 1 }),
    ]);
    if (o.error || s.error || h.error) { setError(say(o.error || s.error || h.error)); return; }
    setError("");
    setOverview(o.data || {});
    setSites(s.data || []);
    setDetails(h.data || { cameras: [], faults: [] });
    setStamp(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const agents = overview?.agents || [];
  const health = overview?.health || {};
  const offlineAgents = health.offline_agents || [];
  const cameras = details?.cameras || [];
  const faults = details?.faults || [];
  const quietCameras = cameras.filter((c) => c.activity_state === "silent" || c.activity_state === "never");
  const onlineAgents = agents.filter((a) => liveness(a.last_seen_at)[0] === "s-ok").length;
  const totalCameras = Number(overview?.totals?.cameras || cameras.length || sites.reduce((n, s) => n + Number(s.cameras || 0), 0));

  const bySite = useMemo(() => {
    const grouped = {};
    for (const a of agents) (grouped[a.site] = grouped[a.site] || []).push(a);
    return grouped;
  }, [agents]);

  const camerasBySite = useMemo(() => {
    const grouped = {};
    for (const c of cameras) (grouped[c.site] = grouped[c.site] || []).push(c);
    return grouped;
  }, [cameras]);

  const healthySites = sites.filter((s) => {
    const siteAgents = bySite[s.name] || [];
    const hasOnline = siteAgents.some((a) => liveness(a.last_seen_at)[0] === "s-ok") || s.online;
    const issue = offlineAgents.some((a) => a.site === s.name)
      || quietCameras.some((c) => c.site === s.name)
      || faults.some((f) => f.site === s.name);
    return hasOnline && !issue;
  }).length;

  return <div className="shell">
    <Nav active="Site Health" email={email} right={
      <span className="muted hide-sm" style={{ fontSize: "var(--font-size-xs)" }}>
        {stamp ? `updated ${stamp}` : ""}
      </span>
    } />
    <main className="main">
      <div className={styles.pageHead}>
        <div>
          <div className={styles.eyebrow}>Site Health</div>
          <h1>Know when something goes quiet.</h1>
          <p>See whether each site, Site Agent and camera is reporting normally. Camera activity is a recorder signal, not a continuous heartbeat, so WatchLog shows exactly when activity was last reported.</p>
        </div>
      </div>

      {error && <div className="err">{error}</div>}
      {!overview ? <div className="panel"><div className="empty">Loading site health...</div></div> : <>
        <section className={styles.metrics} aria-label="Site health summary">
          <div className={styles.metric}><div className={styles.metricLabel}>Healthy sites</div><div className={styles.metricValue}>{healthySites}/{sites.length}</div><div className={styles.metricHint}>No current reporting issue</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Cameras</div><div className={styles.metricValue}>{totalCameras}</div><div className={styles.metricHint}>{quietCameras.length} with no activity in 24h</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Site Agents online</div><div className={styles.metricValue}>{onlineAgents}/{agents.length}</div><div className={styles.metricHint}>Live contact within 3 minutes</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Faults, 24h</div><div className={styles.metricValue}>{faults.length}</div><div className={styles.metricHint}>Recorder or camera fault events</div></div>
        </section>

        <div className={styles.sectionHead}><div><h2>Sites</h2><p>Health by location, including setup state, cameras and Site Agent contact.</p></div></div>
        <section className={styles.siteGrid}>
          {sites.map((s) => {
            const siteAgents = bySite[s.name] || [];
            const online = siteAgents.filter((a) => liveness(a.last_seen_at)[0] === "s-ok").length;
            const siteQuiet = quietCameras.filter((c) => c.site === s.name).length;
            const siteFaults = faults.filter((f) => f.site === s.name).length;
            const siteIssues = offlineAgents.filter((a) => a.site === s.name).length + siteQuiet + siteFaults;
            const [setupLabel, setupClass] = setupPill(s.setup_state, s.online || online > 0);
            return <article className={styles.siteCard} key={s.id}>
              <div className={styles.siteTop}><div><h3>{s.name}</h3><span className="muted" style={{fontSize:"var(--font-size-xs)"}}>{s.timezone}</span></div>
                <span className={"pill " + (siteIssues ? "s-warn" : (s.online || online > 0) ? "s-ok" : "s-bad")}>{siteIssues ? `${siteIssues} issue${siteIssues === 1 ? "" : "s"}` : (s.online || online > 0) ? "healthy" : "offline"}</span>
              </div>
              <div className={styles.siteMeta}>
                <div><span>Setup</span><b><span className={"pill " + setupClass}>{setupLabel}</span></b></div>
                <div><span>Cameras</span><b>{(camerasBySite[s.name] || []).length || s.cameras || 0}</b></div>
                <div><span>Site Agents</span><b>{online}/{siteAgents.length} online</b></div>
                <div><span>Last event</span><b>{ago(s.last_event)}</b></div>
              </div>
            </article>;
          })}
          {!sites.length && <div className="panel"><div className="empty">No sites yet. Add a site in Settings to begin health monitoring.</div></div>}
        </section>

        <div className={styles.sectionHead}><div><h2>Needs attention</h2><p>The specific gaps WatchLog has observed. A quiet list is good news.</p></div></div>
        <div className={styles.issueGrid}>
          <IssueCard title="Site Agents" copy="PCs that stopped checking in to WatchLog." items={offlineAgents} fallback="Site Agent offline" />
          <IssueCard title="Quiet cameras" copy="Cameras with no recorded activity in the last 24 hours. Review in context before treating silence as a fault." items={quietCameras} fallback="Camera quiet" />
          <IssueCard title="Recorder & camera faults" copy="Fault events reported by the CCTV system in the last 24 hours." items={faults} fallback="Recorder / camera fault" />
        </div>

        <div className={styles.sectionHead}><div><h2>Camera activity</h2><p>Last activity WatchLog received from every discovered camera, by site.</p></div></div>
        <div className="panel"><div className={styles.tableWrap}>
          {cameras.length ? <table><thead><tr><th>Site</th><th>Camera</th><th>Channel</th><th>Activity state</th><th>Last reported activity</th></tr></thead><tbody>
            {cameras.map((c) => <tr key={c.id}><td>{c.site}</td><td className={styles.agentName}>{c.camera}</td><td className="mono">{c.channel}</td><td><span className={"pill " + (c.activity_state === "active" ? "s-ok" : c.activity_state === "silent" ? "s-warn" : "s-unk")}>{c.activity_state === "active" ? "recent activity" : c.activity_state === "silent" ? "quiet 24h+" : "no activity yet"}</span></td><td title={c.last_activity_at ? new Date(c.last_activity_at).toLocaleString() : ""}>{ago(c.last_activity_at)}</td></tr>)}
          </tbody></table> : <div className="empty">No cameras have been discovered yet.</div>}
        </div></div>

        <div className={styles.sectionHead}><div><h2>Site Agents</h2><p>Last contact from the Windows PC at each site.</p></div></div>
        <div className="panel"><div className={styles.tableWrap}>
          {agents.length ? <table><thead><tr><th>Site</th><th>PC</th><th>Status</th><th>Last seen</th><th className="hide-sm">Recorder</th></tr></thead><tbody>
            {agents.map((a) => { const [cls, label] = liveness(a.last_seen_at); const recorder=[a.device_vendor,a.device_model].filter(Boolean).join(" "); return <tr key={a.agent_id}>
              <td>{a.site}</td><td className={styles.agentName}>{a.hostname || "Site PC"}</td><td><span className={"pill " + cls}>{label}</span></td><td>{ago(a.last_seen_at)}</td><td className="muted hide-sm">{recorder || "Not identified"}</td>
            </tr>; })}
          </tbody></table> : <div className="empty">No Site Agent has connected yet.</div>}
        </div></div>
      </>}
    </main>
  </div>;
}
