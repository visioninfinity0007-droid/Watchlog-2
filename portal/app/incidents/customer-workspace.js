"use client";

import {useCallback,useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId,withSite} from "../site-context";
import {OwnerPage,SiteSelect,Lead,Status,Empty,Loading,Notice} from "../owner/ui";
import styles from "./review.module.css";

function human(v){return String(v||"Incident").replace(/^analytic_/,"").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase())}
function when(v,timeZone){if(!v)return"Not recorded";try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(v))+" · site time"}catch{return String(v)}}
function duration(v){const n=Math.max(0,Math.round(Number(v||0)));if(!n)return"—";if(n<60)return n+"s";const m=Math.floor(n/60),s=n%60;return s?m+"m "+s+"s":m+"m"}
function severityTone(v){const x=String(v||"").toLowerCase();return x==="critical"?"bad":x==="warning"||x==="high"?"warn":"info"}
function reviewTone(v){const x=String(v||"open").toLowerCase();return x==="resolved"?"ok":x==="dismissed"?"unknown":"warn"}
function needsReview(r){return Boolean(r?.review_required)||["candidate","open","acknowledged"].includes(String(r?.review_status||"").toLowerCase())}
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
function evidenceLabelTone(r){const x=evidenceLabel(r);return x==="Evidence ready"||x==="Review ready"?"verified":x==="Evidence incomplete"||x==="Incident active"?"warn":"unknown"}
function evidenceState(v){const x=String(v||"").toLowerCase();return x==="ready"?"verified":x==="failed"?"bad":"unknown"}
function bytesLabel(value){const n=Number(value||0);if(!n)return"";if(n<1024*1024)return Math.round(n/1024)+" KB";return(n/1024/1024).toFixed(1)+" MB"}
function decodeB64(value){const raw=atob(value||"");const out=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)out[i]=raw.charCodeAt(i);return out}
async function digestHex(blob){const digest=await crypto.subtle.digest("SHA-256",await blob.arrayBuffer());return Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,"0")).join("")}
function showDetail(){try{if(window.matchMedia("(max-width:1100px)").matches)document.getElementById("incident-detail")?.scrollIntoView({block:"start",behavior:"smooth"})}catch{}}

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
    const next=(keepId&&list.find(x=>String(x.id)===String(keepId)))||list.find(needsReview)||list[0]||null;
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
  const tz=site?.timezone;
  const lifecycle=detail?.lifecycle||[];
  const evidence=detail?.evidence||{stills:[],clips:[]};
  const queue=useMemo(()=>rows?.filter(needsReview)||[],[rows]);
  const reviewCount=queue.length;
  const urgentCount=useMemo(()=>queue.filter(x=>String(x.severity||"").toLowerCase()==="critical").length,[queue]);
  const canManage=role==="owner"||role==="admin";
  const actionable=selected&&["candidate","open","acknowledged"].includes(String(selected.review_status||"").toLowerCase());
  const canAcknowledge=selected&&["candidate","open"].includes(String(selected.review_status||"").toLowerCase());
  const periodLabel=days===1?"last 24 hours":"last "+days+" days";

  // Exception queue first, already-handled incidents after it.
  const ordered=useMemo(()=>{
    const list=[...(rows||[])];
    return list.sort((a,b)=>(needsReview(a)?0:1)-(needsReview(b)?0:1));
  },[rows]);

  let lead=null;
  if(rows&&rows.length){
    if(reviewCount){
      const first=queue[0];
      lead={tone:urgentCount?"bad":"warn",title:reviewCount+" incident"+(reviewCount===1?" needs":"s need")+" review",body:"Start with "+human(first.incident_type)+" at "+(first.camera||"this site")+" · "+when(first.started_at||first.opened_at,tz)};
    }else{
      lead={tone:"ok",title:"No incident needs review",body:rows.length+" incident"+(rows.length===1?"":"s")+" in the "+periodLabel+", all handled."};
    }
  }

  return <OwnerPage active="Incidents" email={email} siteId={siteId}
    kicker={["Incident review",site?.name]}
    title={"Security review for "+(site?.name||"this site")}
    actions={<>
      <SiteSelect sites={sites} value={siteId} onChange={chooseSite}/>
      <select aria-label="Review period" value={days} onChange={e=>changeDays(Number(e.target.value))}><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option></select>
    </>}>
    {error&&<Notice tone="bad">{error}</Notice>}
    {note&&<Notice>{note}</Notice>}
    {rows===null?<Loading label="Checking recent incidents"/>:rows.length===0?<Empty title={"No incident episodes in the "+periodLabel+"."} action={<a className="ow-btn quiet small" href={withSite("/site-health/",siteId)}>Check monitoring coverage</a>}>Check monitoring coverage before treating this as a complete security picture.</Empty>:<>
      {lead&&<Lead tone={lead.tone} title={lead.title} body={lead.body}/>}
      <section className={styles.layout}>
        <div className={styles.queue}>
          <div className="ow-sec-head"><div><h2>Needs review<em>{reviewCount}</em></h2><p>{rows.length} incident{rows.length===1?"":"s"} in the {periodLabel}</p></div></div>
          <div className={styles.list}>{ordered.map(r=>{const isSel=selected?.id===r.id;return <button type="button" className={styles.item} key={r.id} aria-pressed={isSel} onClick={()=>{setSelected(r);showDetail()}}>
            <i className={styles.tick+" "+(styles[needsReview(r)?severityTone(r.severity):"unknown"]||"")} aria-hidden="true"/>
            <span className={styles.itemMain}>
              <span className={styles.itemTitle}>{human(r.incident_type)}</span>
              <span className={styles.itemMeta}><span>{r.camera||"Site"}</span><span>{when(r.started_at||r.opened_at,tz)}</span></span>
              <span className={styles.itemSummary}>{summaryText(r)}</span>
            </span>
            <span className={styles.itemSide}><Status tone={reviewTone(r.review_status)}>{human(r.review_status||"Open")}</Status></span>
          </button>})}</div>
        </div>

        <article className={styles.detail} id="incident-detail" aria-live="polite">{selected?<>
          <div className={styles.detailHead}>
            <h2>{human(selected.incident_type)}</h2>
            <div className={styles.badges}>
              <Status tone={severityTone(selected.severity)}>{human(selected.severity||"Recorded")}</Status>
              <Status tone={reviewTone(selected.review_status)}>{human(selected.review_status||"Open")}</Status>
              <Status tone={evidenceLabelTone(selected)}>{evidenceLabel(selected)}</Status>
            </div>
          </div>
          <dl className={styles.facts}>
            <div><dt>Camera</dt><dd>{selected.camera||"Site-wide"}</dd></div>
            <div><dt>Started</dt><dd>{when(selected.started_at||selected.opened_at,tz)}</dd></div>
            <div><dt>Duration</dt><dd>{duration(selected.duration_seconds)}</dd></div>
          </dl>
          <p className={styles.summary}>{summaryText(selected)}</p>

          {actionable&&<section className={styles.block} aria-label="Management action">
            <h3>Management action</h3>
            <p>Record the outcome. Every action is kept in the incident lifecycle.</p>
            {canManage?<div className={styles.buttons}>
              {canAcknowledge&&<button type="button" className="ow-btn small" disabled={!!busy} aria-busy={busy==="wl_acknowledge_operations_incident"} onClick={()=>act("wl_acknowledge_operations_incident")}>Acknowledge</button>}
              <button type="button" className={"ow-btn small"+(canAcknowledge?" quiet":"")} disabled={!!busy} aria-busy={busy==="wl_resolve_operations_incident"} onClick={()=>act("wl_resolve_operations_incident")}>Resolve</button>
              <button type="button" className="ow-btn small danger" disabled={!!busy} aria-busy={busy==="wl_dismiss_operations_incident"} onClick={()=>{const why=window.prompt("Why are you dismissing this incident?");if(why!==null)act("wl_dismiss_operations_incident",why)}}>Dismiss</button>
            </div>:<p className="ow-muted" style={{marginTop:6}}>Owner or Admin access is required to update the incident.</p>}
          </section>}

          <section className={styles.block} aria-label="Supporting evidence">
            <h3>Supporting evidence</h3>
            {(evidence.stills||[]).length||(evidence.clips||[]).length?<div className="ow-rows">
              {(evidence.stills||[]).map(still=><div className="ow-row compact" key={"still:"+still.id}>
                <i className={"ow-row-tick "+evidenceState(still.status)} aria-hidden="true"/>
                <div className="ow-row-main"><h3>Evidence image</h3>
                  <div className="ow-row-meta"><span>{still.captured_at?when(still.captured_at,tz):"Capture time not available"}</span><span><Status tone={evidenceState(still.status)}>{human(still.status)}</Status></span></div>
                  {images[still.id]&&<img className={styles.evidenceImg} src={images[still.id]} alt="Incident evidence"/>}
                </div>
                <div className="ow-row-act">{images[still.id]?null:still.status==="ready"&&still.has_image?<button type="button" className="ow-btn small quiet" disabled={busy==="still:"+still.id} aria-busy={busy==="still:"+still.id} onClick={()=>viewStill(still)}>{busy==="still:"+still.id?"Loading…":"View image"}</button>:<span className="ow-muted" style={{fontSize:12.5}}>{still.error?"Image unavailable":"No image"}</span>}</div>
              </div>)}
              {(evidence.clips||[]).map(clip=><div className="ow-row compact" key={"clip:"+clip.request_id}>
                <i className={"ow-row-tick "+evidenceState(clip.status)} aria-hidden="true"/>
                <div className="ow-row-main"><h3>Evidence footage</h3>
                  <div className="ow-row-meta"><span>{clip.start_at&&clip.end_at?when(clip.start_at,tz)+" – "+when(clip.end_at,tz):"Requested footage window"}</span><span><Status tone={evidenceState(clip.status)}>{human(clip.status)}</Status></span></div>
                  {clip.status!=="ready"&&<p>{clip.status==="failed"?"This footage request could not be completed.":clip.status==="unsupported"?"This camera system did not provide footage for this request.":"The footage appears here when the request is ready."}</p>}
                </div>
                <div className="ow-row-act">{clip.status==="ready"&&<button type="button" className="ow-btn small quiet" disabled={busy==="clip:"+clip.request_id} aria-busy={busy==="clip:"+clip.request_id} onClick={()=>downloadClip(clip)}>{busy==="clip:"+clip.request_id?"Preparing…":"Download "+(bytesLabel(clip.bytes)||"footage")}</button>}</div>
              </div>)}
            </div>:<p className="ow-muted">No supporting image or footage is attached to this incident yet.</p>}
          </section>

          <details className="ow-details" style={{marginTop:20}}>
            <summary>Incident lifecycle{lifecycle.length?" · "+lifecycle.length+" step"+(lifecycle.length===1?"":"s"):""}</summary>
            <div>{lifecycle.length?<div className="ow-rows">{lifecycle.map((x,i)=><div className="ow-row compact" key={String(x.event)+"-"+String(x.at)+"-"+i}><i className="ow-row-tick info" aria-hidden="true"/><div className="ow-row-main"><h3>{human(x.event)}</h3><div className="ow-row-meta"><span>{when(x.at,tz)}</span></div></div></div>)}</div>:<p className="ow-muted">No additional lifecycle steps are recorded for this incident yet.</p>}</div>
          </details>

          <div className={styles.foot}>
            <a className="ow-btn quiet small" href={withSite("/ai/?prompt="+encodeURIComponent("Explain this incident: "+human(selected.incident_type)+" at "+(selected.camera||"this site")+". Tell me what matters and what action is appropriate."),siteId)}>Ask WatchLog</a>
            <a href={withSite("/incidents/evidence/",siteId)}>Open camera evidence</a>
          </div>
        </>:<Empty title="Select an incident to review it."/>}</article>
      </section>
    </>}
  </OwnerPage>;
}
