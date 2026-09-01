"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import ui from "../portal.module.css";

const CHANNELS = [
  ["whatsapp", "WhatsApp"],
  ["email", "Email"],
  ["both", "WhatsApp + Email"],
];

function statusPill(s) {
  const cls = s === "sent" ? "s-ok" : s === "failed" ? "s-bad" : "s-unk";
  return <span className={"pill " + cls}>{s}</span>;
}
function channelLabel(v) { return CHANNELS.find(([k]) => k === v)?.[1] || v; }

export default function Reports() {
  const [email, setEmail] = useState("");
  const [recips, setRecips] = useState(null);
  const [sites, setSites] = useState([]);
  const [deliveries, setDeliveries] = useState([]);
  const [role, setRole] = useState("viewer");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ whatsapp: "", email: "", channel: "whatsapp", name: "", site_id: "" });

  const load = useCallback(async () => {
    const g = await requireTenant();
    if (!g) return;
    setEmail(g.session.user.email || "");
    const sb = supabase();
    const [r, s, d, me] = await Promise.all([
      sb.rpc("wl_recipients"), sb.rpc("wl_sites"), sb.rpc("wl_deliveries", { p_days: 30 }), sb.rpc("wl_my_role"),
    ]);
    if (r.error || s.error || d.error || me.error) { setErr(say(r.error || s.error || d.error || me.error)); return; }
    setRecips(r.data || []); setSites(s.data || []); setDeliveries(d.data || []); setRole(me.data || "viewer");
  }, []);
  useEffect(() => { load(); }, [load]);

  const canManage = role === "owner" || role === "admin";
  const active = (recips || []).filter((r) => r.enabled).length;
  const sent = deliveries.filter((d) => d.status === "sent").length;
  const failed = deliveries.filter((d) => d.status === "failed").length;

  async function add(e) {
    e.preventDefault(); setBusy(true); setErr(""); setNote("");
    const { data, error } = await supabase().rpc("wl_add_recipient_v2", {
      p_whatsapp: form.channel === "email" ? null : form.whatsapp.trim(),
      p_email: form.channel === "whatsapp" ? null : form.email.trim(),
      p_channel: form.channel, p_name: form.name.trim() || null, p_site_id: form.site_id || null,
    });
    setBusy(false);
    if (error) { setErr(say(error)); return; }
    if (data?.created === false) setNote(data.note || "Those delivery endpoints already exist.");
    else { setNote(form.channel === "both" ? "WhatsApp and email delivery added." : "Recipient added."); setForm({ whatsapp: "", email: "", channel: "whatsapp", name: "", site_id: "" }); }
    load();
  }
  async function toggle(id, enabled) { const { error } = await supabase().rpc("wl_set_recipient", { p_id: id, p_enabled: !enabled }); if (error) setErr(say(error)); else load(); }
  async function remove(id) { if (!confirm("Remove this report delivery endpoint?")) return; const { error } = await supabase().rpc("wl_remove_recipient", { p_id: id }); if (error) setErr(say(error)); else load(); }

  return <div className="shell">
    <Nav active="Reports" email={email} />
    <main className="main">
      <header className={ui.pageHead}>
        <div><div className={ui.eyebrow}>Daily reporting</div><h1>The morning brief, delivered.</h1><p>Compose the useful part of each site's night once, then deliver it by WhatsApp, email, or both with a complete delivery record.</p></div>
      </header>
      {err && <div className="err">{err}</div>}{note && <div className="ok-note">{note}</div>}

      <section className={ui.metricGrid} aria-label="Reporting summary">
        <div className={ui.metric}><div className={ui.metricValue}>{active}</div><div className={ui.metricLabel}>Active endpoints</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{sites.length}</div><div className={ui.metricLabel}>Sites covered</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{sent}</div><div className={ui.metricLabel}>Sent, last 30 days</div></div>
        <div className={ui.metric}><div className={ui.metricValue}>{failed}</div><div className={ui.metricLabel}>Failed, last 30 days</div></div>
      </section>

      <div className={ui.sectionHead}><div><h2>The daily report</h2><p>A preview of the information recipients receive each morning.</p></div></div>
      <section className={ui.heroGrid}>
        <div className={ui.featureCard}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14}}><span className={ui.statusDot}/><b>WatchLog daily report</b><span className="muted" style={{marginLeft:"auto",fontSize:"var(--font-size-xs)"}}>07:00, site time</span></div>
          <div style={{padding:18,border:"1px solid var(--color-line-dark)",borderRadius:12,background:"var(--color-canvas)"}}>
            <div className="muted" style={{fontSize:"var(--font-size-sm)",marginBottom:14}}>Warehouse, Tuesday 2 September</div>
            <b>Overnight</b><ul style={{margin:"6px 0 16px",paddingLeft:"1.1rem",fontSize:"var(--font-size-sm)"}}><li>18 incidents: 12 person, 5 vehicle, 1 motorcycle</li><li>6 after hours, between 21:00 and 06:00</li><li>First at 21:14, last at 05:47</li></ul>
            <b>Camera health</b><ul style={{margin:"6px 0 0",paddingLeft:"1.1rem",fontSize:"var(--font-size-sm)"}}><li>14 of 15 cameras reporting</li><li>Rear Perimeter silent since 01:00</li></ul>
          </div>
          <p style={{fontSize:"var(--font-size-xs)",marginBottom:0}}>Example layout only. Live reports are built from each site's own incidents, health and analytics data.</p>
        </div>
        <div className={ui.card}>
          <h3>One report, the right channel</h3><p>WhatsApp and email are independent delivery endpoints. Choosing both stores and delivers to both addresses correctly; no address is reused across providers.</p>
          <div className={ui.splitList}>
            <div className={ui.listRow}><span className={ui.statusDot}/><div><b>Per-site recipients</b><small>Branch teams can receive only their own site.</small></div></div>
            <div className={ui.listRow}><span className={ui.statusDot}/><div><b>Site-local time</b><small>Morning and after-hours mean the site's own timezone.</small></div></div>
            <div className={ui.listRow}><span className={ui.statusDot}/><div><b>Delivery history</b><small>Sent, skipped and failed attempts remain visible below.</small></div></div>
          </div>
        </div>
      </section>

      <div className={ui.sectionHead}><div><h2>Recipients</h2><p>Choose who receives each site's daily brief and where it should arrive.</p></div></div>
      {canManage ? <div className={ui.card}>
        <form onSubmit={add}>
          <div className="row">
            <div className="field" style={{maxWidth:210}}><label>Channel</label><select value={form.channel} onChange={(e)=>setForm({...form,channel:e.target.value})}>{CHANNELS.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></div>
            <div className="field"><label>Name</label><input value={form.name} placeholder="Warehouse manager" onChange={(e)=>setForm({...form,name:e.target.value})}/></div>
            <div className="field"><label>Site</label><select value={form.site_id} onChange={(e)=>setForm({...form,site_id:e.target.value})}><option value="">All sites</option>{sites.map((s)=><option key={s.id} value={s.id}>{s.name}</option>)}</select></div>
          </div>
          <div className="row" style={{marginTop:14}}>
            {form.channel !== "email" && <div className="field"><label>WhatsApp number</label><input required value={form.whatsapp} inputMode="tel" placeholder="923001234567" onChange={(e)=>setForm({...form,whatsapp:e.target.value})}/></div>}
            {form.channel !== "whatsapp" && <div className="field"><label>Email address</label><input required type="email" value={form.email} placeholder="name@company.com" onChange={(e)=>setForm({...form,email:e.target.value})}/></div>}
            <button className="small" disabled={busy} style={{width:"auto"}}>{busy ? "Adding..." : form.channel === "both" ? "Add both channels" : "Add recipient"}</button>
          </div>
          <p className="muted" style={{fontSize:"var(--font-size-xs)",margin:"10px 0 0"}}>WhatsApp numbers use international digits, for example 923001234567. Email and WhatsApp are validated independently.</p>
        </form>
      </div> : <div className={ui.callout}><span className={ui.statusDot}/><div><strong>Read-only access</strong><p>Owners and admins manage report recipients. You can review recipients and delivery history.</p></div></div>}

      <div className="panel"><div className={ui.tableWrap}>
        {recips === null ? <div className="empty">Loading...</div> : recips.length === 0 ? <div className="empty">No recipients yet. Add a delivery endpoint to start the daily report.</div> : <table><thead><tr><th>Recipient</th><th>Channel</th><th>Destination</th><th>Site</th><th>Status</th><th></th></tr></thead><tbody>{recips.map((r)=><tr key={r.id}><td><b>{r.name || "Recipient"}</b></td><td>{channelLabel(r.channel)}</td><td className="mono">{r.destination}</td><td>{r.site || "All sites"}</td><td><span className={"pill "+(r.enabled?"s-ok":"s-unk")}>{r.enabled?"active":"paused"}</span></td><td>{canManage && <div className={ui.inlineActions}><button className="ghost small" onClick={()=>toggle(r.id,r.enabled)}>{r.enabled?"Pause":"Resume"}</button><button className="btn-danger" onClick={()=>remove(r.id)}>Remove</button></div>}</td></tr>)}</tbody></table>}
      </div></div>

      <div className={ui.sectionHead}><div><h2>Delivery history</h2><p>Every attempted report remains visible for operational follow-up.</p></div></div>
      <div className="panel"><div className={ui.tableWrap}>{deliveries.length===0?<div className="empty">No reports sent yet. Delivery history will appear here after the first scheduled run.</div>:<table><thead><tr><th>Date</th><th>Site</th><th>Channel</th><th>To</th><th>Status</th><th>Events</th></tr></thead><tbody>{deliveries.map((d,i)=><tr key={`${d.date}-${d.channel}-${d.destination}-${i}`}><td className="mono">{d.date}</td><td>{d.site}</td><td>{channelLabel(d.channel)}</td><td className="muted">{d.destination}</td><td>{statusPill(d.status)}{d.error&&<div className="muted" style={{fontSize:"var(--font-size-xs)",marginTop:4}}>{d.error}</div>}</td><td className="mono">{d.events??"-"}</td></tr>)}</tbody></table>}</div></div>
    </main>
  </div>;
}
