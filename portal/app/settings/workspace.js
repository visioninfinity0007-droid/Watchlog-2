"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant, setupPill } from "../shell";
import { rememberSite, selectedSiteId, withSite } from "../site-context";
import ui from "../portal.module.css";

function fmt(ts){return ts?new Date(ts).toLocaleString():"Never";}

export default function SettingsWorkspace(){
  const[email,setEmail]=useState(""),[role,setRole]=useState("viewer"),[trial,setTrial]=useState({}),[entitlement,setEntitlement]=useState({}),[sites,setSites]=useState([]),[siteId,setSiteId]=useState(""),[newSite,setNewSite]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[note,setNote]=useState("");
  const load=useCallback(async()=>{const g=await requireTenant();if(!g)return;setEmail(g.session.user.email||"");const sb=supabase();const[t,e,s,r]=await Promise.all([sb.rpc("wl_trial_status"),sb.rpc("wl_entitlement"),sb.rpc("wl_sites"),sb.rpc("wl_my_role")]);const fail=t.error||e.error||s.error||r.error;if(fail){setError(say(fail));return;}const list=s.data||[];setTrial(t.data||{});setEntitlement(e.data||{});setSites(list);setRole(r.data||"viewer");setError("");if(!siteId&&list.length){const preferred=new URLSearchParams(window.location.search).get("site")||selectedSiteId()||list[0].id;setSiteId(preferred);rememberSite(preferred,list.find((x)=>x.id===preferred)?.name||"");}},[siteId]);
  useEffect(()=>{load();},[load]);
  const canManage=role==="owner"||role==="admin";const current=sites.find((s)=>s.id===siteId);
  function choose(id){setSiteId(id);rememberSite(id,sites.find((s)=>s.id===id)?.name||"");history.replaceState(null,"",`/settings/?site=${encodeURIComponent(id)}`);}
  async function addSite(e){e.preventDefault();if(!canManage||!newSite.trim())return;setBusy(true);setError("");const{error}=await supabase().rpc("wl_add_site",{p_name:newSite.trim()});setBusy(false);if(error){setError(say(error));return;}setNewSite("");setNote("Site added. Open Guided Setup when you are ready to connect it.");await load();}
  async function issueCode(id){setError("");const{data,error}=await supabase().rpc("wl_issue_code",{p_site_id:id,p_days:14});if(error){setError(say(error));return;}setNote(`Setup code ${data.code} created. It is valid for 14 days and can be used once.`);await load();}

  return <div className="shell"><Nav active="Settings" email={email} currentSiteId={siteId}/><main className="main">
    <header className="target-page-head"><div><div className="target-eyebrow">Settings</div><h1>Account and sites</h1><p>Maintain existing sites and account access here. First-time camera configuration stays in Guided Setup.</p></div><div className="target-actions"><a className={ui.secondaryLink} href="/settings/account/">Billing, invoices & agreements</a></div></header>
    {error&&<div className="err">{error}</div>}{note&&<div className="ok-note">{note}</div>}
    <section className={ui.metricGrid}><div className={ui.metric}><div className={ui.metricValue} style={{textTransform:"capitalize"}}>{trial.plan||"trial"}</div><div className={ui.metricLabel}>Current plan</div></div><div className={ui.metric}><div className={ui.metricValue}><span className={`pill ${trial.status==="active"?"s-ok":trial.status==="trialing"?"s-warn":"s-unk"}`}>{trial.status||"unknown"}</span></div><div className={ui.metricLabel}>Account status</div></div><div className={ui.metric}><div className={ui.metricValue}>{sites.length}</div><div className={ui.metricLabel}>Sites</div></div><div className={ui.metric}><div className={ui.metricValue}><span className={`pill ${entitlement?.reporting_enabled?"s-ok":"s-unk"}`}>{entitlement?.reporting_enabled?"active":"paused"}</span></div><div className={ui.metricLabel}>Daily reporting</div></div></section>

    <div className={ui.sectionHead}><div><h2>Sites</h2><p>Maintain site identity, setup access and current connection state. Use Guided Setup to configure cameras, purposes and monitoring recommendations.</p></div></div>
    {canManage&&<form className={ui.card} onSubmit={addSite}><div className="row"><div className="field"><label>Add a site</label><input required value={newSite} onChange={(e)=>setNewSite(e.target.value)} placeholder="Warehouse, Head Office, Branch 2"/></div><button className="small" disabled={busy} style={{width:"auto"}}>{busy?"Adding…":"Add site"}</button></div></form>}
    <section className="settings-site-grid">{sites.map((s)=>{const[label,cls]=setupPill(s.setup_state,s.online);return <article className={`settings-site-card ${s.id===siteId?"active":""}`} key={s.id} onClick={()=>choose(s.id)}><div className="settings-site-head"><div><b>{s.name}</b><small>{s.timezone||"Site timezone"}</small></div><span className={`pill ${cls}`}>{label}</span></div><div className="settings-site-facts"><div><span>Cameras</span><b>{s.cameras??0}</b></div><div><span>Last activity</span><b>{fmt(s.last_event)}</b></div></div>{canManage&&<div className="settings-site-actions"><a href={withSite("/setup/",s.id)}>Open Guided Setup</a><button className="ghost small" type="button" onClick={(e)=>{e.stopPropagation();issueCode(s.id);}}>New setup code</button></div>}</article>;})}{!sites.length&&<div className={ui.emptyCard}>No sites yet. Add your first site above, then WatchLog will guide the connection.</div>}</section>

    {current&&<><div className={ui.sectionHead}><div><h2>{current.name}</h2><p>Choose the right place for the task instead of duplicating configuration in Settings.</p></div></div><section className={ui.threeCol}><a className={ui.roleCard} href={withSite("/setup/",current.id)}><strong>Guided Setup</strong><p>Connect the Agent, map cameras and review AI recommendations.</p><span>Continue setup →</span></a><a className={ui.roleCard} href={withSite("/site-health/",current.id)}><strong>Site Health</strong><p>Check connection, camera and recording truth.</p><span>Open health →</span></a><a className={ui.roleCard} href={withSite("/ai/",current.id)}><strong>WatchLog AI</strong><p>Ask WatchLog to explain or change the software-side monitoring plan.</p><span>Ask WatchLog →</span></a></section></>}
  </main></div>;
}
