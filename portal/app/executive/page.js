"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import styles from "./executive.module.css";

const RECORD_MAP = { recording: ["s-ok", "recording"], not_recording: ["s-bad", "not recording"], unknown: ["s-unk", "unknown"] };
const STORAGE_MAP = { ok: ["s-ok", "healthy"], degraded: ["s-warn", "low space"], fault: ["s-bad", "fault"], unknown: ["s-unk", "unknown"] };
const PERIODS = [["24h", 1], ["7 days", 7], ["30 days", 30], ["90 days", 90]];
function human(v) { if (v === null || v === undefined || v === "") return "—"; return String(v).replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()); }
function hrs(sec) { return ((Number(sec) || 0) / 3600).toFixed(1) + "h"; }
function pill(cls, label) { return <span className={"pill " + cls}>{label}</span>; }
function statePill(map, key) { const [cls, label] = map[key] || map.unknown; return pill(cls, label); }
function reachPill(v, ok, bad) { if (v === true) return pill("s-ok", ok); if (v === false) return pill("s-bad", bad); return pill("s-unk", "unknown"); }
function Chips({ obj, cls }) { const e = Object.entries(obj || {}); if (!e.length) return <span className="muted">none</span>; return <span className={styles.chips}>{e.map(([k, v]) => <span key={k} className={styles.chip}>{human(k)} <b>{v}</b></span>)}</span>; }

export default function Executive() {
  const [email, setEmail] = useState("");
  const [sites, setSites] = useState([]);
  const [site, setSite] = useState("");
  const [days, setDays] = useState(7);
  const [rep, setRep] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const loadSites = useCallback(async () => {
    const guard = await requireTenant(); if (!guard) return;
    setEmail(guard.session.user.email || "");
    const s = await supabase().rpc("wl_sites");
    if (s.error) { setError(say(s.error)); return; }
    setSites(s.data || []);
    if ((s.data || []).length && !site) setSite(s.data[0].id);
  }, [site]);
  useEffect(() => { loadSites(); }, [loadSites]);

  const loadReport = useCallback(async () => {
    if (!site) return;
    setLoading(true);
    const to = new Date();
    const from = new Date(to.getTime() - days * 86400000);
    const { data, error: e } = await supabase().rpc("wl_operations_report", { p_site_id: site, p_from: from.toISOString(), p_to: to.toISOString() });
    setLoading(false);
    if (e) { setError(say(e)); setRep(null); return; }
    setError(""); setRep(data || null);
  }, [site, days]);
  useEffect(() => { loadReport(); }, [loadReport]);

  const c = rep?.completeness, rel = rep?.reliability, sec = rep?.security, ops = rep?.operations;
  const covClass = useMemo(() => { if (!c || c.coverage_pct === null || c.coverage_pct === undefined) return "s-unk"; const p = Number(c.coverage_pct); return p >= 95 ? "s-ok" : p >= 80 ? "s-warn" : "s-bad"; }, [c]);

  return <div className="shell"><Nav active="Executive" email={email} right={<>
    <select value={site} onChange={(e) => setSite(e.target.value)} style={{ width: "auto", margin: 0 }}>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
    <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: "auto", margin: 0 }}>{PERIODS.map(([l, d]) => <option key={d} value={d}>{l}</option>)}</select>
  </>} /><main className="main">
    <header className="target-page-head"><div><div className="target-eyebrow">Executive Report</div><h1>{rep?.site?.name || "Operations"} — management summary.</h1><p>Reliability, security and operations for the selected period, every figure traceable to a health record, incident or evidence item. Unverified time is shown as unverified — never counted as uptime or downtime.</p></div></header>
    {error && <div className="err">{error}</div>}
    {loading && !rep ? <div className="panel"><div className="empty">Building report…</div></div> : !rep ? <div className="panel"><div className="empty">Select a site to build its report.</div></div> : <>

      {/* Completeness — first-class, honest */}
      <section className={styles.coverage + " " + styles["cov_" + covClass.replace("s-", "")]}>
        <div><div className={styles.covLabel}>Monitoring coverage</div>
          <div className={styles.covValue}>{c.coverage_pct === null || c.coverage_pct === undefined ? "Not verified" : c.coverage_pct + "%"}</div>
          <div className="muted" style={{ fontSize: "var(--font-size-sm)" }}>{c.note}</div></div>
        <div className={styles.covSplit}>
          <div><span className="muted">Monitored</span><b>{hrs(c.monitored_seconds)}</b></div>
          <div><span className="muted">Unverified</span><b>{hrs(c.unverified_seconds)}{c.unverified_pct != null ? ` · ${c.unverified_pct}%` : ""}</b></div>
          <div><span className="muted">Wall clock</span><b>{hrs(c.wall_seconds)}</b></div>
        </div>
      </section>

      {/* Reliability */}
      <div className={styles.sectionHead}><h2>Reliability</h2><p>Availability measured over monitored time.</p></div>
      <section className={styles.tiles}>
        <div className={styles.tile}><div className={styles.tLabel}>Cameras</div><div className={styles.tVal}>{rel.cameras.total - rel.cameras.offline_now - rel.cameras.unknown_now}/{rel.cameras.total}</div><div className={styles.tHint}>{rel.cameras.offline_now} offline · {rel.cameras.unknown_now} not verified</div></div>
        <div className={styles.tile}><div className={styles.tLabel}>Camera faults opened</div><div className={styles.tVal}>{rel.cameras.offline_faults_opened}</div><div className={styles.tHint}>in this period</div></div>
        <div className={styles.tile}><div className={styles.tLabel}>Site connection unavailable</div><div className={styles.tVal}>{hrs(rel.agent_unreachable.seconds)}</div><div className={styles.tHint}>{rel.agent_unreachable.intervals} interval(s)</div></div>
        <div className={styles.tile}><div className={styles.tLabel}>Unverified</div><div className={styles.tVal}>{hrs(rel.unverified.seconds)}</div><div className={styles.tHint}>{rel.unverified.intervals} interval(s) — excluded from availability</div></div>
      </section>
      <div className="panel"><div className={styles.tableWrap}>
        {rel.recorders.length ? <table><thead><tr><th>Recorder</th><th>Reachable</th><th>Credentials</th><th>Recording</th><th>Storage</th></tr></thead>
          <tbody>{rel.recorders.map((r) => <tr key={r.agent_id}><td>Camera system</td><td>{reachPill(r.reachable, "reachable", "unreachable")}</td><td>{reachPill(r.auth_ok, "authenticated", "auth failed")}</td><td>{statePill(RECORD_MAP, r.recording_state)}</td><td>{statePill(STORAGE_MAP, r.storage_state)}</td></tr>)}</tbody></table>
          : <div className="empty">No recorder health recorded for this site yet.</div>}
      </div></div>

      {/* Security */}
      <div className={styles.sectionHead}><h2>Security</h2><p>Incidents, recorder events and evidence availability.</p></div>
      <section className={styles.twoCol}>
        <div className="panel"><div className={styles.pTitle}>Operations incidents</div>
          <div className={styles.big}>{sec.incidents.total}<small>{sec.incidents.review_required} awaiting review</small></div>
          <div className={styles.kv}><span className="muted">By severity</span><Chips obj={sec.incidents.by_severity} /></div>
          <div className={styles.kv}><span className="muted">By state</span><Chips obj={sec.incidents.by_status} /></div>
          {sec.incidents.total ? <a className={styles.drill} href="/operations/">Open in Operations →</a> : null}
        </div>
        <div className="panel"><div className={styles.pTitle}>Recorder events &amp; evidence</div>
          <div className={styles.kv}><span className="muted">Recorder events</span><b>{sec.native_events.total}</b></div>
          <div className={styles.kv}><span className="muted">By type</span><Chips obj={sec.native_events.by_type} /></div>
          <div className={styles.kv}><span className="muted">Footage requests</span><b>{sec.evidence.clip_requests}</b></div>
          <div className={styles.kv}><span className="muted">Evidence by state</span><Chips obj={sec.evidence.by_status} /></div>
        </div>
      </section>

      {/* Operations / SOP */}
      <div className={styles.sectionHead}><h2>Operations</h2><p>Configured operating-rule (SOP) exceptions.</p></div>
      <section className={styles.tiles}>
        <div className={styles.tile}><div className={styles.tLabel}>SOP exceptions</div><div className={styles.tVal}>{ops.sop_violations.total}</div><div className={styles.tHint}>total</div></div>
        <div className={styles.tile}><div className={styles.tLabel}>Dwell / wait</div><div className={styles.tVal}>{ops.dwell_wait}</div><div className={styles.tHint}>dwell + queue breaches</div></div>
        <div className={styles.tile}><div className={styles.tLabel}>Presence / absence</div><div className={styles.tVal}>{ops.presence_absence}</div><div className={styles.tHint}>expected-activity exceptions</div></div>
        <div className={styles.tile}><div className={styles.tLabel}>Occupancy / schedule</div><div className={styles.tVal}>{ops.occupancy + ops.schedule}</div><div className={styles.tHint}>{ops.occupancy} occupancy · {ops.schedule} schedule</div></div>
      </section>
      <div className="panel"><div className={styles.pTitle}>SOP exceptions by type</div><Chips obj={ops.sop_violations.by_type} />{ops.sop_violations.total ? <a className={styles.drill} href="/operations/"> — review in Operations →</a> : null}</div>
    </>}
  </main></div>;
}
