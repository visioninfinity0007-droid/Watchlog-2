"use client";

import {useCallback,useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {Nav,requireTenant} from "../shell";
import {rememberSite,selectedSiteId} from "../site-context";
import ui from "../portal.module.css";

function human(v){return String(v||"Not verified").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase())}
function ago(ts){
  if(!ts)return"Never";
  const s=Math.max(0,Math.round((Date.now()-Date.parse(ts))/1000));
  if(s<60)return s+"s ago";
  if(s<3600)return Math.round(s/60)+"m ago";
  if(s<86400)return Math.round(s/3600)+"h ago";
  return Math.round(s/86400)+"d ago";
}
function Pill({kind="unknown",children}){return <span className={"pill "+(kind==="ok"?"s-ok":kind==="bad"?"s-bad":kind==="warn"?"s-warn":"s-unk")}>{children}</span>}
function healthView(state){
  switch(String(state||"unknown").toLowerCase()){
    case"operational":return{label:"Healthy",kind:"ok"};
    case"degraded":return{label:"Needs attention",kind:"warn"};
    case"offline":return{label:"Offline",kind:"bad"};
    default:return{label:"Not verified",kind:"unknown"};
  }
}
function recordingView(state){
  switch(String(state||"unknown").toLowerCase()){
    case"recording":return{label:"Confirmed",kind:"ok"};
    case"not_recording":return{label:"Not recording",kind:"bad"};
    case"storage_fault":return{label:"Needs attention",kind:"warn"};
    default:return{label:"Not verified",kind:"unknown"};
  }
}
function faultLabel(f){
  const key=String(f?.reason||f?.reason_code||f?.type||f?.fault_type||"").toLowerCase();
  const labels={
    agent_unreachable:"WatchLog connection lost",
    nvr_unreachable:"Camera system unreachable",
    nvr_auth_failed:"Camera system sign-in failed",
    storage_fault:"Camera system storage issue",
    storage_degraded:"Camera system storage needs attention",
    disk_full:"Camera system storage is full",
    video_loss:"Camera offline",
    camera_offline:"Camera offline",
    not_recording:"Recording is not confirmed",
    recording_storage_fault:"Recording storage issue"
  };
  return labels[key]||"Monitoring issue";
}

export default function HealthWorkspace(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[ctx,setCtx]=useState(null);
  const[error,setError]=useState("");
  const[loading,setLoading]=useState(true);

  const load=useCallback(async()=>{
    const g=await requireTenant();if(!g)return;
    setEmail(g.session.user.email||"");
    const s=await supabase().rpc("wl_sites");
    if(s.error){setError(say(s.error));setLoading(false);return}
    const list=s.data||[];
    setSites(list);
    const requested=new URLSearchParams(location.search).get("site")||selectedSiteId()||list[0]?.id||"";
    const id=list.some(x=>String(x.id)===String(requested))?requested:(list[0]?.id||"");
    setSiteId(id);
    if(id)rememberSite(id,list.find(x=>String(x.id)===String(id))?.name||"");
  },[]);

  const refresh=useCallback(async id=>{
    if(!id)return;
    setLoading(true);
    const r=await supabase().rpc("wl_ai_context",{p_site_id:id});
    setLoading(false);
    if(r.error){setError(say(r.error));return}
    setCtx(r.data||null);setError("");
  },[]);

  useEffect(()=>{load()},[load]);
  useEffect(()=>{if(siteId)refresh(siteId)},[siteId,refresh]);
  useEffect(()=>{if(!siteId)return;const t=setInterval(()=>refresh(siteId),15000);return()=>clearInterval(t)},[siteId,refresh]);

  async function choose(id){
    setSiteId(id);setCtx(null);
    const row=sites.find(x=>String(x.id)===String(id));
    rememberSite(id,row?.name||"");
    history.replaceState(null,"",id?"/site-health/?site="+encodeURIComponent(id):"/site-health/");
    await refresh(id);
  }

  const site=sites.find(s=>String(s.id)===String(siteId));
  const cams=(ctx?.cameras||[]).filter(c=>c.monitor);
  const faults=ctx?.faults||[];
  const online=Boolean(ctx?.connectivity?.agent_online);
  const ever=Boolean(ctx?.connectivity?.last_seen);
  const system=ctx?.recorder||{};

  const stats=useMemo(()=>{
    const operational=cams.filter(c=>String(c.health_state||"").toLowerCase()==="operational").length;
    const degraded=cams.filter(c=>String(c.health_state||"").toLowerCase()==="degraded").length;
    const offline=cams.filter(c=>String(c.health_state||"").toLowerCase()==="offline").length;
    const healthUnknown=cams.length-operational-degraded-offline;
    const recording=cams.filter(c=>String(c.recording_state||"").toLowerCase()==="recording").length;
    const recordingIssue=cams.filter(c=>["not_recording","storage_fault"].includes(String(c.recording_state||"").toLowerCase())).length;
    const recordingUnknown=cams.length-recording-recordingIssue;
    return{operational,degraded,offline,healthUnknown,recording,recordingIssue,recordingUnknown};
  },[cams]);

  let tone="ok",title="Monitoring is currently verified",copy="WatchLog is connected, monitored cameras are healthy, and recording is confirmed for the cameras shown below.";
  if(!ever&&!online){
    tone="unknown";title="Monitoring is not set up yet";copy="Connect WatchLog to this site before camera health and recording can be checked.";
  }else if(!online){
    tone="bad";title="Site connection is unavailable";copy="The last WatchLog contact was "+ago(ctx?.connectivity?.last_seen)+". The current monitoring picture may be incomplete until the site reconnects.";
  }else if(!cams.length){
    tone="unknown";title="No cameras are selected for monitoring";copy="Choose the cameras WatchLog should monitor before this page can verify camera health or recording.";
  }else if(faults.length||stats.offline||stats.degraded||stats.recordingIssue){
    tone="warn";title="Monitoring needs attention";copy=(faults.length||stats.offline+stats.degraded+stats.recordingIssue)+" item"+((faults.length||stats.offline+stats.degraded+stats.recordingIssue)===1?"":"s")+" need checking. Review the details below.";
  }else if(stats.healthUnknown||stats.recordingUnknown){
    tone="unknown";title="Monitoring is not fully verified";copy="WatchLog is connected, but some camera health or recording states are still unverified. Unknown states are not treated as healthy.";
  }

  const connectionKind=online?"ok":ever?"bad":"unknown";
  const cameraKind=!cams.length?"unknown":stats.offline||stats.degraded?"warn":stats.healthUnknown?"unknown":"ok";
  const recordingKind=!cams.length?"unknown":stats.recordingIssue?"warn":stats.recordingUnknown?"unknown":"ok";

  return <div className="shell">
    <Nav active="Site Health" email={email} currentSiteId={siteId}/>
    <main className="main">
      <header className="target-page-head">
        <div><div className="target-eyebrow">System Health</div><h1>Can I trust monitoring at {site?.name||"this site"}?</h1><p>Connection, camera health and recording verification in one place.</p></div>
        <div className="target-actions">
          {sites.length>1&&<select value={siteId} onChange={e=>choose(e.target.value)}>{sites.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select>}
          <button className="ghost small" onClick={()=>refresh(siteId)} disabled={loading}>{loading?"Refreshing…":"Refresh"}</button>
        </div>
      </header>

      {error&&<div className="err">{error}</div>}
      {!ctx?<div className={ui.emptyCard}>Checking monitoring health…</div>:<>
        <section className={ui.featureCard}>
          <Pill kind={tone}>{tone==="ok"?"Verified":tone==="bad"?"Connection issue":tone==="warn"?"Needs attention":"Not fully verified"}</Pill>
          <h2 style={{fontSize:28,margin:"12px 0 8px",letterSpacing:"-.035em",textTransform:"none"}}>{title}</h2>
          <p style={{fontSize:14,margin:0,maxWidth:760}}>{copy}</p>
        </section>

        <section className={ui.metricGrid} style={{marginTop:14}}>
          <div className={ui.metric}><div className={ui.metricLabel}>Connection</div><div style={{marginTop:8}}><Pill kind={connectionKind}>{online?"Connected":ever?"Unavailable":"Not connected"}</Pill></div><div className={ui.metricLabel}>Last contact {ago(ctx?.connectivity?.last_seen)}</div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{cams.length?stats.operational+"/"+cams.length:"—"}</div><div className={ui.metricLabel}>Cameras confirmed healthy</div><div style={{marginTop:8}}><Pill kind={cameraKind}>{cameraKind==="ok"?"Verified":cameraKind==="warn"?"Needs attention":"Not verified"}</Pill></div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{cams.length?stats.recording+"/"+cams.length:"—"}</div><div className={ui.metricLabel}>Recording confirmed</div><div style={{marginTop:8}}><Pill kind={recordingKind}>{recordingKind==="ok"?"Verified":recordingKind==="warn"?"Needs attention":"Not verified"}</Pill></div></div>
          <div className={ui.metric}><div className={ui.metricValue}>{faults.length}</div><div className={ui.metricLabel}>Current monitoring issues</div><div style={{marginTop:8}}><Pill kind={faults.length?"warn":"ok"}>{faults.length?"Review":"None reported"}</Pill></div></div>
        </section>

        <section className={ui.twoCol} style={{marginTop:16}}>
          <div className={ui.card}>
            <h3>Needs attention</h3>
            {!ever&&!online?<div className={ui.emptyCard}>Health information will appear after WatchLog connects to the site.</div>:faults.length?<div className={ui.splitList}>{faults.slice(0,8).map((f,i)=><div className={ui.listRow} key={f.id||i}><Pill kind="warn">Check</Pill><div><b>{f.camera||"Site monitoring"}</b><small>{faultLabel(f)}</small></div></div>)}</div>:stats.healthUnknown||stats.recordingUnknown?<div className={ui.emptyCard}>No current fault is reported, but some states are still not verified. Review the camera table below before treating the picture as complete.</div>:<div className={ui.emptyCard}>No current monitoring fault is reported.</div>}
          </div>
          <div className={ui.card}>
            <h3>Camera system</h3>
            <div className={ui.splitList}>
              <div className={ui.listRow}><div><b>Recorder</b><small>{[system.vendor,system.model].filter(Boolean).join(" ")||"Not identified yet"}</small></div><Pill kind={system.identified?"ok":"unknown"}>{system.identified?"Identified":"Not confirmed"}</Pill></div>
              <div className={ui.listRow}><div><b>WatchLog connection</b><small>Current site connection</small></div><Pill kind={connectionKind}>{online?"Connected":ever?"Unavailable":"Not connected"}</Pill></div>
            </div>
          </div>
        </section>

        <div className={ui.sectionHead}><div><h2>Cameras</h2><p>Health and recording are shown separately so one green state never hides an unknown one.</p></div></div>
        <div className="panel"><div className={ui.tableWrap}>{cams.length?<table><thead><tr><th>Camera</th><th>Area</th><th>Health</th><th>Recording</th></tr></thead><tbody>{cams.map(c=>{const health=healthView(c.health_state),recording=recordingView(c.recording_state);return <tr key={c.id||c.channel}><td><b>{c.name||"Camera "+c.channel}</b></td><td>{human(c.purpose||"general")}</td><td><Pill kind={health.kind}>{health.label}</Pill></td><td><Pill kind={recording.kind}>{recording.label}</Pill></td></tr>})}</tbody></table>:<div className="empty">No monitored cameras yet.</div>}</div></div>
      </>}
    </main>
  </div>;
}
