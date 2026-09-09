"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import styles from "./operations.module.css";

// severity/state -> status-pill class (colour is never the only signal; each carries its word)
const SEV_CLS = { critical: "s-bad", attention: "s-warn", info: "s-unk" };
const STATE_CLS = { candidate: "s-unk", open: "s-warn", acknowledged: "s-warn", resolved: "s-ok", dismissed: "s-unk" };
function human(v) { if (!v) return "—"; return String(v).replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()); }
function ago(ts) { if (!ts) return "—"; const s = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000)); if (s < 60) return `${s}s ago`; if (s < 3600) return `${Math.round(s / 60)}m ago`; if (s < 86400) return `${Math.round(s / 3600)}h ago`; return `${Math.round(s / 86400)}d ago`; }
function pill(cls, label) { return <span className={"pill " + cls}>{label}</span>; }

export default function Operations() {
  const [email, setEmail] = useState("");
  const [sites, setSites] = useState([]);
  const [rows, setRows] = useState([]);
  const [ruleMap, setRuleMap] = useState({});
  const [camMap, setCamMap] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [f, setF] = useState({ site: "", type: "", severity: "", state: "", review: "", q: "" });

  const load = useCallback(async () => {
    const guard = await requireTenant();
    if (!guard) return;
    setEmail(guard.session.user.email || "");
    const sb = supabase();
    const s = await sb.rpc("wl_sites");
    if (s.error) { setError(say(s.error)); return; }
    const siteList = s.data || [];
    setSites(siteList);
    // rule + camera name maps (RLS-scoped: a member only ever sees their own tenant's rows)
    const [rulesRes, camsRes] = await Promise.all([
      sb.from("monitoring_rules").select("id,name,rule_type"),
      sb.from("cameras").select("id,name,channel"),
    ]);
    const rm = {}; (rulesRes.data || []).forEach((r) => { rm[r.id] = r; }); setRuleMap(rm);
    const cm = {}; (camsRes.data || []).forEach((c) => { cm[c.id] = c; }); setCamMap(cm);
    const incRes = await Promise.all(siteList.map((si) => sb.rpc("wl_operations_incidents", { p_site_id: si.id })));
    const all = [];
    siteList.forEach((si, i) => { if (incRes[i] && !incRes[i].error) (incRes[i].data || []).forEach((x) => all.push({ ...x, siteId: si.id, siteName: si.name })); });
    all.sort((a, b) => Date.parse(b.opened_at || b.occurred_at) - Date.parse(a.opened_at || a.occurred_at));
    setRows(all); setError("");
  }, []);
  useEffect(() => { load(); }, [load]);

  async function act(id, fn, reason) {
    setBusy(true);
    const sb = supabase();
    const { error: e } = reason !== undefined ? await sb.rpc(fn, { p_id: id, p_reason: reason }) : await sb.rpc(fn, { p_id: id });
    setBusy(false);
    if (e) { setError(/owner|admin|role/i.test(e.message || "") ? "Only owners or admins can update incidents." : say(e)); return; }
    setError(""); load();
  }

  const types = useMemo(() => [...new Set(rows.map((r) => r.incident_type))].sort(), [rows]);
  const filtered = useMemo(() => rows.filter((r) => {
    if (f.site && r.siteId !== f.site) return false;
    if (f.type && r.incident_type !== f.type) return false;
    if (f.severity && r.severity !== f.severity) return false;
    if (f.state && r.status !== f.state) return false;
    if (f.review === "yes" && !r.review_required) return false;
    if (f.q) { const hay = `${r.incident_type} ${ruleMap[r.rule_id]?.name || ""} ${camMap[r.camera_id]?.name || ""} ${r.siteName}`.toLowerCase(); if (!hay.includes(f.q.toLowerCase())) return false; }
    return true;
  }), [rows, f, ruleMap, camMap]);

  const openCount = rows.filter((r) => r.status === "open" || r.status === "candidate" || r.status === "acknowledged").length;
  const reviewCount = rows.filter((r) => r.review_required && r.status !== "resolved" && r.status !== "dismissed").length;

  return <div className="shell"><Nav active="Operations" email={email} /><main className="main">
    <header className="target-page-head"><div><div className="target-eyebrow">Operations Intelligence</div><h1>Exceptions that need a human decision.</h1><p>Your configured operating rules turn camera activity into reviewable incidents — each traceable to the exact rule version, with evidence and provenance. Sensitive classifications are always candidates for human review, never automatic conclusions.</p></div></header>
    {error && <div className="err">{error}</div>}

    <section className={styles.metrics} aria-label="Operations summary">
      <div className={styles.metric}><div className={styles.metricLabel}>Open exceptions</div><div className={styles.metricValue}>{openCount}</div><div className={styles.metricHint}>Candidate, open or acknowledged</div></div>
      <div className={styles.metric}><div className={styles.metricLabel}>Awaiting human review</div><div className={styles.metricValue} style={{ color: reviewCount ? "var(--color-status-warn-dark)" : "var(--color-status-ok-dark)" }}>{reviewCount}</div><div className={styles.metricHint}>Sensitive / review-required</div></div>
      <div className={styles.metric}><div className={styles.metricLabel}>Total incidents</div><div className={styles.metricValue}>{rows.length}</div><div className={styles.metricHint}>Across {sites.length} site{sites.length === 1 ? "" : "s"}</div></div>
    </section>

    <div className={styles.toolbar}>
      <input className={styles.search} placeholder="Search rule, camera, site…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
      <select value={f.site} onChange={(e) => setF({ ...f, site: e.target.value })}><option value="">All sites</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
      <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}><option value="">All types</option>{types.map((t) => <option key={t} value={t}>{human(t)}</option>)}</select>
      <select value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}><option value="">All severities</option><option value="critical">Critical</option><option value="attention">Attention</option><option value="info">Info</option></select>
      <select value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}><option value="">All states</option><option value="candidate">Candidate</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option><option value="dismissed">Dismissed</option></select>
      <label className={styles.chk}><input type="checkbox" checked={f.review === "yes"} onChange={(e) => setF({ ...f, review: e.target.checked ? "yes" : "" })} /> Needs review</label>
    </div>

    <div className="panel"><div className={styles.tableWrap}>
      {filtered.length ? <table><thead><tr><th>When</th><th>Site</th><th>Type</th><th>Camera</th><th>Rule (version)</th><th>Severity</th><th>State</th><th>Review</th><th /></tr></thead>
        <tbody>{filtered.map((r) => {
          const rule = ruleMap[r.rule_id]; const cam = camMap[r.camera_id]; const open = expanded === r.id;
          const canAct = r.status === "candidate" || r.status === "open" || r.status === "acknowledged";
          return <Fragment key={r.id}>
            <tr className={styles.row} onClick={() => setExpanded(open ? null : r.id)}>
              <td title={r.occurred_at ? new Date(r.occurred_at).toLocaleString() : ""}>{ago(r.occurred_at)}</td>
              <td>{r.siteName}</td>
              <td>{human(r.incident_type)}</td>
              <td>{cam ? (cam.name || "ch " + cam.channel) : "—"}</td>
              <td>{rule ? rule.name : <span className="mono">{String(r.rule_id || "").slice(0, 8)}</span>} {pill("s-unk", "v" + r.rule_version)}</td>
              <td>{pill(SEV_CLS[r.severity] || "s-unk", r.severity)}</td>
              <td>{pill(STATE_CLS[r.status] || "s-unk", r.status)}</td>
              <td>{r.review_required ? pill("s-warn", "review") : ""} {r.sensitive ? pill("s-unk", "sensitive") : ""}</td>
              <td className="muted">{open ? "▲" : "▼"}</td>
            </tr>
            {open && <tr className={styles.detailRow}><td colSpan={9}><div className={styles.detail}>
              <div className={styles.detailGrid}>
                <div><span className="muted">Provenance</span><div>Rule <strong>{rule ? rule.name : (r.rule_id || "—")}</strong> · version {r.rule_version}</div></div>
                <div><span className="muted">Occurred</span><div>{r.occurred_at ? new Date(r.occurred_at).toLocaleString() : "—"}</div></div>
                <div><span className="muted">Object</span><div>{human(r.object_class)}</div></div>
                <div><span className="muted">Review</span><div>{r.review_required ? "Human review required" : "Not required"}{r.sensitive ? " · sensitive classification (assistive only)" : ""}</div></div>
              </div>
              <div className={styles.detailBlock}><span className="muted">Evidence</span>{r.evidence && Object.keys(r.evidence).length ? <pre className={styles.json}>{JSON.stringify(r.evidence, null, 2)}</pre> : <div className="muted">No still evidence attached yet. Footage capture is on-demand and bounded (requires a future agent release on this recorder).</div>}</div>
              <div className={styles.detailBlock}><span className="muted">Actions taken</span>{(r.actions || []).length ? <ul className={styles.actions}>{r.actions.map((a, i) => <li key={i}>{human(a.type)} · <span className="muted">{a.actor}</span> · {ago(a.at)}</li>)}</ul> : <div className="muted">No actions recorded.</div>}</div>
              {canAct && <div className={styles.actBtns}>
                {(r.status === "candidate" || r.status === "open") && <button className="small" disabled={busy} onClick={() => act(r.id, "wl_acknowledge_operations_incident")}>Acknowledge</button>}
                <button className="small" disabled={busy} onClick={() => act(r.id, "wl_resolve_operations_incident")}>Resolve</button>
                <button className="ghost small" disabled={busy} onClick={() => { const why = prompt("Reason for dismissing (e.g. reviewed — not a real event):"); if (why != null) act(r.id, "wl_dismiss_operations_incident", why); }}>Dismiss</button>
              </div>}
            </div></td></tr>}
          </Fragment>;
        })}</tbody></table>
        : <div className="empty">{rows.length ? "No incidents match these filters." : "No operations incidents yet. This view fills as your configured rules fire — the live rule engine that produces them requires a future WatchLog agent release."}</div>}
    </div></div>
  </main></div>;
}
