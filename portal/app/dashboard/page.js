"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav } from "../shell";
import ui from "../portal.module.css";

const REFRESH_MS = 10000;
function liveness(lastSeen) { if (!lastSeen) return ["s-unk","not connected"]; const age=(Date.now()-Date.parse(lastSeen))/1000; if(age<180)return["s-ok","online"]; if(age<1800)return["s-warn","needs attention"]; return["s-bad","offline"]; }
function ago(ts) { if(!ts)return"never"; const s=Math.max(0,Math.round((Date.now()-Date.parse(ts))/1000)); if(s<60)return s+"s ago"; if(s<3600)return Math.round(s/60)+"m ago"; if(s<86400)return Math.round(s/3600)+"h ago"; return Math.round(s/86400)+"d ago"; }
function humanType(v){ return String(v||"event").replace(/^analytic_/,"").replaceAll("_"," ").replace(/\b\w/g,(c)=>c.toUpperCase()); }
function exact(ts){ try{return new Date(ts).toLocaleString();}catch{return String(ts||"");} }

export default function Dashboard(){
  const [data,setData]=useState(null),[error,setError]=useState(""),[days,setDays]=useState(7),[email,setEmail]=useState(""),[stamp,setStamp]=useState("");
  const shots=useRef(new Map()); const [,force]=useState(0);
  const load=useCallback(async(d)=>{const sb=supabase();const{data:{session}}=await sb.auth.getSession();if(!session){location.replace("/login/");return;}setEmail(session.user.email||"");const{data:res,error}=await sb.rpc("wl_portal_overview",{p_days:d});if(error){setError(say(error));return;}if(!res||!res.tenant){location.replace("/onboarding/");return;}setError("");setData(res);setStamp(new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}));},[]);
  useEffect(()=>{load(days);const t=setInterval(()=>load(days),REFRESH_MS);return()=>clearInterval(t);},[days,load]);
  async function loadShot(eventId){if(shots.current.has(eventId))return;shots.current.set(eventId,null);const{data}=await supabase().rpc("wl_portal_snapshot",{p_event_id:eventId});shots.current.set(eventId,data?.image_b64?`data:${data.content_type||"image/jpeg"};base64,${data.image_b64}`:false);force((n)=>n+1);}
  useEffect(()=>{(data?.recent||[]).filter((e)=>e.has_snapshot).slice(0,4).forEach((e)=>loadShot(e.event_id));},[data]);
  if(!data&&!error)return <div className="center"><p className="muted">Loading your sites...</p></div>;

  const agents=data?.agents||[],health=data?.health||{},offlineAgents=health.offline_agents||[],silentCameras=health.silent_cameras||[],faults24=health.faults_24h||[];
  const attentionCount=offlineAgents.length+silentCameras.length+faults24.length,offBySite={}; offlineAgents.forEach((a)=>{offBySite[a.site]=(offBySite[a.site]||0)+1;});
  const silentItems=silentCameras.slice(0,2).map((c)=>c?.camera&&c?.site?`${c.camera} quiet at ${c.site}`:c?.site?`Camera quiet at ${c.site}`:null).filter(Boolean);
  const attentionItems=[...Object.entries(offBySite).map(([site,n])=>`${n} WatchLog connection${n>1?"s":""} offline at ${site}`),...(silentItems.length?silentItems:(silentCameras.length?[`${silentCameras.length} camera${silentCameras.length>1?"s":""} quiet`]:[])),...(faults24.length?[`${faults24.length} camera-system fault${faults24.length>1?"s":""} in the last 24h`]:[])];
  const visibleAttentionItems=attentionItems.slice(0,4),hiddenAttentionCount=Math.max(0,attentionCount-visibleAttentionItems.length);
  const online=agents.filter((a)=>liveness(a.last_seen_at)[0]==="s-ok").length,bySite={};agents.forEach((a)=>{(bySite[a.site]=bySite[a.site]||[]).push(a);});
  const withShots=(data?.recent||[]).filter((e)=>e.has_snapshot).slice(0,4),maxType=Math.max(1,...(data?.by_type||[]).map((t)=>t.count));

  return <div className="shell"><Nav active="Overview" email={email} right={<><span className="muted hide-sm" style={{fontSize:"var(--font-size-xs)"}}>{stamp?`updated ${stamp}`:""}</span><select value={days} onChange={(e)=>setDays(Number(e.target.value))} style={{width:"auto",margin:0}} aria-label="Overview date range"><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option></select></>}/>
    <main className="main">
      <header className="target-page-head"><div><div className="target-eyebrow">Overview</div><h1>Every site, one operational view.</h1><p>See what needs attention first, then move straight into incidents, health or analytics without losing context.</p></div><div className="target-actions"><a className={ui.secondaryLink} href="/site-health/">Site Health</a><a className={ui.primaryLink} href="/incidents/">Review incidents</a></div></header>
      {error&&<div className="err">{error}</div>}
      <div className={"banner"+(attentionCount?"":" clear")}>{attentionCount?<><b>{attentionCount} thing{attentionCount>1?"s":""} need attention</b><ul>{visibleAttentionItems.map((t,i)=><li key={i}>{t}</li>)}</ul>{hiddenAttentionCount>0&&<div className="muted" style={{fontSize:"var(--font-size-sm)",marginTop:6}}>+{hiddenAttentionCount} more attention item{hiddenAttentionCount===1?"":"s"}</div>}</>:<><b>Everything is reporting normally.</b><div className="muted" style={{fontSize:"var(--font-size-sm)",marginTop:4}}>No site connections, cameras or recent equipment checks currently need attention.</div></>}</div>

      <section className={ui.metricGrid} aria-label="Overview summary"><div className={ui.metric}><div className={ui.metricValue}>{data?.totals?.events??0}</div><div className={ui.metricLabel}>Events · {data?.window_days} days</div></div><div className={ui.metric}><div className={ui.metricValue}>{data?.totals?.cameras??0}</div><div className={ui.metricLabel}>Cameras</div></div><div className={ui.metric}><div className={ui.metricValue} style={{color:agents.length&&online===0?"var(--color-status-bad-dark)":online<agents.length?"var(--color-status-warn-dark)":agents.length?"var(--color-status-ok-dark)":undefined}}>{online} / {agents.length}</div><div className={ui.metricLabel}>Site connections online</div></div><div className={ui.metric}><div className={ui.metricValue}>{data?.totals?.sites??0}</div><div className={ui.metricLabel}>{(data?.totals?.sites??0)===1?"Site":"Sites"}</div></div></section>

      <section className="overview-grid">
        <div className="panel overview-panel">
          <div className="target-panel-title"><div><h2>Sites &amp; connections</h2><p>WatchLog connection status for each operating location. Event counts below are all-time.</p></div><a className="target-link" href="/settings/">Manage sites</a></div>
          <div className={ui.tableWrap}>{agents.length===0?<div className="empty">No site has connected to WatchLog yet. <a href="/settings/">Open Sites &amp; Setup</a> to connect your first location.</div>:<table><thead><tr><th>Site / connection</th><th>Status</th><th>Last contact</th><th>All-time events</th><th className="hide-sm">Camera system</th></tr></thead><tbody>{Object.entries(bySite).map(([site,list])=>{const anyOnline=list.some((a)=>liveness(a.last_seen_at)[0]==="s-ok"),siteEvents=list.reduce((s,a)=>s+(a.event_count||0),0);return <Fragment key={site}><tr className="overview-site-row"><td><b>{site}</b></td><td><span className={"pill "+(anyOnline?"s-ok":"s-bad")}>{anyOnline?"online":"offline"}</span></td><td className="muted">{list.length} connection{list.length===1?"":"s"}</td><td className="mono">{siteEvents}</td><td className="muted hide-sm"></td></tr>{list.map((a)=>{const[cls,label]=liveness(a.last_seen_at),device=[a.device_vendor,a.device_model].filter(Boolean).join(" ");return <tr className="overview-agent" key={a.agent_id}><td>{a.hostname||"Site computer"}</td><td><span className={"pill "+cls}>{label}</span></td><td className="mono" title={exact(a.last_seen_at)}>{ago(a.last_seen_at)}</td><td className="mono">{a.event_count}</td><td className="muted hide-sm">{device||"Not identified"}</td></tr>;})}</Fragment>;})}</tbody></table>}</div>
        </div>

        <div className="overview-side">
          <div className="panel overview-panel">
            <div className="target-panel-title"><div><h2>Recent incidents</h2><p>Useful moments captured from your cameras.</p></div><a className="target-link" href="/incidents/">View all</a></div>
            {withShots.length===0?<div className="empty">No incident stills in this period yet.</div>:<div className="overview-shots">{withShots.map((e)=>{const shot=shots.current.get(e.event_id);return <a className="shot" key={e.event_id} href="/incidents/" style={{color:"inherit",textDecoration:"none"}}>{shot===undefined||shot===null?<div style={{display:"grid",placeItems:"center",background:"var(--color-canvas)"}} className="muted">loading image...</div>:shot===false?<div style={{display:"grid",placeItems:"center",background:"var(--color-canvas)"}} className="muted">image unavailable</div>:<img src={shot} alt={`still from ${e.camera||"camera"}`}/>}<div className="meta"><b>{e.camera||"Camera"}</b><div className="muted">{humanType(e.event_type)}</div><div className="muted" title={exact(e.device_ts)}>{ago(e.device_ts)}</div></div></a>;})}</div>}
          </div>

          <div className="panel overview-panel">
            <div className="target-panel-title"><div><h2>Event types</h2><p>What WatchLog has seen in the selected period.</p></div><span className="muted" style={{fontSize:12}}>{days===1?"24 hours":`${days} days`}</span></div>
            {(data?.by_type||[]).length===0?<div className="empty">Nothing yet.</div>:<div className="overview-event-bars">{data.by_type.slice(0,6).map((t)=><div className="overview-event-row" key={t.event_type}><span>{humanType(t.event_type)}</span><span className="overview-bar-track"><span className="overview-bar-fill" style={{display:"block",width:`${Math.max(4,Math.round((t.count/maxType)*100))}%`}}/></span><span className="overview-count mono">{t.count}</span></div>)}</div>}
          </div>
        </div>
      </section>
    </main></div>;
}
