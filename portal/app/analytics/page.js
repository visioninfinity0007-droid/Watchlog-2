"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import styles from "./analytics.module.css";

function metric(label, value, hint) {
  return { label, value: Number(value || 0).toLocaleString(), hint };
}

function linePath(values, width=720, height=220, pad=18) {
  if (!values.length) return "";
  const max = Math.max(1, ...values);
  return values.map((v, i) => {
    const x = pad + (i * (width - pad * 2)) / Math.max(1, values.length - 1);
    const y = height - pad - (v / max) * (height - pad * 2);
    return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

export default function AnalyticsOverview() {
  const [email, setEmail] = useState("");
  const [studio, setStudio] = useState(null);
  const [overview, setOverview] = useState(null);
  const [days, setDays] = useState(7);
  const [siteId, setSiteId] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const guard = await requireTenant();
    if (!guard) return;
    setEmail(guard.session.user.email || "");
    const sb = supabase();
    const [{ data: s, error: se }, { data: o, error: oe }] = await Promise.all([
      sb.rpc("wl_analytics_studio"),
      sb.rpc("wl_analytics_overview", {
        p_days: days,
        p_site_id: siteId || null,
      }),
    ]);
    if (se || oe) {
      setError(say(se || oe));
      setLoading(false);
      return;
    }
    setError("");
    setStudio(s || { sites: [] });
    setOverview(o || { summary: {}, daily: [], by_rule: [] });
    setLoading(false);
  }, [days, siteId]);

  useEffect(() => { load(); }, [load]);

  const sites = studio?.sites || [];
  const daily = overview?.daily || [];
  const summary = overview?.summary || {};
  const selected = siteId ? sites.find((s) => s.id === siteId) : null;
  const hasRetail = selected ? selected.site_type === "retail" : sites.some((s) => s.site_type === "retail");
  const metrics = [
    metric("Visitors in", summary.visitor_in, "Entrance line crossings"),
    metric("Visitors out", summary.visitor_out, "Exit line crossings"),
    metric("Vehicles in", summary.vehicles_in, "Cars + motorcycles"),
    metric("Zone entries", summary.zone_entries, "Configured areas"),
    metric("After hours", summary.after_hours, "Outside configured schedules"),
    ...(hasRetail ? [metric("Checkout peak", summary.checkout_peak, "People present in checkout zone")]: []),
  ];

  const coverage = useMemo(() => sites.flatMap((s) => (s.cameras || []).map((c) => ({
    site: s.name,
    camera: c.name || `Camera ${c.channel}`,
    purpose: c.purpose,
    rules: (c.rules || []).filter((r) => r.enabled),
  }))), [sites]);

  const visitorPath = linePath(daily.map((d) => Number(d.visitor_in || 0)));
  const vehiclePath = linePath(daily.map((d) => Number(d.vehicles_in || 0)));

  return (
    <div className="shell">
      <Nav active="Analytics" email={email} right={
        <>
          <select value={siteId} onChange={(e) => setSiteId(e.target.value)}
                  aria-label="Filter analytics by site" style={{ width: "auto", margin: 0 }}>
            <option value="">All sites</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}
                  aria-label="Analytics date range" style={{ width: "auto", margin: 0 }}>
            <option value={1}>24 hours</option>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
          </select>
        </>
      } />

      <main className="main">
        <div className={styles.pageHead}>
          <div>
            <div className={styles.subnav}>
              <a className={styles.current} href="/analytics/">Overview</a>
              <a href="/analytics/studio/">Analytics Studio</a>
            </div>
            <h1 style={{ marginTop: 22 }}>Site intelligence</h1>
            <p>See the operational signals your cameras are responsible for, not just a list of motion events.</p>
          </div>
          <div className={styles.actions}>
            <a className={styles.primaryLink} href="/analytics/studio/">Configure monitoring</a>
          </div>
        </div>

        {error && <div className="err">{error}</div>}
        {loading ? <div className={styles.card}>Loading analytics...</div> : <>
          <section className={styles.grid5} aria-label="Analytics summary">
            {metrics.map((m) => <div className={styles.metric} key={m.label}>
              <div className={styles.metricLabel}>{m.label}</div>
              <div className={styles.metricValue}>{m.value}</div>
              <div className={styles.metricHint}>{m.hint}</div>
            </div>)}
          </section>

          <section className={styles.twoCol}>
            <div className={styles.card}>
              <h2>Flow by day</h2>
              {daily.length ? <>
                <svg className={styles.chart} viewBox="0 0 720 220" role="img"
                     aria-label="Daily visitor and vehicle flow">
                  {[45,90,135,180].map((y) => <line key={y} x1="18" x2="702" y1={y} y2={y}
                    className={styles.chartGrid} />)}
                  <path d={visitorPath} className={styles.chartVisitor} />
                  <path d={vehiclePath} className={styles.chartVehicle} />
                </svg>
                <div className={styles.legend}>
                  <span><i style={{ background: "var(--color-violet-bright)" }} />Visitors in</span>
                  <span><i style={{ background: "#72d4ff" }} />Vehicles in</span>
                </div>
              </> : <div className={styles.empty}>
                No analytics measurements yet. Configure a camera and let the Site Agent collect the first crossings or zone activity.
              </div>}
            </div>

            <div className={styles.card}>
              <h2>Monitoring coverage</h2>
              <div className={styles.coverage}>
                {coverage.length ? coverage.slice(0, 8).map((c) => <div className={styles.coverageRow}
                  key={`${c.site}-${c.camera}`}>
                  <span className={c.rules.length ? styles.dot : `${styles.dot} ${styles.dotMuted}`} />
                  <div className={styles.grow}>
                    <strong>{c.camera}</strong>
                    <small>{c.site} · {c.rules.length} monitoring rule{c.rules.length === 1 ? "" : "s"}</small>
                  </div>
                  <span className={styles.badge}>{(c.purpose || "custom").replaceAll("_", " ")}</span>
                </div>) : <div className={styles.empty}>No cameras discovered yet.</div>}
              </div>
            </div>
          </section>

          <section className={styles.card}>
            <h2>Most active monitoring rules</h2>
            {(overview?.by_rule || []).length ? <table>
              <thead><tr><th>Monitoring</th><th>Site</th><th>Camera</th><th>Type</th><th>Measurements</th></tr></thead>
              <tbody>{overview.by_rule.map((r) => <tr key={r.rule_id}>
                <td><b>{r.name}</b></td><td>{r.site}</td><td>{r.camera || "Camera"}</td>
                <td>{String(r.rule_type || "").replaceAll("_", " ")}</td>
                <td className="mono">{Number(r.count || 0).toLocaleString()}</td>
              </tr>)}</tbody>
            </table> : <div className={styles.empty}>
              Your configured monitoring goals will appear here as the Site Agent records measurements.
            </div>}
          </section>
        </>}
      </main>
    </div>
  );
}
