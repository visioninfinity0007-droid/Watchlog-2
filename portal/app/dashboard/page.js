"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav } from "../shell";

const REFRESH_MS = 10000;

// Liveness is recomputed in the browser rather than trusted from the
// server payload, so a page left open on a stale tab cannot keep showing
// a dead agent as online.
function liveness(lastSeen) {
  if (!lastSeen) return ["s-unk", "never seen"];
  const age = (Date.now() - Date.parse(lastSeen)) / 1000;
  if (age < 180) return ["s-ok", "online"];
  if (age < 1800) return ["s-warn", "stale"];
  return ["s-bad", "offline"];
}

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [days, setDays] = useState(7);
  const [email, setEmail] = useState("");
  const [stamp, setStamp] = useState("");
  // Images are fetched one at a time and cached. Bundling 20 stills into
  // the feed would be several megabytes on every 10-second refresh.
  const shots = useRef(new Map());
  const [, force] = useState(0);

  const load = useCallback(async (d) => {
    const sb = supabase();
    const { data: { session } } = await sb.auth.getSession();
    if (!session) { location.replace("/login/"); return; }
    setEmail(session.user.email || "");

    const { data: res, error } = await sb.rpc("wl_portal_overview", { p_days: d });
    if (error) { setError(say(error)); return; }
    if (!res || !res.tenant) { location.replace("/onboarding/"); return; }

    setError("");
    setData(res);
    setStamp(new Date().toLocaleTimeString());
  }, []);

  useEffect(() => {
    load(days);
    const t = setInterval(() => load(days), REFRESH_MS);
    return () => clearInterval(t);
  }, [days, load]);

  async function loadShot(eventId) {
    if (shots.current.has(eventId)) return;
    shots.current.set(eventId, null);            // claim it, avoid double fetch
    const { data } = await supabase().rpc("wl_portal_snapshot", {
      p_event_id: eventId,
    });
    if (data && data.image_b64) {
      shots.current.set(eventId,
        `data:${data.content_type || "image/jpeg"};base64,${data.image_b64}`);
      force((n) => n + 1);
    }
  }

  useEffect(() => {
    (data?.recent || []).filter((e) => e.has_snapshot)
      .slice(0, 8).forEach((e) => loadShot(e.event_id));
  }, [data]);

  if (!data && !error) {
    return <div className="center"><p className="muted">Loading your sites...</p></div>;
  }

  const agents = data?.agents || [];
  const health = data?.health || {};
  // Summarise attention rather than list every raw line: many offline agents on
  // one site collapse to "N agents offline on <site>", so the banner reads as a
  // signal, not a wall of near-identical rows.
  const offlineAgents = health.offline_agents || [];
  const silentCameras = health.silent_cameras || [];
  const faults24 = health.faults_24h || [];
  const attentionCount = offlineAgents.length + silentCameras.length + faults24.length;
  const offBySite = {};
  offlineAgents.forEach((a) => { offBySite[a.site] = (offBySite[a.site] || 0) + 1; });
  const attentionItems = [
    ...Object.entries(offBySite).map(
      ([site, n]) => `${n} agent${n > 1 ? "s" : ""} offline on ${site}`),
    ...(silentCameras.length
      ? [`${silentCameras.length} camera${silentCameras.length > 1 ? "s" : ""} silent`] : []),
    ...(faults24.length
      ? [`${faults24.length} fault${faults24.length > 1 ? "s" : ""} in the last 24h`] : []),
  ];
  const online = agents.filter((a) => liveness(a.last_seen_at)[0] === "s-ok").length;
  const bySite = {};
  agents.forEach((a) => { (bySite[a.site] = bySite[a.site] || []).push(a); });
  const withShots = (data?.recent || []).filter((e) => e.has_snapshot).slice(0, 8);
  const maxType = Math.max(1, ...(data?.by_type || []).map((t) => t.count));

  return (
    <div className="shell">
      <Nav active="Overview" email={email} right={
        <>
          <span className="muted hide-sm" style={{ fontSize: "var(--font-size-xs)" }}>
            updated {stamp}
          </span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}
                  style={{ width: "auto", margin: 0 }}>
            <option value={1}>24 hours</option>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
          </select>
        </>
      } />

      <main className="main">
        {error && <div className="err">{error}</div>}

        <div className={"banner" + (attentionCount ? "" : " clear")}>
          {attentionCount ? (
            <>
              <b>{attentionCount} thing{attentionCount > 1 ? "s" : ""} need attention</b>
              <ul>{attentionItems.map((t, i) => <li key={i}>{t}</li>)}</ul>
            </>
          ) : <b>Everything is reporting normally.</b>}
        </div>

        <div className="tiles">
          <div className="tile">
            <div className="n">{data?.totals?.events ?? 0}</div>
            <div className="l">events · {data?.window_days}d</div>
          </div>
          <div className="tile">
            <div className="n">{data?.totals?.cameras ?? 0}</div>
            <div className="l">cameras</div>
          </div>
          <div className="tile">
            <div className="n" style={{ color:
              agents.length && online === 0 ? "var(--color-status-bad-dark)"
              : online < agents.length ? "var(--color-status-warn-dark)"
              : agents.length ? "var(--color-status-ok-dark)" : undefined }}>
              {online}/{agents.length}</div>
            <div className="l">agents online</div>
          </div>
          <div className="tile">
            <div className="n">{data?.totals?.sites ?? 0}</div>
            <div className="l">{(data?.totals?.sites ?? 0) === 1 ? "site" : "sites"}</div>
          </div>
        </div>

        <h2>Sites &amp; agents</h2>
        <div className="panel">
          {agents.length === 0 ? (
            <div className="empty">
              No agent has connected yet.<br />
              <a href="/onboarding/">Get your setup code</a> and run the agent
              on a PC at your site.
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Site / agent</th><th>Status</th><th>Last seen</th>
                  <th>Events</th><th className="hide-sm">Recorder</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(bySite).map(([site, list]) => {
                  const anyOnline = list.some(
                    (a) => liveness(a.last_seen_at)[0] === "s-ok");
                  const siteEvents = list.reduce(
                    (s, a) => s + (a.event_count || 0), 0);
                  return (
                    <Fragment key={site}>
                      <tr style={{ background: "rgba(255,255,255,.025)" }}>
                        <td><b>{site}</b></td>
                        <td><span className={"pill " + (anyOnline ? "s-ok" : "s-bad")}>
                          {anyOnline ? "online" : "offline"}</span></td>
                        <td></td>
                        <td className="mono">{siteEvents}</td>
                        <td className="muted hide-sm">
                          {list.length} agent{list.length > 1 ? "s" : ""}</td>
                      </tr>
                      {list.map((a) => {
                        const [cls, label] = liveness(a.last_seen_at);
                        const device = [a.device_vendor, a.device_model]
                          .filter(Boolean).join(" ");
                        return (
                          <tr key={a.agent_id}>
                            <td className="muted" style={{ paddingLeft: "var(--space-6)" }}>
                              {a.hostname || "unknown PC"}</td>
                            <td><span className={"pill " + cls}>{label}</span></td>
                            <td className="mono">{ago(a.last_seen_at)}</td>
                            <td className="mono">{a.event_count}</td>
                            <td className="muted hide-sm">{device || "not set"}</td>
                          </tr>
                        );
                      })}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <h2>Recent incidents</h2>
        {withShots.length === 0 ? (
          <div className="panel"><div className="empty">
            No incidents with images yet. The first will appear within a
            minute of anything moving.
          </div></div>
        ) : (
          <div className="shots">
            {withShots.map((e) => (
              <div className="shot" key={e.event_id}>
                <img src={shots.current.get(e.event_id) || undefined}
                     alt={`still from ${e.camera || "camera"}`} />
                <div className="meta">
                  <b>{e.camera || "unassigned"}</b> · {e.event_type}
                  <div className="muted">
                    {new Date(e.device_ts).toLocaleString()}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        <h2>Event types</h2>
        <div className="panel">
          {(data?.by_type || []).length === 0 ? (
            <div className="empty">Nothing yet.</div>
          ) : (
            <table>
              <tbody>
                {data.by_type.map((t) => (
                  <tr key={t.event_type}>
                    <td style={{ width: "38%" }}>{t.event_type}</td>
                    <td className="mono" style={{ width: 70 }}>{t.count}</td>
                    <td>
                      <span style={{
                        display: "block", height: 8, borderRadius: 3,
                        background: "var(--wl-ice)", opacity: .85,
                        width: `${Math.round((t.count / maxType) * 100)}%`,
                      }} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <h2>Latest events</h2>
        <div className="panel">
          {(data?.recent || []).length === 0 ? (
            <div className="empty">Nothing yet.</div>
          ) : (
            <table>
              <thead>
                <tr><th>Time</th><th>Site</th><th>Camera</th><th>Type</th></tr>
              </thead>
              <tbody>
                {data.recent.map((e) => (
                  <tr key={e.event_id}>
                    <td className="mono">
                      {new Date(e.device_ts).toLocaleString()}</td>
                    <td>{e.site}</td>
                    <td>{e.camera || "—"}</td>
                    <td>{e.event_type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
