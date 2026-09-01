"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";

// Enum values like "line_crossing" are for the database, not the operator.
function humanType(t) {
  if (!t) return "Event";
  return t.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export default function Incidents() {
  const [email, setEmail] = useState("");
  const [rows, setRows] = useState(null);
  const [sites, setSites] = useState([]);
  const [err, setErr] = useState("");
  const [days, setDays] = useState(7);
  const [site, setSite] = useState("");
  const [type, setType] = useState("");
  const shots = useRef(new Map());
  const [, force] = useState(0);

  const load = useCallback(async (d, s, t) => {
    const g = await requireTenant();
    if (!g) return;
    setEmail(g.session.user.email || "");
    const sb = supabase();
    const [inc, si] = await Promise.all([
      sb.rpc("wl_incidents", { p_days: d, p_site: s || null, p_type: t || null }),
      sb.rpc("wl_sites"),
    ]);
    if (inc.error) { setErr(say(inc.error)); return; }
    setErr("");
    setRows(inc.data || []);
    setSites(si.data || []);
  }, []);

  useEffect(() => { load(days, site, type); }, [days, site, type, load]);

  async function loadShot(eventId) {
    if (shots.current.has(eventId)) return;
    shots.current.set(eventId, null);
    const { data } = await supabase().rpc("wl_portal_snapshot", { p_event_id: eventId });
    if (data && data.image_b64) {
      shots.current.set(eventId, `data:${data.content_type || "image/jpeg"};base64,${data.image_b64}`);
      force((n) => n + 1);
    }
  }
  useEffect(() => {
    (rows || []).filter((r) => r.has_snapshot).slice(0, 24).forEach((r) => loadShot(r.event_id));
  }, [rows]);

  // type filter options derived from what is present (no extra RPC)
  const types = Array.from(new Set((rows || []).map((r) => r.event_type))).sort();

  return (
    <div className="shell">
      <Nav active="Incidents" email={email} right={
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}
                style={{ width: "auto", margin: 0 }}>
          <option value={1}>24 hours</option>
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
          <option value={90}>90 days</option>
        </select>
      } />
      <main className="main">
        {err && <div className="err">{err}</div>}

        <h2>Filter</h2>
        <div className="card">
          <div className="row">
            <div className="field" style={{ maxWidth: 220 }}>
              <label>Site</label>
              <select value={site} onChange={(e) => setSite(e.target.value)}>
                <option value="">All sites</option>
                {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <div className="field" style={{ maxWidth: 200 }}>
              <label>Type</label>
              <select value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">All types</option>
                {types.map((t) => <option key={t} value={t}>{humanType(t)}</option>)}
              </select>
            </div>
            <div className="field" style={{ alignSelf: "flex-end" }}>
              <span className="muted" style={{ fontSize: "var(--font-size-sm)" }}>
                {rows === null ? "" : `${rows.length} incident${rows.length === 1 ? "" : "s"}`}
              </span>
            </div>
          </div>
        </div>

        <h2>Incident history</h2>
        {rows === null ? (
          <div className="panel"><div className="empty">Loading…</div></div>
        ) : rows.length === 0 ? (
          <div className="panel"><div className="empty">
            No incidents in this window. Valid events (person, vehicle, motorcycle) appear here after
            the on-site filter accepts them.
          </div></div>
        ) : (
          <div className="panel">
            <table>
              <thead>
                <tr><th>Still</th><th>Time</th><th>Site</th><th>Camera</th><th>Type</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.event_id}>
                    <td style={{ width: 96 }}>
                      {r.has_snapshot && shots.current.get(r.event_id) ? (
                        <img src={shots.current.get(r.event_id)}
                             alt={`still from ${r.camera || "camera"}`}
                             style={{ width: 84, height: 47, objectFit: "cover",
                                      borderRadius: 6, background: "var(--color-canvas)",
                                      display: "block" }} />
                      ) : (
                        <span style={{ display: "grid", placeItems: "center",
                                       width: 84, height: 47, borderRadius: 6,
                                       background: "var(--color-canvas)",
                                       border: "1px solid var(--color-line-dark)",
                                       color: "var(--color-muted-dark)",
                                       fontSize: "var(--font-size-xs)" }}>
                          {r.has_snapshot ? "loading" : "no still"}
                        </span>
                      )}
                    </td>
                    <td className="mono">{new Date(r.device_ts).toLocaleString()}</td>
                    <td>{r.site}</td>
                    <td>{r.camera || <span className="muted">unassigned</span>}</td>
                    <td><span style={{ fontSize: "var(--font-size-xs)", fontWeight: 600,
                                       padding: "3px 9px", borderRadius: 999,
                                       background: "rgba(114,212,255,.12)",
                                       color: "var(--wl-ice)" }}>
                      {humanType(r.event_type)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
