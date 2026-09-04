"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import ui from "../portal.module.css";

const CHANNELS = [["whatsapp","WhatsApp"],["email","Email"],["both","WhatsApp + Email"]];
function statusPill(s){const cls=s==="sent"?"s-ok":s==="failed"?"s-bad":"s-unk";return <span className={"pill "+cls}>{s}</span>;}
function channelLabel(v){return CHANNELS.find(([k])=>k===v)?.[1]||v;}
function endpoint(r){return r.whatsapp_destination||r.email_destination||r.destination||"-";}
function humanDate(v){if(!v)return"-";try{return new Date(`${v}T00:00:00`).toLocaleDateString();}catch{return v;}}

export default function Reports(){
  const[email,setEmail]=useState(""),[recips,setRecips]=useState(null),[sites,setSites]=useState([]),[deliveries,setDeliveries]=useState([]),[role,setRole]=useState("viewer"),[err,setErr]=useState(""),[note,setNote]=useState(""),[busy,setBusy]=useState(false),[form,setForm]=useState({whatsapp:"",email:"",channel:"whatsapp",name:"",site_id:""});
  const load=useCallback(async()=>{const g=await requireTenant();if(!g)return;setEmail(g.session.user.email||"");const sb=supabase();const[r,s,d,me]=await Promise.all([sb.rpc("wl_recipients"),sb.rpc("wl_sites"),sb.rpc("wl_deliveries",{p_days:30}),sb.rpc("wl_my_role")]);if(r.error||s.error||d.error||me.error){setErr(say(r.error||s.error||d.error||me.error));return;}setErr("");setRecips(r.data||[]);setSites(s.data||[]);setDeliveries(d.data||[]);setRole(me.data||"viewer");},[]);
  useEffect(()=>{load();},[load]);
  const canManage=role==="owner"||role==="admin",active=(recips||[]).filter((r)=>r.enabled),previewSite=deliveries[0]?.site||sites[0]?.name||"Example site",previewDate=new Date().toLocaleDateString(undefined,{weekday:"long",day:"numeric",month:"long"});
  async function add(e){e.preventDefault();setBusy(true);setErr("");setNote("");const{data,error}=await supabase().rpc("wl_add_recipient_v2",{p_whatsapp:form.channel==="email"?null:form.whatsapp.trim(),p_email:form.channel==="whatsapp"?null:form.email.trim(),p_channel:form.channel,p_name:form.name.trim()||null,p_site_id:form.site_id||null});setBusy(false);if(error){setErr(say(error));return;}if(data?.created===false)setNote(data.note||"Those report details are already saved.");else{setNote(form.channel==="both"?"WhatsApp and email added.":"Report recipient added.");setForm({whatsapp:"",email:"",channel:"whatsapp",name:"",site_id:""});}load();}
  async function toggle(id,enabled){const{error}=await supabase().rpc("wl_set_recipient",{p_id:id,p_enabled:!enabled});if(error)setErr(say(error));else load();}
  async function remove(id){if(!confirm("Remove this report recipient?"))return;const{error}=await supabase().rpc("wl_remove_recipient",{p_id:id});if(error)setErr(say(error));else load();}

  return <div className="shell"><Nav active="Reports" email={email}/><main className="main">
    <header className="target-page-head"><div><div className="target-eyebrow">Daily reporting</div><h1>The daily report</h1><p>A concise morning brief for each site, delivered through the channels your team actually uses.</p></div></header>
    {err&&<div className="err">{err}</div>}{note&&<div className="ok-note">{note}</div>}

    <section className="report-layout">
      <article className="daily-report-card">
        <div className="daily-report-head"><span className="daily-report-dot"/><div><b>WatchLog daily report preview</b><div className="muted" style={{fontSize:12,marginTop:2}}>Layout preview — not your live figures</div></div></div>
        <div className="daily-report-body"><div className="muted" style={{fontSize:13}}>{previewSite} · {previewDate}</div><h3>Overnight</h3><ul><li>Your report will summarize incidents and activity from the selected site.</li><li>Analytics such as visitor flow or after-hours activity appear when they are set up for that location.</li></ul><h3>Camera health</h3><ul><li>WatchLog highlights cameras or site connections that may need attention.</li></ul></div>
        <p className="muted" style={{fontSize:11,margin:"14px 0 0"}}>This preview intentionally shows no sample counts. Your actual figures appear in sent reports and delivery history.</p>
      </article>
      <aside className="report-side">
        <section className="panel"><div className="target-panel-title"><div><h2>Recipients</h2><p>{active.length} active report recipient{active.length===1?"":"s"}</p></div></div>{recips===null?<div className="empty">Loading...</div>:(recips||[]).length===0?<div className="empty">No recipients yet.</div>:<div style={{padding:"8px 16px 14px"}}>{(recips||[]).slice(0,5).map((r)=><div className={ui.listRow} key={r.id} style={{border:0,borderBottom:"1px solid var(--wl-target-line)",borderRadius:0,padding:"12px 2px"}}><div><b>{r.name||channelLabel(r.channel)}</b><small>{endpoint(r)} · {r.site||"All sites"}</small></div><span className={"pill "+(r.enabled?"s-ok":"s-unk")}>{r.enabled?"active":"paused"}</span></div>)}</div>}</section>
        <section className="panel"><div className="target-panel-title"><div><h2>Delivery history</h2><p>Last 30 days</p></div></div>{deliveries.length===0?<div className="empty">No reports sent yet.</div>:<div style={{padding:"8px 16px 14px"}}>{deliveries.slice(0,4).map((d,i)=><div className={ui.listRow} key={`${d.date}-${d.channel}-${i}`} style={{border:0,borderBottom:"1px solid var(--wl-target-line)",borderRadius:0,padding:"12px 2px"}}><div><b>{humanDate(d.date)} · {d.site}</b><small>{channelLabel(d.channel)} · {d.events??0} events</small></div>{statusPill(d.status)}</div>)}</div>}</section>
      </aside>
    </section>

    <div className={ui.sectionHead}><div><h2>Report recipients</h2><p>Choose who should receive each site's daily brief and whether it should arrive by WhatsApp, email or both.</p></div></div>
    {canManage?<div className={ui.card}><form onSubmit={add}><div className="row"><div className="field" style={{maxWidth:210}}><label>Channel</label><select value={form.channel} onChange={(e)=>setForm({...form,channel:e.target.value})}>{CHANNELS.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></div><div className="field"><label>Name</label><input value={form.name} placeholder="Operations team" onChange={(e)=>setForm({...form,name:e.target.value})}/></div><div className="field"><label>Site</label><select value={form.site_id} onChange={(e)=>setForm({...form,site_id:e.target.value})}><option value="">All sites</option>{sites.map((s)=><option key={s.id} value={s.id}>{s.name}</option>)}</select></div></div><div className="row" style={{marginTop:14}}>{form.channel!=="email"&&<div className="field"><label>WhatsApp number</label><input required value={form.whatsapp} inputMode="tel" placeholder="923001234567" onChange={(e)=>setForm({...form,whatsapp:e.target.value})}/></div>}{form.channel!=="whatsapp"&&<div className="field"><label>Email address</label><input required type="email" value={form.email} placeholder="name@company.com" onChange={(e)=>setForm({...form,email:e.target.value})}/></div>}<button className="small" disabled={busy} style={{width:"auto"}}>{busy?"Adding...":form.channel==="both"?"Add both channels":"Add recipient"}</button></div></form></div>:<div className={ui.callout}><span className={ui.statusDot}/><div><strong>Read-only report access</strong><p>Owners and Admins manage report recipients. You can still review recipients and delivery history.</p></div></div>}

    <div className="panel"><div className={ui.tableWrap}>{recips===null?<div className="empty">Loading...</div>:recips.length===0?<div className="empty">No recipients yet. Add someone to start daily report delivery.</div>:<table><thead><tr><th>Recipient</th><th>Channel</th><th>Destination</th><th>Site</th><th>Status</th><th></th></tr></thead><tbody>{recips.map((r)=><tr key={r.id}><td><b>{r.name||"Recipient"}</b></td><td>{channelLabel(r.channel)}</td><td className="mono">{endpoint(r)}</td><td>{r.site||"All sites"}</td><td><span className={"pill "+(r.enabled?"s-ok":"s-unk")}>{r.enabled?"active":"paused"}</span></td><td>{canManage&&<div className={ui.inlineActions}><button className="ghost small" onClick={()=>toggle(r.id,r.enabled)}>{r.enabled?"Pause":"Resume"}</button><button className="btn-danger" onClick={()=>remove(r.id)}>Remove</button></div>}</td></tr>)}</tbody></table>}</div></div>

    <div className={ui.sectionHead}><div><h2>Full delivery history</h2><p>See when each report was sent and whether WatchLog needs your attention.</p></div></div>
    <div className="panel"><div className={ui.tableWrap}>{deliveries.length===0?<div className="empty">No reports sent yet. Delivery history will appear here after the first scheduled report.</div>:<table><thead><tr><th>Date</th><th>Site</th><th>Channel</th><th>To</th><th>Status</th><th>Events</th></tr></thead><tbody>{deliveries.map((d,i)=><tr key={`${d.date}-${d.channel}-${d.destination}-${i}`}><td>{humanDate(d.date)}</td><td>{d.site}</td><td>{channelLabel(d.channel)}</td><td className="muted">{d.destination}</td><td>{statusPill(d.status)}{d.error&&<div className="muted" style={{fontSize:11,marginTop:4}}>{d.error}</div>}</td><td className="mono">{d.events??"-"}</td></tr>)}</tbody></table>}</div></div>
  </main></div>;
}
