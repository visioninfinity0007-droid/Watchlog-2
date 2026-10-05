"use client";

import {useCallback,useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId,withSite} from "../site-context";
import {coverageTruth,OwnerPage,SiteSelect,Lead,Section,Row,Status,Ledger,RailSection,Stat,Figure,Summary,Empty,Loading,Notice,AskLinks,ratioPct} from "../owner/ui";
import {recorderImpact} from "./recorder-impact";

function human(v){return String(v||"").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase())}
function ago(ts){
  if(!ts)return"Never";
  const s=Math.max(0,Math.round((Date.now()-Date.parse(ts))/1000));
  if(s<60)return s+"s ago";
  if(s<3600)return Math.round(s/60)+"m ago";
  if(s<86400)return Math.round(s/3600)+"h ago";
  return Math.round(s/86400)+"d ago";
}
function siteTime(ts,timeZone,withDay){
  if(!ts)return"";
  try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",...(withDay?{day:"numeric",month:"short"}:{}),hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(ts))}
  catch{return String(ts)}
}
function duration(seconds){
  const s=Math.max(0,Math.round(Number(seconds)||0));
  if(s<60)return"under a minute";
  const m=Math.round(s/60);
  if(m<60)return m+" min";
  const h=Math.floor(m/60),r=m%60;
  return h+" h"+(r?" "+r+" min":"");
}
function healthView(state){
  switch(String(state||"unknown").toLowerCase()){
    case"operational":return{label:"Healthy",tone:"ok"};
    case"degraded":return{label:"Needs attention",tone:"warn"};
    case"offline":return{label:"Offline",tone:"bad"};
    default:return{label:"Not verified",tone:"unknown"};
  }
}
function recordingView(state){
  switch(String(state||"unknown").toLowerCase()){
    case"recording":return{label:"Confirmed",tone:"ok"};
    case"not_recording":return{label:"Not recording",tone:"bad"};
    case"storage_fault":return{label:"Needs attention",tone:"warn"};
    default:return{label:"Not verified",tone:"unknown"};
  }
}
// Customer wording for a reported fault, and whether the owner (rather than WatchLog) needs to act.
function faultView(f){
  const key=String(f?.reason||f?.reason_code||f?.type||f?.fault_type||"").toLowerCase();
  const views={
    agent_unreachable:["WatchLog connection lost","Check that the WatchLog computer at the site is switched on and online.",true],
    nvr_unreachable:["Camera system unreachable","Check that the recorder is powered and connected to the site network.",true],
    nvr_auth_failed:["Camera system sign-in failed","The recorder sign-in may have changed. Update it in Setup & Support.",true],
    storage_fault:["Camera system storage issue","Check the recorder's storage drive.",true],
    storage_degraded:["Camera system storage needs attention","Check the recorder's storage drive soon.",true],
    disk_full:["Camera system storage is full","Free up or replace the recorder's storage.",true],
    video_loss:["Camera offline","Check the camera's power and cable.",true],
    camera_offline:["Camera offline","Check the camera's power and cable.",true],
    not_recording:["Recording is not confirmed","Check the recording schedule on the recorder.",true],
    recording_storage_fault:["Recording storage issue","Check the recorder's storage drive.",true]
  };
  const v=views[key]||["Monitoring issue","WatchLog is checking this issue.",false];
  return{label:v[0],action:v[1],owner:v[2]};
}
function gapCause(cause){
  const key=String(cause||"").toLowerCase();
  if(key==="agent_unreachable")return"Site connection lost";
  if(key==="no_authoritative_agent")return"Site was not connected";
  return"Monitoring not verified";
}
function recorderView(row){
  const state=String(row?.state||"unknown").toLowerCase();
  const issue=String(row?.issue||"").toLowerCase();
  if(state==="healthy")return{label:"Available",tone:"ok",body:"This recorder is available to WatchLog.",action:"No owner action required"};
  if(state==="offline")return{label:"Unavailable",tone:"bad",body:"WatchLog cannot reach this recorder right now.",action:"Check that the recorder is powered and connected to the site network."};
  if(state==="attention"&&issue==="sign_in")return{label:"Needs attention",tone:"warn",body:"WatchLog cannot sign in to this recorder.",action:"The recorder sign-in may have changed. Update it in Setup & Support."};
  if(state==="attention"&&issue==="storage")return{label:"Needs attention",tone:"warn",body:"This recorder's storage needs attention.",action:"Check the recorder's storage drive."};
  if(state==="attention")return{label:"Needs attention",tone:"warn",body:"This recorder needs checking.",action:"Check the recorder at the site."};
  return{label:"Not verified",tone:"unknown",body:"WatchLog has not verified this recorder's current state yet.",action:"No owner action yet"};
}

export default function HealthWorkspace(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[ctx,setCtx]=useState(null);
  const[recorderSummary,setRecorderSummary]=useState(null);
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
    else setLoading(false);
  },[]);

  const refresh=useCallback(async id=>{
    if(!id)return;
    setLoading(true);
    const sb=supabase();
    const[r,recorders]=await Promise.all([
      sb.rpc("wl_ai_context",{p_site_id:id}),
      sb.rpc("wl_my_site_recorders",{p_site_id:id})
    ]);
    setLoading(false);
    if(r.error){setError(say(r.error));return}
    setCtx(r.data||null);
    setRecorderSummary(recorders.error?null:(recorders.data||null));
    setError("");
  },[]);

  useEffect(()=>{load()},[load]);
  useEffect(()=>{if(siteId)refresh(siteId)},[siteId,refresh]);
  useEffect(()=>{if(!siteId)return;const t=setInterval(()=>refresh(siteId),15000);return()=>clearInterval(t)},[siteId,refresh]);

  async function choose(id){
    setSiteId(id);setCtx(null);setRecorderSummary(null);
    const row=sites.find(x=>String(x.id)===String(id));
    rememberSite(id,row?.name||"");
    history.replaceState(null,"",id?"/site-health/?site="+encodeURIComponent(id):"/site-health/");
    await refresh(id);
  }

  const site=sites.find(s=>String(s.id)===String(siteId));
  const tz=site?.timezone||ctx?.site?.timezone;
  const cams=(ctx?.cameras||[]).filter(c=>c.monitor);
  const faults=ctx?.faults||[];
  const online=Boolean(ctx?.connectivity?.agent_online);
  const ever=Boolean(ctx?.connectivity?.last_seen);
  const lastSeen=ctx?.connectivity?.last_seen;
  const system=ctx?.recorder||{};
  const recorderRows=recorderSummary?.recorders||[];
  const multiRecorder=recorderRows.length>1;
  const recorderById=useMemo(()=>new Map(recorderRows.map(r=>[String(r.id),r])),[recorderRows]);
  const recorderByCamera=useMemo(()=>{
    const map=new Map();
    for(const row of recorderRows)for(const cameraId of row.camera_ids||[])map.set(String(cameraId),String(row.id));
    return map;
  },[recorderRows]);
  // Recorder root cause (MNVR-068): one issue per failing recorder; its cameras take its advice.
  const impact=recorderImpact({cams,faults,recorderRows});
  const {recorderIssues,recorderIssueCameraIds,cameraFaults}=impact;
  const recorderHealthy=recorderRows.filter(r=>String(r.state||"").toLowerCase()==="healthy").length;
  const coverage=ctx?.coverage||null;
  // One governed coverage truth for the whole page (rail, summary and the "could not be verified" section).
  const truth=coverageTruth(coverage);
  const coveragePct=truth.pct;
  const generatedAt=ctx?.generated_at?Date.parse(ctx.generated_at):Date.now();
  const gaps=(coverage?.gaps||[]).filter(g=>g&&g.start).map(g=>{
    const start=Date.parse(g.start),end=g.end?Date.parse(g.end):generatedAt;
    return{start:g.start,end:g.end,cause:g.cause,seconds:Math.max(0,(end-start)/1000),ongoing:!g.end||Math.abs(generatedAt-end)<120000};
  });

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

  // The conclusion: is WatchLog currently able to observe this site?
  const issueCount=impact.issueCount;
  let tone="ok",title="WatchLog is observing this site",copy="Connected · "+cams.length+" monitored camera"+(cams.length===1?"":"s")+" healthy with recording confirmed.";
  let cta=null;
  if(!ever&&!online){
    tone="unknown";title="WatchLog is not observing this site yet";copy="Connect WatchLog to this site before camera health and recording can be checked.";
    cta=<a className="ow-btn" href={withSite("/setup/",siteId)}>Continue setup</a>;
  }else if(!online){
    tone="bad";title="WatchLog cannot observe this site right now";copy="Site connection lost · last contact "+ago(lastSeen)+". The current monitoring picture may be incomplete until the site reconnects.";
  }else if(!cams.length){
    tone="unknown";title="No cameras are selected for monitoring";copy="Choose the cameras WatchLog should monitor before this page can verify camera health or recording.";
    cta=<a className="ow-btn" href={withSite("/setup/",siteId)}>Choose cameras</a>;
  }else if(impact.blockingIssues.length||(recorderIssues.length&&issueCount===recorderIssues.length)){
    // A recorder issue leads only when it stops observation, or when it is the only issue: a storage
    // issue never outranks camera faults. Affected cameras are the ones the root cause explains.
    const affected=recorderIssueCameraIds.size;
    const unavailable=recorderIssues.filter(r=>String(r.state||"").toLowerCase()==="offline").length;
    const more=issueCount-recorderIssues.length;
    tone=unavailable?"bad":"warn";
    title=recorderIssues.length+" recorder"+(recorderIssues.length===1?" needs":"s need")+" attention";
    copy=(affected?affected+" camera"+(affected===1?" is":"s are")+" affected. ":"")+"WatchLog is connected; the recorder issue is grouped below so one cause is not shown as many unrelated camera faults."+(more>0?" "+more+" more item"+(more===1?" needs":"s need")+" checking below.":"");
  }else if(cameraFaults.length||stats.offline||stats.degraded||stats.recordingIssue){
    tone="warn";title="Monitoring needs attention";copy=issueCount+" item"+(issueCount===1?"":"s")+" need checking. WatchLog is connected; the affected cameras are listed below.";
  }else if(stats.healthUnknown||stats.recordingUnknown){
    tone="unknown";title="Monitoring is not fully verified";copy="WatchLog is connected, but some camera health or recording states are still unverified. Unknown states are not treated as healthy.";
  }

  const connectionTone=online?"ok":ever?"bad":"unknown";
  const connectionWord=online?"Connected":ever?"Disconnected":"Not connected yet";

  // Per-camera ledger. While the site connection is lost, earlier camera states are stale, so they are
  // shown as not verified with the last known state - never as current green.
  const ledger=cams.map(c=>{
    const h=healthView(c.health_state),r=recordingView(c.recording_state);
    const fault=impact.faultFor(c),failed=impact.recorderFor(c);
    const recorder=recorderById.get(recorderByCamera.get(String(c.id)));
    const stale=!online;
    return{
      key:c.id||c.channel,
      name:c.name||"Camera "+c.channel,
      area:c.purpose?human(c.purpose):"Purpose not set",
      recorder:recorder?.name||null,
      health:stale?{label:"Not verified",tone:"unknown",note:h.tone!=="unknown"?"Last known: "+h.label.toLowerCase():null}:h,
      recording:stale?{label:"Not verified",tone:"unknown",note:r.tone!=="unknown"?"Last known: "+r.label.toLowerCase():null}:r,
      action:failed?recorderView(failed).action:fault?faultView(fault).action:stale?"Waiting for the site to reconnect":h.tone==="bad"||h.tone==="warn"?"Check the camera's power and cable.":r.tone==="bad"||r.tone==="warn"?"Check the recording schedule on the recorder.":h.tone==="unknown"||r.tone==="unknown"?"WatchLog is still confirming":"None",
      attention:Boolean(fault||failed)||["bad","warn"].includes(h.tone)||["bad","warn"].includes(r.tone),
      reported:Boolean(fault||failed)||impact.unattributedCameraIds.has(String(c.id))
    };
  }).sort((a,b)=>Number(b.attention)-Number(a.attention));
  // Cameras whose own state needs checking but which have no reported fault row.
  const unreported=online?ledger.filter(c=>c.attention&&!c.reported&&!recorderIssueCameraIds.has(String(c.key))):[];
  const ownerActions=recorderIssues.length+cameraFaults.map(faultView).filter(v=>v.owner).length+unreported.length+(!online&&ever?1:0);

  const rail=ctx?<>
    <RailSection label="Coverage today">
      {coveragePct===null?<Figure value="—" unit="not verified yet"/>:<Figure value={coveragePct+"%"} unit="verified since midnight"/>}
      <div style={{marginTop:10}}><Ledger ratio={coveragePct===null?null:coveragePct/100} classes={coverage?.classes}/></div>
      {truth.partial&&<p className="ow-rail-note">{truth.unverifiedSeconds===null?(100-coveragePct)+"% of today could not be verified. ":""}Unverified time is not treated as quiet time.</p>}
    </RailSection>
    <RailSection label="Site">
      <Stat label="Connection" note={ever?"Last contact "+ago(lastSeen):null} value={<Status tone={connectionTone}>{connectionWord}</Status>}/>
      <Stat label="Cameras confirmed healthy" value={cams.length&&online?stats.operational+" of "+cams.length:null}/>
      <Stat label="Recording confirmed" value={cams.length&&online?stats.recording+" of "+cams.length:null}/>
      <Stat label={multiRecorder?"Recorders available":"Recorder"} value={recorderRows.length
        ?<><Status tone={recorderIssues.some(r=>String(r.state||"").toLowerCase()==="offline")?"bad":recorderIssues.length?"warn":recorderHealthy===recorderRows.length?"ok":"unknown"}>
          {multiRecorder?recorderHealthy+" of "+recorderRows.length:recorderView(recorderRows[0]).label}
        </Status></>
        :<Status tone={system.identified?"verified":"unknown"}>{system.identified?"Confirmed":"Not confirmed"}</Status>}/>
    </RailSection>
    <RailSection label="Ask WatchLog">
      <AskLinks siteId={siteId} prompts={["Is monitoring fully working at this site right now?","Which cameras need attention and why?","Was today fully monitored?"]}/>
    </RailSection>
  </>:null;

  const summary=ctx?<Summary items={[
    {value:connectionWord,label:"Connection",muted:!online},
    {value:cams.length&&online?stats.operational+"/"+cams.length:"—",label:"Cameras healthy",muted:!cams.length||!online},
    {value:coveragePct===null?"Not verified":coveragePct+"%",label:"Coverage today",muted:coveragePct===null,ledger:coveragePct===null?null:coveragePct/100,classes:coverage?.classes},
    {value:String(ownerActions),label:"Owner actions"}
  ]}/>:null;

  return <OwnerPage active="Site Health" email={email} siteId={siteId}
    kicker={["System Health",site?.name]}
    title="Can WatchLog observe this site?"
    actions={<><SiteSelect sites={sites} value={siteId} onChange={choose}/><button type="button" className="ow-btn quiet" onClick={()=>refresh(siteId)} disabled={loading||!siteId} aria-busy={loading}>{loading?"Refreshing…":"Refresh"}</button></>}
    rail={rail} summary={summary}>
    {error&&<Notice tone="bad">{error}</Notice>}
    {!ctx?(loading?<Loading label="Checking monitoring health"/>:<Empty title="No site to check yet.">Add a site in Account, then connect it in Setup & Support.</Empty>):<>
      <Lead tone={tone} title={title} body={copy} action={cta}/>

      <Section first title="Needs attention" count={recorderIssues.length+cameraFaults.length+unreported.length+(!online&&ever?1:0)||null}>
        {!ever&&!online?<Empty title="Health information appears after WatchLog connects to the site."/>
        :<div className="ow-rows">
          {!online&&<Row tone="bad" title="WatchLog connection lost" body="Check that the WatchLog computer at the site is switched on and online." meta={["Since "+(siteTime(lastSeen,tz,true)||"unknown")+" · site time","Owner action required"]}/>}
          {recorderIssues.map(r=>{const v=recorderView(r);return <Row key={"rec-"+r.id} tone={v.tone} title={r.name+" · "+v.label} body={v.action} meta={[impact.camerasAffectedBy(r)+" camera"+(impact.camerasAffectedBy(r)===1?"":"s")+" affected","Owner action required"]}/>})}
          {cameraFaults.slice(0,8).map((f,i)=>{const v=faultView(f);return <Row key={f.id||i} tone="warn" title={f.camera?f.camera+" · "+v.label:v.label} body={v.action} meta={[f.camera?"Camera":"Camera system",v.owner?"Owner action required":"WatchLog is checking"]}/>})}
          {unreported.map(c=><Row key={"cam-"+c.key} tone="warn" title={c.name+" · "+(c.health.tone==="ok"?"Recording needs attention":c.health.label)} body={c.action} meta={["Camera","Owner action required"]}/>)}
          {online&&!recorderIssues.length&&!cameraFaults.length&&!unreported.length&&(stats.healthUnknown||stats.recordingUnknown)?<Row tone="unknown" title="Some camera states are Not verified" body="No current fault is reported, but some health or recording states are still unverified. Check the cameras below before treating the picture as complete." meta={["No owner action yet"]}/>:null}
          {online&&!recorderIssues.length&&!cameraFaults.length&&!unreported.length&&!stats.healthUnknown&&!stats.recordingUnknown&&<Row tone="ok" title="No current monitoring fault is reported" meta={["No owner action required"]}/>}
        </div>}
      </Section>

      <Section title="What could not be verified today" note={"Since midnight · site time"+(coveragePct!==null?" · "+coveragePct+"% verified":"")}>
        {coveragePct===null?<Empty title="Today's coverage is not verified yet.">Unverified time is not treated as quiet time.</Empty>
        :gaps.length?<div className="ow-rows">{gaps.slice(0,6).map((g,i)=><Row key={i} compact tone={g.ongoing?"bad":"unknown"} title={siteTime(g.start,tz)+" – "+(g.ongoing?"now":siteTime(g.end,tz))} body={gapCause(g.cause)} meta={[duration(g.seconds),g.ongoing?"Ongoing":"Not verified"]}/>)}
          {truth.unverifiedSeconds!==null&&truth.unverifiedSeconds-gaps.reduce((n,g)=>n+g.seconds,0)>60?<Row compact tone="unknown" title={duration(truth.unverifiedSeconds-gaps.reduce((n,g)=>n+g.seconds,0))+" more could not be verified today"} body="Exact times for this time are not available." meta={["Not verified"]}/>:null}</div>
        :truth.partial?<div className="ow-rows"><Row compact tone="unknown" title={(truth.unverifiedSeconds!==null?duration(truth.unverifiedSeconds):(100-coveragePct)+"% of today")+" could not be verified today"} body="Exact times are not available for this site. Unverified time is not treated as quiet time." meta={["Since midnight · site time","Not verified"]}/></div>
        :truth.fullyVerified?<Row compact tone="verified" title="No unverified period today" meta={[coveragePct+"% verified since midnight"]}/>
        :<Empty title="Today's coverage is not verified yet.">Unverified time is not treated as quiet time.</Empty>}
      </Section>

      <Section title="Cameras" count={cams.length||null} note="Health and recording are shown separately so one confirmed state never hides an unknown one.">
        {cams.length?<table className="ow-table">
          <thead><tr><th>Camera</th>{multiRecorder&&<th>Recorder</th>}<th>Area</th><th>Health</th><th>Recording</th><th>Action</th></tr></thead>
          <tbody>{ledger.map(c=><tr key={c.key}>
            <td><b>{c.name}</b></td>
            {multiRecorder&&<td>{c.recorder||"Not verified"}</td>}
            <td className={c.area==="Purpose not set"?"ow-muted":""}>{c.area}</td>
            <td><Status tone={c.health.tone}>{c.health.label}</Status>{c.health.note&&<small>{c.health.note}</small>}</td>
            <td><Status tone={c.recording.tone}>{c.recording.label}</Status>{c.recording.note&&<small>{c.recording.note}</small>}</td>
            <td className={c.attention?"":"ow-muted"}>{c.action}</td>
          </tr>)}</tbody>
        </table>:<Empty title="No monitored cameras yet." action={<a className="ow-btn quiet small" href={withSite("/setup/",siteId)}>Choose cameras</a>}/>}
      </Section>

      <Section title={multiRecorder?"Recorders":"Camera system"} count={multiRecorder?recorderRows.length:null}>
        <div className="ow-rows">
          {recorderRows.length?recorderRows.map(r=>{const v=recorderView(r);return <Row key={r.id} compact tone={v.tone} title={r.name} body={v.body} meta={[r.camera_count+" camera"+(r.camera_count===1?"":"s"),r.checked_at?"Checked "+ago(r.checked_at):"Not verified yet"]} action={<Status tone={v.tone}>{v.label}</Status>}/>}):<Row compact tone={system.identified?"verified":"unknown"} title="Recorder" body={system.identified?"Camera system identity confirmed":"Not identified yet"} action={<Status tone={system.identified?"verified":"unknown"}>{system.identified?"Confirmed":"Not confirmed"}</Status>}/>}
          <Row compact tone={connectionTone} title="WatchLog connection" body={ever?"Last contact "+ago(lastSeen):"Not connected yet"} action={<Status tone={connectionTone}>{connectionWord}</Status>}/>
        </div>
      </Section>

      <Section title="Ask WatchLog" className="ow-narrow-only">
        <AskLinks siteId={siteId} prompts={["Is monitoring fully working at this site right now?","Which cameras need attention and why?"]}/>
      </Section>
    </>}
  </OwnerPage>;
}
