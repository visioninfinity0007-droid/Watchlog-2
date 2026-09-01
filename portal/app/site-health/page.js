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
    item?.last_seen_at || item?.last_event ? ago(item.last_seen_at || item.last_event) : null,
  ].filter(Boolean).join(" · ");
  return [primary, secondary];
}

function IssueCard({ title, copy, items, fallback }) {
  return <section className={styles.issueCard}>
    <h3>{title}</h3><p>{copy}</p>
    {items.length ? <div className={styles.issueList}>{items.map((item, i) => {
      const [primary, secondary] = issueLine(item, fallback);
      return <div className={styles.issue} key={item.id || `${primary}-${i}`}>
        <i /><div><strong>{primary}</strong>{secondary && <small>{secondary}</small>}</div>
      </div>;
    })}</div> : <div className={styles.clear}>No issue in this category.</div>}
  </section>;
}

export default function SiteHealth() {
  const [email, setEmail] = useState("");
  const [overview, setOverview] = useState(null);
  const [sites, setSites] = useState([]);
  const [error, setError] = useState("");
  const [stamp, setStamp] = useState("");

  const load = useCallback(async () => {
    const guard = await requireTenant();
    if (!guard) return;
    setEmail(guard.session.user.email || "");
    const sb = supabase();
    const [o, s] = await Promise.all([
      sb.rpc("wl_portal_overview", { p_days: 7 }),
      sb.rpc("wl_sites"),
    ]);
    if (o.error || s.error) { setError(say(o.error || s.error)); return; }
    setError("");
    setOverview(o.data || {});
    setSites(s.data || []);
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
  const silentCameras = health.silent_cameras || [];
  const faults = health.faults_24h || [];
  const onlineAgents = agents.filter((a) => liveness(a.last_seen_at)[0] === "s-ok").length;
  const totalCameras = Number(overview?.totals?.cameras || sites.reduce((n, s) => n + Number(s.cameras || 0), 0));

  const bySite = useMemo(() => {
    const grouped = {};
    for (const a of agents) (grouped[a.site] = grouped[a.site] || []).push(a);
    return grouped;
  }, [agents]);

  const healthySites = sites.filter((s) => {
    const siteAgents = bySite[s.name] || [];
    const hasOnline = siteAgents.some((a) => liveness(a.last_seen_at)[0] === "s-ok") || s.online;
    const issue = offlineAgents.some((a) => a.site === s.name)
      || silentCameras.some((c) => c.site === s.name)
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
          <p>See whether each site, Site Agent and camera is reporting normally. WatchLog surfaces gaps here and in the daily report rather than pretending to be a live alarm service.</p>
        </div>
      </div>

      {error && <div className="err">{error}</div>}
      {!overview ? <div className="panel"><div className="empty">Loading site health...</div></div> : <>
        <section className={styles.metrics} aria-label="Site health summary">
          <div className={styles.metric}><div className={styles.metricLabel}>Healthy sites</div><div className={styles.metricValue}>{healthySites}/{sites.length}</div><div className={styles.metricHint}>No current reporting issue</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Cameras</div><div className={styles.metricValue}>{totalCameras}</div><div className={styles.metricHint}>{silentCameras.length} currently silent</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Site Agents online</div><div className={styles.metricValue}>{onlineAgents}/{agents.length}</div><div className={styles.metricHint}>Live contact within 3 minutes</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Faults, 24h</div><div className={styles.metricValue}>{faults.length}</div><div className={styles.metricHint}>Recorder or camera faults</div></div>
        </section>

        <div className={styles.sectionHead}><div><h2>Sites</h2><p>Health by location, including setup state, cameras and Site Agent contact.</p></div></div>
        <section className={styles.siteGrid}>
          {sites.map((s) => {
            const siteAgents = bySite[s.name] || [];
            const online = siteAgents.filter((a) => liveness(a.last_seen_at)[0] === "s-ok").length;
            const siteIssues = offlineAgents.filter((a) => a.site === s.name).length
              + silentCameras.filter((c) => c.site === s.name).length
              + faults.filter((f) => f.site === s.name).length;
            const [setupLabel, setupClass] = setupPill(s.setup_state, s.online || online > 0);
            return <article className={styles.siteCard} key={s.id}>
              <div className={styles.siteTop}><div><h3>{s.name}</h3><span className="muted" style={{fontSize:"var(--font-size-xs)"}}>{s.timezone}</span></div>
                <span className={"pill " + (siteIssues ? "s-warn" : (s.online || online > 0) ? "s-ok" : "s-bad")}>{siteIssues ? `${siteIssues} issue${siteIssues === 1 ? "" : "s"}` : (s.online || online > 0) ? "healthy" : "offline"}</span>
              </div>
              <div className={styles.siteMeta}>
                <div><span>Setup</span><b><span className={"pill " + setupClass}>{setupLabel}</span></b></div>
                <div><span>Cameras</span><b>{s.cameras ?? 0}</b></div>
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
          <IssueCard title="Silent cameras" copy="Cameras that have stopped producing activity." items={silentCameras} fallback="Camera silent" />
          <IssueCard title="Recorder & camera faults" copy="Faults reported by the CCTV system in the last 24 hours." items={faults} fallback="Recorder / camera fault" />
        </div>

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
