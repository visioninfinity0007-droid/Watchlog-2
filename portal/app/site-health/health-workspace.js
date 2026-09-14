"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import { rememberSite, selectedSiteId } from "../site-context";
import ui from "../portal.module.css";

function human(v){return String(v||"Not verified").replaceAll("_"," ").replace(/\b\w/g,(c)=>c.toUpperCase());}
function ago(ts){if(!ts)return"Never";const s=Math.max(0,Math.round((Date.now()-Date.parse(ts))/1000));if(s<60)return`${s}s ago`;if(s<3600)return`${Math.round(s/60)}m ago`;if(s<86400)return`${Math.round(s/3600)}h ago`;return`${Math.round(s/86400)}d ago`;}
function Pill({kind="unknown",children}){const cls=kind==="ok"?"s-ok":kind==="bad"?"s-bad":kind==="warn"?"s-warn":"s-unk";return <span className={`pill ${cls}`}>{children}</span>;}

export default function HealthWorkspace(){
  const[email,setEmail]=useState(""),[sites,setSites]=useState([]),[siteId,setSiteId]=useState(""),[ctx,setCtx]=useState(null),[error,setError]=useState(""),[stamp,setStamp]=useState("");
  const load=useCallback(async()=>{const g=await requireTenant();if(!g)return;setEmail(g.session.user.email||"");const s=await supabase().rpc("wl_sites");if(s.error){setError(say(s.error));return;}const list=s.data||[];setSites(list);const preferred=new URLSearchParams(window.location.search).get("site")||selectedSiteId()||list[0]?.id||"";if(preferred){setSiteId(preferred);rememberSite(preferred,list.find((x)=>x.id===preferred)?.name||"");}},[]);
  const refresh=useCallback(async(id)=>{if(!id)return;const{data,error}=await supabase().rpc("wl_ai_context",{p_site_id:id});if(error){setError(say(error));return;}setCtx(data||null);setError("");setStamp(new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}));},[]);
  useEffect(()=>{load();},[load]);useEffect(()=>{if(siteId)refresh(siteId);},[siteId,refresh]);useEffect(()=>{if(!siteId)return;const t=setInterval(()=>refresh(siteId),15000);return()=>clearInterval(t);},[siteId,refresh]);
  const site=sites.find((s)=>s.id===siteId),cams=(ctx?.cameras||[]).filter((c)=>c.monitor),faults=ctx?.faults||[],online=Boolean(ctx?.connectivity?.agent_online),ever=Boolean(ctx?.connectivity?.last_seen),rec=ctx?.recorder||{};
  const state=!ever&&!online?["unknown","Setup required"]:!online?["bad","Offline"]:faults.length?["warn","Needs attention"]:["ok","Monitoring"];
  const healthy=cams.filter((c)=>!['offline','fault'].includes(String(c.health_state||'').toLowerCase())).length;
  function choose(id){setSiteId(id);rememberSite(id,sites.find((s)=>s.id===id)?.name||"");history.replaceState(null,"",`/site-health/?site=${encodeURIComponent(id)}`);}

  return <div className="shell"><Nav active="Site Health" email={email} currentSiteId={siteId} right={<span>{stamp?`Updated ${stamp}`:""}</span>}/><main className="main">
    <header className="target-page-head"><div><div className="target-eyebrow">Site Health</div><h1>Can I trust monitoring at {site?.name||"this site"}?</h1><p>WatchLog distinguishes live monitoring, recorder recovery and anything it could not verify.</p></div><div className="target-actions"><select value={siteId} onChange={(e)=>choose(e.target.value)}>{sites.map((s)=><option key={s.id} value={s.id}>{s.name}</option>)}</select><button className="ghost small" onClick={()=>refresh(siteId)}>Refresh</button></div></header>
    {error&&<div className="err">{error}</div>}
    {!ctx?<div className="panel"><div className="empty">Loading this site's health…</div></div>:<>
      <section className={ui.metricGrid}><div className={ui.metric}><div className={ui.metricValue}><Pill kind={state[0]}>{state[1]}</Pill></div><div className={ui.metricLabel}>WatchLog status</div></div><div className={ui.metric}><div className={ui.metricValue}>{cams.length?`${healthy}/${cams.length}`:"—"}</div><div className={ui.metricLabel}>Healthy monitored cameras</div></div><div className={ui.metric}><div className={ui.metricValue}>{faults.length}</div><div className={ui.metricLabel}>Needs attention</div></div><div className={ui.metric}><div className={ui.metricValue}>{ctx?.capability_known?"Loaded":"—"}</div><div className={ui.metricLabel}>Recorder capability profile</div></div></section>
      <section className={ui.twoCol}><div className={ui.card}><h3>Connection</h3><div className={ui.splitList}><div className={ui.listRow}><div><b>WatchLog Agent</b><small>Last contact {ago(ctx?.connectivity?.last_seen)}</small></div><Pill kind={online?"ok":ever?"bad":"unknown"}>{online?"Online":ever?"Offline":"Not connected"}</Pill></div><div className={ui.listRow}><div><b>Recorder</b><small>{[rec.vendor,rec.model].filter(Boolean).join(" ")||"Not identified yet"}</small></div><Pill kind={rec.identified?"ok":"unknown"}>{rec.identified?"Identified":"Unconfirmed"}</Pill></div></div></div><div className={ui.card}><h3>Attention</h3>{!ever&&!online?<div className={ui.emptyCard}>Camera health isn't available yet. Connect the WatchLog Agent to begin monitoring.</div>:faults.length?<div className={ui.splitList}>{faults.slice(0,8).map((f,i)=><div className={ui.listRow} key={f.id||i}><Pill kind="warn">Check</Pill><div><b>{f.camera||"Camera"}</b><small>{human(f.reason||f.type||f.fault_type)}</small></div></div>)}</div>:<div className={ui.emptyCard}>No current monitoring issue is reported for this configured site.</div>}</div></section>
      <div className={ui.sectionHead}><div><h2>Cameras</h2><p>Only cameras configured for monitoring count toward health.</p></div></div><div className="panel"><div className={ui.tableWrap}>{cams.length?<table><thead><tr><th>Camera</th><th>Channel</th><th>Purpose</th><th>Health</th><th>Recording</th></tr></thead><tbody>{cams.map((c)=><tr key={c.id||c.channel}><td><b>{c.name||`Camera ${c.channel}`}</b></td><td>{c.channel}</td><td>{human(c.purpose||"general")}</td><td><Pill kind={c.health_state==="offline"?"bad":c.health_state?"ok":"unknown"}>{human(c.health_state)}</Pill></td><td><Pill kind={String(c.recording_state||"").toLowerCase().includes("record")?"ok":"unknown"}>{human(c.recording_state)}</Pill></td></tr>)}</tbody></table>:<div className="empty">{online?"No cameras are configured for monitoring yet.":"Camera health will appear after WatchLog connects to this site."}</div>}</div></div>
    </>}
  </main></div>;
}
