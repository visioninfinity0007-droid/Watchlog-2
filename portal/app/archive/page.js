"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import styles from "./archive.module.css";

const STATUS_CLS = { requested: "s-unk", locating: "s-warn", retrieving: "s-warn", analyzing: "s-warn", complete: "s-ok", failed: "s-bad", cancelled: "s-unk" };
const PROVENANCE = "Recovered from recorder archive";
function human(v) { if (!v) return "—"; return String(v).replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()); }
function when(ts) { return ts ? new Date(ts).toLocaleString() : "—"; }
function localInput(d) { const p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; }

export default function Archive() {
  const [email, setEmail] = useState("");
  const [sites, setSites] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [rules, setRules] = useState([]);
  const [scans, setScans] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState(null);
  const now = useMemo(() => new Date(), []);
  const [form, setForm] = useState({ site: "", cams: [], rules: [], from: localInput(new Date(now.getTime() - 86400000)), to: localInput(now) });

  const load = useCallback(async () => {
    const guard = await requireTenant(); if (!guard) return;
    setEmail(guard.session.user.email || "");
    const sb = supabase();
    const [s, cam, rl] = await Promise.all([
      sb.rpc("wl_sites"),
      sb.from("cameras").select("id,name,channel,site_id"),
      sb.from("monitoring_rules").select("id,name,rule_type,site_id"),
    ]);
    if (s.error) { setError(say(s.error)); return; }
    const siteList = s.data || [];
    setSites(siteList); setCameras(cam.data || []); setRules(rl.data || []);
    setForm((f) => ({ ...f, site: f.site || (siteList[0]?.id || "") }));
    const sc = await sb.from("archive_scans").select("*").order("requested_at", { ascending: false });
    setScans(sc.data || []);
  }, []);
  useEffect(() => { load(); }, [load]);

  const siteCams = cameras.filter((c) => c.site_id === form.site);
  const siteRules = rules.filter((r) => r.site_id === form.site);

  async function submit() {
    setError("");
    if (!form.site) { setError("Choose a site."); return; }
    if (!form.cams.length) { setError("Choose at least one camera."); return; }
    const from = new Date(form.from), to = new Date(form.to);
    if (!(to > from)) { setError("End must be after start."); return; }
    if ((to - from) > 7 * 86400000) { setError("Scan window may not exceed 7 days."); return; }
    setBusy(true);
    const { error: e } = await supabase().rpc("wl_request_archive_scan", {
      p_site_id: form.site, p_camera_ids: form.cams, p_from: from.toISOString(), p_to: to.toISOString(),
      p_rule_ids: form.rules.length ? form.rules : null,
    });
    setBusy(false);
    if (e) { setError(/owner|admin|role/i.test(e.message || "") ? "Only owners or admins can request a scan." : say(e)); return; }
    setForm((f) => ({ ...f, cams: [], rules: [] }));
    load();
  }

  async function open(id) {
    const { data, error: e } = await supabase().rpc("wl_archive_scan", { p_scan_id: id });
    if (e) { setError(say(e)); return; }
    setDetail(data || null);
  }
  function toggle(list, id) { return list.includes(id) ? list.filter((x) => x !== id) : [...list, id]; }

  return <div className="shell"><Nav active="Archive" email={email} /><main className="main">
    <header className="target-page-head"><div><div className="target-eyebrow">Archive / Historical Scan</div><h1>Re‑examine recorded footage.</h1><p>Select cameras and a time range, then apply your operating rules to footage already on the recorder. Recovered results are always labelled <b>“{PROVENANCE}”</b> and kept separate from live monitoring — WatchLog never claims it watched live while the agent was offline.</p></div></header>
    {error && <div className="err">{error}</div>}

    <div className={styles.note}>Recorded‑footage retrieval and offline analysis run on the site agent and <b>require a future WatchLog agent release</b>. You can request scans now; each is queued and its status shown honestly until that capability ships on the recorder.</div>

    <section className={styles.grid}>
      <div className="panel"><div className={styles.pTitle}>Request a scan</div>
        <label className={styles.field}><span>Site</span><select value={form.site} onChange={(e) => setForm({ ...form, site: e.target.value, cams: [], rules: [] })}>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label className={styles.field}><span>Cameras</span>
          <div className={styles.checks}>{siteCams.length ? siteCams.map((c) => <label key={c.id} className={styles.check}><input type="checkbox" checked={form.cams.includes(c.id)} onChange={() => setForm({ ...form, cams: toggle(form.cams, c.id) })} /> {c.name || "Camera " + c.channel} <span className="muted">ch {c.channel}</span></label>) : <span className="muted">No cameras on this site.</span>}</div>
        </label>
        <div className={styles.row2}>
          <label className={styles.field}><span>Start</span><input type="datetime-local" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} /></label>
          <label className={styles.field}><span>End</span><input type="datetime-local" value={form.to} onChange={(e) => setForm({ ...form, to: e.target.value })} /></label>
        </div>
        <label className={styles.field}><span>Rules / SOPs <span className="muted">(optional — all enabled if none chosen)</span></span>
          <div className={styles.checks}>{siteRules.length ? siteRules.map((r) => <label key={r.id} className={styles.check}><input type="checkbox" checked={form.rules.includes(r.id)} onChange={() => setForm({ ...form, rules: toggle(form.rules, r.id) })} /> {r.name} <span className="muted">{human(r.rule_type)}</span></label>) : <span className="muted">No rules configured on this site.</span>}</div>
        </label>
        <button disabled={busy} onClick={submit}>{busy ? "Requesting…" : "Request scan"}</button>
      </div>

      <div className="panel"><div className={styles.pTitle}>Scans</div><div className={styles.tableWrap}>
        {scans.length ? <table><thead><tr><th>Requested</th><th>Window</th><th>Cameras</th><th>Status</th><th /></tr></thead>
          <tbody>{scans.map((s) => <tr key={s.id}><td>{when(s.requested_at)}</td><td className="muted">{when(s.from_ts)} → {when(s.to_ts)}</td><td>{(s.camera_ids || []).length}</td><td><span className={"pill " + (STATUS_CLS[s.status] || "s-unk")}>{s.status}</span></td><td><button className="ghost small" onClick={() => open(s.id)}>View</button></td></tr>)}</tbody></table>
          : <div className="empty">No scans requested yet.</div>}
      </div></div>
    </section>

    {detail && <section className={styles.detail}>
      <div className={styles.sectionHead}><h2>Scan results</h2><button className="ghost small" onClick={() => setDetail(null)}>Close</button></div>
      <div className="panel">
        <div className={styles.meta}><div><span className="muted">Window</span><b>{when(detail.scan.from_ts)} → {when(detail.scan.to_ts)}</b></div><div><span className="muted">Status</span><span className={"pill " + (STATUS_CLS[detail.scan.status] || "s-unk")}>{detail.scan.status}</span></div><div><span className="muted">Provenance</span><b>recorder_archive</b></div></div>
        <div className={styles.tableWrap}>{(detail.results || []).length ? <table><thead><tr><th>Recovered at</th><th>Type</th><th>Confidence</th><th>Provenance</th></tr></thead>
          <tbody>{detail.results.map((r) => <tr key={r.id}><td>{when(r.recovered_at)}</td><td>{human(r.result_type)}</td><td>{r.confidence != null ? r.confidence : "—"}</td><td><span className="pill s-unk">{r.provenance_label}</span></td></tr>)}</tbody></table>
          : <div className="empty">No recovered results yet. Results appear here once the site agent processes this scan (requires a future agent release), each labelled “{PROVENANCE}”.</div>}</div>
      </div>
    </section>}
  </main></div>;
}
