"use client";

import {useCallback,useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {Nav,requireTenant} from "../shell";
import {rememberSite,selectedSiteId,withSite} from "../site-context";
import ui from "../portal.module.css";
import styles from "./customer.module.css";

function human(v){return String(v||"Incident").replace(/^analytic_/,"").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase())}
function when(v,timeZone){if(!v)return"Not recorded";try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(v))+" · site time"}catch{return String(v)}}
function duration(v){const n=Math.max(0,Math.round(Number(v||0)));if(!n)return"—";if(n<60)return n+"s";const m=Math.floor(n/60),s=n%60;return s?m+"m "+s+"s":m+"m"}
function severityClass(v){const x=String(v||"").toLowerCase();return x==="critical"?styles.critical:x==="warning"||x==="high"?styles.warning:x==="info"||x==="low"?styles.info:styles.neutral}
function summaryText(r){const s=r?.summary||{};return s.headline||s.summary||s.description||r?.detail?.summary||r?.detail?.description||human(r?.incident_type)+" was grouped into one incident episode for review."}
function evidenceLabel(r){
  const s=r?.summary||{},footage=String(s.footage_status||"").toLowerCase();
  if(footage==="ready")return"Evidence ready";
  if(footage==="retrieving")return"Retrieving evidence";
  if(footage==="incomplete")return"Evidence incomplete";
  if(footage==="not_available")return"Video not available";
  const life=String(r?.lifecycle_state||"").toLowerCase();
  if(life==="active"||life==="escalated")return"Incident active";
  if(life==="evidence_processing")return"Preparing evidence";
  if(life==="report_ready")return"Review ready";
  return human(life||"Recorded");
}
function evidenceState(v){const x=String(v||"").toLowerCase();return x==="ready"?"s-ok":x==="failed"?"s-bad":x==="pending"||x==="processing"?"s-warn":"s-unk"}
function bytesLabel(value){const n=Number(value||0);if(!n)return"";if(n<1024*1024)return Math.round(n/1024)+" KB";return(n/1024/1024).toFixed(1)+" MB"}
function decodeB64(value){const raw=atob(value||"");const out=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)out[i]=raw.charCodeAt(i);return out}
async function digestHex(blob){const digest=await crypto.subtle.digest("SHA-256",await blob.arrayBuffer());return Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,"0")).join("")}

export default function CustomerIncidents(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[role,setRole]=useState("viewer");
  const[days,setDays]=useState(7);
  const[rows,setRows]=useState(null);
  const[selected,setSelected]=useState(null);
  const[detail,setDetail]=useState(null);
  const[images,setImages]=useState({});
  const[busy,setBusy]=useState("");
  const[note,setNote]=useState("");
  const[error,setError]=useState("");

  const loadRows=useCallback(async(id,windowDays,keepId=null)=>{
    if(!id)return;
    setRows(null);setError("");
    const r=await supabase().rpc("wl_operations_incidents_v2",{p_site_id:id,p_days:windowDays,p_review_status:null});
    if(r.error){setError(say(r.error));setRows([]);return}
    const list=Array.isArray(r.data)?r.data:[];
    setRows(list);
    const next=(keepId&&list.find(x=>String(x.id)===String(keepId)))||list[0]||null;
    setSelected(next);
  },[]);

  useEffect(()=>{(async()=>{
    const g=await requireTenant();if(!g)return;
    setEmail(g.session.user.email||"");
    const [siteResult,roleResult]=await Promise.all([supabase().rpc("wl_sites"),supabase().rpc("wl_my_role")]);
    if(siteResult.error){setError(say(siteResult.error));return}
    const list=siteResult.data||[];
    setSites(list);setRole(roleResult.error?"viewer":(roleResult.data||"viewer"));
    const requested=new URLSearchParams(location.search).get("site")||selectedSiteId()||list[0]?.id||"";
    const id=list.some(x=>String(x.id)===String(requested))?requested:(list[0]?.id||"");
    setSiteId(id);
    if(id){rememberSite(id,list.find(x=>String(x.id)===String(id))?.name||"");await loadRows(id,days)}
  })()},[loadRows]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(()=>{if(!selected?.id){setDetail(null);return}(async()=>{
    const r=await supabase().rpc("wl_operations_incident_detail",{p_incident_id:selected.id});
    if(r.error){setError(say(r.error));setDetail(null);return}
    setDetail(r.data||null);
  })()},[selected?.id]);

  async function changeDays(value){setDays(value);await loadRows(siteId,value,selected?.id)}
  async function chooseSite(id){
    setSiteId(id);setDetail(null);setImages({});
    const row=sites.find(x=>String(x.id)===String(id));
    rememberSite(id,row?.name||"");
    history.replaceState(null,"","/incidents/?site="+encodeURIComponent(id));
    await loadRows(id,days);
  }

  async function act(fn,reason){
    if(!selected?.id)return;
    setBusy(fn);setError("");setNote("");
    const args=reason===undefined?{p_id:selected.id}:{p_id:selected.id,p_reason:reason};
    const r=await supabase().rpc(fn,args);
    setBusy("");
    if(r.error){setError(say(r.error));return}
    setNote(fn.includes("acknowledge")?"Incident acknowledged.":fn.includes("dismiss")?"Incident dismissed.":"Incident resolved.");
    await loadRows(siteId,days,selected.id);
  }

  async function viewStill(row){
    setBusy("still:"+row.id);setNote("");
    const r=await supabase().rpc("wl_operations_incident_still_image",{p_still_id:row.id});
    setBusy("");
    if(r.error){setError(say(r.error));return}
    if(r.data?.image_b64)setImages(current=>({...current,[row.id]:"data:"+(r.data.content_type||"image/jpeg")+";base64,"+r.data.image_b64}));
    else setNote("This evidence image is no longer available.");
  }

  async function downloadClip(clip){
    if(!clip?.request_id||clip.status!=="ready")return;
    setBusy("clip:"+clip.request_id);setNote("Preparing evidence footage…");
    try{
      const parts=[];
      for(let seq=0;seq<Number(clip.chunks||0);seq++){
        const r=await supabase().rpc("wl_incident_clip_chunk",{p_request_id:clip.request_id,p_sequence_no:seq});
        if(r.error||!r.data?.data_b64)throw new Error("Footage data is incomplete.");
        parts.push(decodeB64(r.data.data_b64));
      }
      if(!parts.length)throw new Error("No footage data is available.");
      const blob=new Blob(parts,{type:clip.content_type||"application/octet-stream"});
      if(Number(clip.bytes||0)>0&&blob.size!==Number(clip.bytes))throw new Error("Footage size check failed.");
      if(!clip.sha256||await digestHex(blob)!==clip.sha256)throw new Error("Footage integrity check failed.");
      const url=URL.createObjectURL(blob),a=document.createElement("a");
      a.href=url;a.download="WatchLog-incident-"+selected.id+"."+(clip.file_extension||"dav");
      document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      setNote("Evidence footage downloaded. The temporary WatchLog copy expires automatically.");
    }catch{
      setNote("WatchLog could not prepare this footage download. Request it again if needed.");
    }finally{setBusy("")}
  }

  const site=sites.find(s=>String(s.id)===String(siteId));
  const lifecycle=detail?.lifecycle||[];
  const evidence=detail?.evidence||{stills:[],clips:[]};
  const reviewCount=useMemo(()=>rows?.filter(x=>x.review_required||["candidate","open","acknowledged"].includes(String(x.review_status||"").toLowerCase())).length||0,[rows]);
  const canManage=role==="owner"||role==="admin";
  const actionable=selected&&["candidate","open","acknowledged"].includes(String(selected.review_status||"").toLowerCase());

  return <div className="shell">
    <Nav active="Incidents" email={email} currentSiteId={siteId}/>
    <main className={"main "+styles.page}>
      <header className="target-page-head">
        <div><div className="target-eyebrow">Incident review</div><h1>Security review for {site?.name||"this site"}</h1><p>Review meaningful incident episodes, their evidence and the action taken—without mixing them with routine camera events.</p></div>
        <div className="target-actions">
          {sites.length>1&&<select value={siteId} onChange={e=>chooseSite(e.target.value)}>{sites.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select>}
          <select value={days} onChange={e=>changeDays(Number(e.target.value))}><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option></select>
        </div>
      </header>
      {error&&<div className="err">{error}</div>}
      {note&&<div className="ok-note">{note}</div>}
      {rows===null?<div className={styles.empty}>Checking recent incidents…</div>:rows.length===0?<div className={styles.empty}>No incident episodes are recorded for the available information in this period. Check monitoring coverage before treating this as a complete security picture.</div>:<section className={styles.layout}>
        <div className={styles.queue}>
          <div className={styles.queueHead}><h2>Needs review</h2><p>{reviewCount?reviewCount+" incident"+(reviewCount===1?"":"s")+" currently need attention.":"Recent incidents and their review state."}</p></div>
          <div className={styles.queueList}>{rows.map(r=><button className={styles.item+" "+(selected?.id===r.id?styles.selected:"")} key={r.id} onClick={()=>setSelected(r)}>
            <div className={styles.itemTop}><span className={styles.itemTitle}>{human(r.incident_type)}</span><span className={styles.severity+" "+severityClass(r.severity)}>{human(r.severity||"Recorded")}</span></div>
            <div className={styles.itemMeta}><span>{r.camera||"Site"}</span><span>{when(r.started_at||r.opened_at,site?.timezone)}</span><span>{human(r.review_status||"Open")}</span></div>
            <div className={styles.itemSummary}>{summaryText(r)}</div>
          </button>)}</div>
        </div>

        <article className={styles.detail}>{selected&&<><div className={styles.detailHead}><div className={styles.titleRow}><div><h2 className={styles.incidentTitle}>{human(selected.incident_type)}</h2><div className={styles.badges}><span className={styles.severity+" "+severityClass(selected.severity)}>{human(selected.severity||"Recorded")}</span><span className={styles.badge+" "+styles.review}>{human(selected.review_status||"Open")}</span><span className={styles.badge}>{evidenceLabel(selected)}</span></div></div></div></div>
          <div className={styles.detailBody}>
            <div className={styles.facts}>
              <div className={styles.fact}><span>Camera</span><b>{selected.camera||"Site-wide"}</b></div>
              <div className={styles.fact}><span>Started</span><b>{when(selected.started_at||selected.opened_at,site?.timezone)}</b></div>
              <div className={styles.fact}><span>Duration</span><b>{duration(selected.duration_seconds)}</b></div>
              <div className={styles.fact}><span>Review state</span><b>{human(selected.review_status||"Open")}</b></div>
            </div>

            <div className={styles.summaryCard}><span>Incident summary</span><p>{summaryText(selected)}</p></div>

            {actionable&&<section className={styles.reviewActions}>
              <div><span>Management action</span><h3>Record the outcome</h3><p>Owners and Admins can acknowledge, resolve or dismiss this incident. The action is recorded in the incident lifecycle.</p></div>
              {canManage?<div className={styles.reviewButtons}>
                {["candidate","open"].includes(String(selected.review_status||"").toLowerCase())&&<button className="small" disabled={!!busy} onClick={()=>act("wl_acknowledge_operations_incident")}>Acknowledge</button>}
                <button className="small" disabled={!!busy} onClick={()=>act("wl_resolve_operations_incident")}>Resolve</button>
                <button className="ghost small" disabled={!!busy} onClick={()=>{const why=window.prompt("Why are you dismissing this incident?");if(why!==null)act("wl_dismiss_operations_incident",why)}}>Dismiss</button>
              </div>:<span className="muted">Owner or Admin access is required to update the incident.</span>}
            </section>}

            <h3 className={styles.sectionTitle}>Supporting evidence</h3>
            <div className={styles.evidenceGrid}>
              {(evidence.stills||[]).map(still=><div className={styles.evidenceCard} key={"still:"+still.id}>
                <div className={styles.evidenceTop}><div><b>Evidence image</b><small>{still.captured_at?when(still.captured_at,site?.timezone):"Capture time not available"}</small></div><span className={"pill "+evidenceState(still.status)}>{human(still.status)}</span></div>
                {images[still.id]?<img src={images[still.id]} alt="Incident evidence"/>:still.status==="ready"&&still.has_image?<button className="ghost small" disabled={busy==="still:"+still.id} onClick={()=>viewStill(still)}>{busy==="still:"+still.id?"Loading…":"View image"}</button>:<p>{still.error?"This evidence image is unavailable.":"No image is available for this evidence item."}</p>}
              </div>)}
              {(evidence.clips||[]).map(clip=><div className={styles.evidenceCard} key={"clip:"+clip.request_id}>
                <div className={styles.evidenceTop}><div><b>Evidence footage</b><small>{clip.start_at&&clip.end_at?when(clip.start_at,site?.timezone)+" – "+when(clip.end_at,site?.timezone):"Requested footage window"}</small></div><span className={"pill "+evidenceState(clip.status)}>{human(clip.status)}</span></div>
                {clip.status==="ready"?<button className="ghost small" disabled={busy==="clip:"+clip.request_id} onClick={()=>downloadClip(clip)}>{busy==="clip:"+clip.request_id?"Preparing…":"Download "+(bytesLabel(clip.bytes)||"footage")}</button>:<p>{clip.status==="failed"?"This footage request could not be completed.":clip.status==="unsupported"?"This camera system did not provide footage for this request.":"WatchLog will show the footage here when the request is ready."}</p>}
              </div>)}
              {!(evidence.stills||[]).length&&!(evidence.clips||[]).length&&<div className={styles.emptyTimeline}>No supporting image or footage is attached to this incident yet.</div>}
            </div>

            <h3 className={styles.sectionTitle}>Incident lifecycle</h3>
            {lifecycle.length?<div className={styles.timeline}>{lifecycle.map((x,i)=><div className={styles.timelineRow} key={String(x.event)+"-"+String(x.at)+"-"+i}><span className={styles.timelineDot}/><b>{human(x.event)}</b><small>{when(x.at,site?.timezone)}</small></div>)}</div>:<div className={styles.emptyTimeline}>No additional lifecycle transitions are recorded for this incident yet.</div>}

            <div className={styles.actions}>
              <a className={ui.primaryLink} href={withSite("/ai/?prompt="+encodeURIComponent("Explain this incident: "+human(selected.incident_type)+" at "+(selected.camera||"this site")+". Tell me what matters and what action is appropriate."),siteId)}>Ask WatchLog</a>
              <a className={ui.secondaryLink} href={withSite("/incidents/evidence/",siteId)}>Open camera evidence</a>
            </div>
          </div>
        </>}</article>
      </section>}
    </main>
  </div>;
}
