"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { requireTenant } from "../shell";
import { withSite } from "../site-context";
import { OwnerPage, Section, Status, Empty, Loading, Notice } from "../owner/ui";
import styles from "./review.module.css";

function humanType(t){if(!t)return"Event";return String(t).replace(/^analytic_/,"").replaceAll("_"," ").replace(/\b\w/g,(c)=>c.toUpperCase());}
function ago(ts){if(!ts)return"unknown";const s=Math.max(0,Math.round((Date.now()-Date.parse(ts))/1000));if(s<60)return`${s}s ago`;if(s<3600)return`${Math.round(s/60)}m ago`;if(s<86400)return`${Math.round(s/3600)}h ago`;return`${Math.round(s/86400)}d ago`;}
function exact(ts,timeZone){if(!ts)return"";try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",year:"numeric",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(ts))+" · site time"}catch{return String(ts||"")}}
function clock(ts,timeZone){if(!ts)return"";try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(ts))}catch{return""}}
function sourceLabel(row){if(row?.native_ai)return"Recorder AI";if(row?.event_source==="watchlog_local_ai")return"WatchLog local AI";return"Recorder event";}
function clipLabel(status){if(!status)return"Not requested";return({pending:"Requested",processing:"Retrieving",ready:"Ready",unsupported:"Unsupported",failed:"Failed",expired:"Expired"})[status]||humanType(status);}
function clipTone(status){return status==="ready"?"verified":status==="failed"?"bad":"unknown";}
function bytesLabel(value){const n=Number(value||0);if(!n)return"";if(n<1024*1024)return`${Math.round(n/1024)} KB`;return`${(n/1024/1024).toFixed(1)} MB`;}
function decodeB64(value){const raw=atob(value||"");const out=new Uint8Array(raw.length);for(let i=0;i<raw.length;i+=1)out[i]=raw.charCodeAt(i);return out;}
async function digestHex(blob){if(!globalThis.crypto?.subtle)throw new Error("This browser cannot verify the footage checksum. Use a current browser and try again.");const digest=await crypto.subtle.digest("SHA-256",await blob.arrayBuffer());return Array.from(new Uint8Array(digest)).map((b)=>b.toString(16).padStart(2,"0")).join("");}
function param(name){try{return new URLSearchParams(location.search).get(name)||"";}catch{return"";}}
function showDetail(){try{if(window.matchMedia("(max-width:1100px)").matches)document.getElementById("event-detail")?.scrollIntoView({block:"start",behavior:"smooth"});}catch{}}

export default function Incidents(){
  const[email,setEmail]=useState(""),[rows,setRows]=useState(null),[sites,setSites]=useState([]),[err,setErr]=useState(""),[days,setDays]=useState(7),[site,setSite]=useState(()=>param("site")),[type,setType]=useState(""),[selected,setSelected]=useState(null),[role,setRole]=useState("viewer"),[clip,setClip]=useState(null),[clipBusy,setClipBusy]=useState(false),[clipNote,setClipNote]=useState("");
  const[focusEvent]=useState(()=>param("event"));
  const shots=useRef(new Map());const[,force]=useState(0);
  const load=useCallback(async(d,s,t)=>{const g=await requireTenant();if(!g)return;setEmail(g.session.user.email||"");const sb=supabase();const[inc,si,me]=await Promise.all([sb.rpc("wl_incidents",{p_days:d,p_site:s||null,p_type:t||null}),sb.rpc("wl_sites"),sb.rpc("wl_my_role")]);if(inc.error||si.error||me.error){setErr(say(inc.error||si.error||me.error));return;}setErr("");setRows(inc.data||[]);setSites(si.data||[]);setRole(me.data||"viewer");},[]);
  useEffect(()=>{load(days,site,type);},[days,site,type,load]);
  async function loadShot(eventId){if(shots.current.has(eventId))return;shots.current.set(eventId,null);const{data}=await supabase().rpc("wl_portal_snapshot",{p_event_id:eventId});shots.current.set(eventId,data?.image_b64?`data:${data.content_type||"image/jpeg"};base64,${data.image_b64}`:false);force((n)=>n+1);}
  useEffect(()=>{(rows||[]).filter((r)=>r.has_snapshot).slice(0,24).forEach((r)=>loadShot(r.event_id));},[rows]);
  useEffect(()=>{if(!rows?.length){setSelected(null);return;}setSelected((cur)=>cur&&rows.some((r)=>r.event_id===cur.event_id)?rows.find((r)=>r.event_id===cur.event_id):(focusEvent&&rows.find((r)=>String(r.event_id)===String(focusEvent)))||rows[0]);},[rows,focusEvent]);
  useEffect(()=>{if(selected?.has_snapshot)loadShot(selected.event_id);},[selected]);
  const refreshClip=useCallback(async(eventId)=>{if(!eventId){setClip(null);return;}const{data,error}=await supabase().rpc("wl_incident_clip_status",{p_event_id:eventId});if(error){if(/wl_incident_clip_status|schema cache|function/i.test(error.message||"")){setClip(null);return;}setClipNote(say(error));return;}setClip(data||null);},[]);
  useEffect(()=>{setClip(null);setClipNote("");if(selected?.event_id)refreshClip(selected.event_id);},[selected?.event_id,refreshClip]);
  useEffect(()=>{if(!selected?.event_id||!(clip?.status==="pending"||clip?.status==="processing"))return;const timer=setInterval(()=>refreshClip(selected.event_id),5000);return()=>clearInterval(timer);},[clip?.status,selected?.event_id,refreshClip]);
  const types=Array.from(new Set((rows||[]).map((r)=>r.event_type))).sort();
  function zoneFor(row){const id=row?.site_id||site;return sites.find(s=>String(s.id)===String(id))?.timezone||"Asia/Karachi"}
  const selectedZone=zoneFor(selected);
  const canRequest=role==="owner"||role==="admin";
  const siteName=sites.find(s=>String(s.id)===String(site))?.name;
  const periodLabel=days===1?"last 24 hours":`last ${days} days`;

  async function requestFootage(){if(!selected?.event_id||!canRequest)return;setClipBusy(true);setClipNote("");const{error}=await supabase().rpc("wl_request_incident_clip",{p_event_id:selected.event_id,p_pre_seconds:10,p_post_seconds:20});if(error){setClipNote(say(error));setClipBusy(false);return;}await refreshClip(selected.event_id);setClipNote("WatchLog requested a short evidence window around this camera event.");setClipBusy(false);}

  async function downloadFootage(){if(!clip?.request_id||clip.status!=="ready")return;setClipBusy(true);setClipNote("Preparing the temporary evidence footage download…");try{const parts=[];for(let seq=0;seq<Number(clip.chunks||0);seq+=1){const{data,error}=await supabase().rpc("wl_incident_clip_chunk",{p_request_id:clip.request_id,p_sequence_no:seq});if(error||!data?.data_b64)throw error||new Error(`Missing footage chunk ${seq}`);parts.push(decodeB64(data.data_b64));}if(!parts.length)throw new Error("No footage data is available.");const blob=new Blob(parts,{type:clip.content_type||"application/octet-stream"});if(Number(clip.bytes||0)>0&&blob.size!==Number(clip.bytes))throw new Error("Footage size check failed. Request it again.");if(!clip.sha256)throw new Error("Footage checksum is missing. Request it again.");const digest=await digestHex(blob);if(digest!==clip.sha256)throw new Error("Footage integrity check failed. Request it again.");const url=URL.createObjectURL(blob);const anchor=document.createElement("a");anchor.href=url;anchor.download=`WatchLog-evidence-${selected.event_id}.${clip.file_extension||"dav"}`;document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);setClipNote(`Downloaded ${bytesLabel(blob.size)} of evidence footage. The temporary copy expires automatically.`);}catch(error){setClipNote(say(error));}finally{setClipBusy(false);}}

  const clipText=clip?.status==="ready"?`A temporary ${bytesLabel(clip.bytes)} evidence file is ready. It expires ${exact(clip.expires_at,selectedZone)}.`:clip?.status==="processing"?"WatchLog is retrieving the requested time window from the camera system.":clip?.status==="pending"?"The footage request is waiting for the site to respond.":clip?.status==="unsupported"?"This recorder did not provide footage for this request.":clip?.status==="failed"?(clip.error_message||"The recorder could not provide this footage window."):clip?.status==="expired"?"The temporary evidence copy expired. The original CCTV recording remains on the recorder.":"Request a short recording window around this camera event when you need it.";
  const selectedShot=selected?shots.current.get(selected.event_id):undefined;

  return <OwnerPage active="Control Room" email={email} siteId={site||undefined}
    kicker={["Cameras & Evidence",siteName||"All sites"]}
    title="Camera evidence"
    actions={<a className="ow-btn quiet" href={site?withSite("/control-room/",site):"/control-room/"}>Cameras</a>}>
    {err&&<Notice tone="bad">{err}</Notice>}
    <div className={styles.filters} role="group" aria-label="Camera evidence filters">
      <label className="ow-field">Site<select value={site} onChange={(e)=>setSite(e.target.value)}><option value="">All sites</option>{sites.map((s)=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label className="ow-field">Type<select value={type} onChange={(e)=>setType(e.target.value)}><option value="">All types</option>{types.map((t)=><option key={t} value={t}>{humanType(t)}</option>)}</select></label>
      <label className="ow-field">Date range<select value={days} onChange={(e)=>setDays(Number(e.target.value))}><option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select></label>
    </div>

    {rows===null?<Loading label="Loading camera evidence"/>:rows.length===0?<Empty title={`No reviewable camera events in the ${periodLabel}.`}>This does not make a claim about time WatchLog could not verify.</Empty>:<section className={styles.layout}>
      <Section first title="Camera event history" count={rows.length} note="Camera events, not incidents. Select one to review its evidence.">
        <div className={styles.list}>{rows.map((r)=>{const shot=shots.current.get(r.event_id),isSelected=selected?.event_id===r.event_id,tz=zoneFor(r);return <button type="button" className={`${styles.item} ${styles.withThumb}`} key={r.event_id} onClick={()=>{setSelected(r);showDetail();}} aria-pressed={isSelected}>
          <i className={`${styles.tick} ${r.has_snapshot&&shot!==false?styles.verified:""}`} aria-hidden="true"/>
          <span className={`${styles.thumb} ${r.has_snapshot&&typeof shot==="string"?"":styles.none}`}>{r.has_snapshot&&typeof shot==="string"?<img src={shot} alt={`Camera view from ${r.camera||"camera"}`}/>:<span>{r.has_snapshot?(shot===false?"Unavailable":"Loading"):"No image"}</span>}</span>
          <span className={styles.itemMain}>
            <span className={styles.itemTitle}>{humanType(r.event_type)}</span>
            <span className={styles.itemMeta}><span>{clock(r.device_ts,tz)} · {r.camera||"Unassigned camera"}</span>{!site&&r.site&&<span>{r.site}</span>}<span title={exact(r.device_ts,tz)}>{ago(r.device_ts)}</span></span>
          </span>
        </button>;})}</div>
      </Section>

      <article className={styles.detail} id="event-detail" aria-live="polite">{selected?<>
        <div className={styles.detailHead}>
          <h2>{selected.camera||"Unassigned camera"} · {humanType(selected.event_type)}</h2>
          <div className={styles.badges}>
            <Status tone={selected.has_snapshot&&selectedShot!==false?"verified":"unknown"}>{selected.has_snapshot&&selectedShot!==false?"Available for review":"No image attached"}</Status>
            <span className="ow-muted" style={{fontSize:12.5}}>{selected.site} · {clock(selected.device_ts,selectedZone)} · {ago(selected.device_ts)}</span>
          </div>
        </div>
        <div className={`${styles.frame} ${selected.has_snapshot&&typeof selectedShot==="string"?"":styles.none}`}>{selected.has_snapshot?(typeof selectedShot==="string"?<img src={selectedShot} alt={`Evidence from ${selected.camera||"camera"}`}/>:<span>{selectedShot===false?"This evidence image is unavailable.":"Loading the evidence image…"}</span>):<span>This camera event does not have an image.</span>}</div>
        <dl className={styles.facts}>
          <div><dt>Activity</dt><dd>{humanType(selected.event_type)}</dd></div>
          <div><dt>Source</dt><dd>{sourceLabel(selected)}</dd></div>
          <div><dt>Recorded</dt><dd>{exact(selected.device_ts,selectedZone)}</dd></div>
        </dl>

        <section className={styles.block} aria-label="Evidence footage">
          <h3>Evidence footage</h3>
          <Status tone={clipTone(clip?.status)}>{clipLabel(clip?.status)}</Status>
          <p style={{marginTop:6}}>{clipText}</p>
          <div className={styles.buttons}>
            {clip?.status==="ready"?<button type="button" className="ow-btn small" disabled={clipBusy} aria-busy={clipBusy} onClick={downloadFootage}>{clipBusy?"Preparing…":"Download footage"}</button>:canRequest&&!(clip?.status==="pending"||clip?.status==="processing")?<button type="button" className="ow-btn small" disabled={clipBusy} aria-busy={clipBusy} onClick={requestFootage}>{clipBusy?"Requesting…":clip?.status==="expired"||clip?.status==="failed"||clip?.status==="unsupported"?"Request again":"Retrieve footage"}</button>:null}
            {(clip?.status==="pending"||clip?.status==="processing")&&<button type="button" className="ow-btn small quiet" onClick={()=>refreshClip(selected.event_id)}>Check status</button>}
          </div>
          {!canRequest&&<p className="ow-muted" style={{fontSize:12,marginTop:10}}>Owners and Admins can request recorder footage. Viewer access remains read-only.</p>}
          {clipNote&&<p className="ow-muted" style={{fontSize:12.5,marginTop:10}} role="status">{clipNote}</p>}
        </section>
        <p className="ow-muted" style={{fontSize:12,marginTop:16}}>Normal CCTV recording stays on the recorder. Footage is retrieved only when requested.</p>
      </>:<Empty title="Select a camera event to review its evidence."/>}</article>
    </section>}
  </OwnerPage>;
}
