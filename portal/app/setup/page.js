"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";
import styles from "./setup.module.css";

const INSTALLER_URL=process.env.NEXT_PUBLIC_INSTALLER_URL||"";
const SITE_TYPES=[
  ["office","Office","Workplace, head office, professional site"],
  ["warehouse","Warehouse","Storage, dispatch and loading operations"],
  ["factory","Factory","Production floor and restricted operations"],
  ["retail","Retail","Store, showroom or customer-facing branch"],
  ["restaurant","Restaurant","Dining, service and collection areas"],
  ["clinic","Clinic","Healthcare or patient-facing site"],
  ["other","Other","A site that does not fit these categories"],
];
const PURPOSES=[
  ["general","General monitoring"],
  ["entrance","Entrance / exit"],
  ["reception","Reception / lobby"],
  ["management","Management / office"],
  ["restricted","Restricted / sensitive area"],
  ["parking","Parking / vehicle access"],
  ["perimeter","Perimeter"],
  ["loading","Loading / service entrance"],
  ["queue","Queue / service area"],
];
const DAYS=[[1,"Mon"],[2,"Tue"],[3,"Wed"],[4,"Thu"],[5,"Fri"],[6,"Sat"],[7,"Sun"]];

function stateLabel(s){return String(s||"unknown").replaceAll("_"," ").replace(/\b\w/g,(c)=>c.toUpperCase());}
function recorderName(ctx){return [ctx?.recorder?.vendor,ctx?.recorder?.model].filter(Boolean).join(" ")||"Not identified yet";}
function firstOpenStep(ctx){return (ctx?.onboarding?.steps||[]).find((s)=>!s.done);}

export default function Setup(){
  const[email,setEmail]=useState("");
  const[role,setRole]=useState("viewer");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[context,setContext]=useState(null);
  const[siteType,setSiteType]=useState("");
  const[openTime,setOpenTime]=useState("08:00");
  const[closeTime,setCloseTime]=useState("19:00");
  const[workingDays,setWorkingDays]=useState([1,2,3,4,5]);
  const[cameras,setCameras]=useState([]);
  const[newSite,setNewSite]=useState("");
  const[recommendation,setRecommendation]=useState(null);
  const[conversationId,setConversationId]=useState("");
  const[busy,setBusy]=useState(false);
  const[error,setError]=useState("");
  const[note,setNote]=useState("");

  const canManage=role==="owner"||role==="admin";

  const load=useCallback(async()=>{
    const sb=supabase();const{data:{session}}=await sb.auth.getSession();if(!session){location.replace("/login/");return;}setEmail(session.user.email||"");
    const[s,r]=await Promise.all([sb.rpc("wl_sites"),sb.rpc("wl_my_role")]);
    if(s.error||r.error){setError(say(s.error||r.error));return;}
    setSites(s.data||[]);setRole(r.data||"viewer");
    if(!siteId&&(s.data||[]).length)setSiteId(s.data[0].id);
  },[siteId]);

  const loadContext=useCallback(async(id)=>{
    if(!id){setContext(null);setCameras([]);return;}
    const{data,error}=await supabase().rpc("wl_ai_context",{p_site_id:id});
    if(error){if(/wl_ai_context|schema cache|function/i.test(error.message||"")){setError("The AI setup migration is not deployed yet. Deploy migration 0101 before using guided setup.");return;}setError(say(error));return;}
    setContext(data||null);setCameras((data?.cameras||[]).map((c)=>({...c,purpose:c.purpose||"general"})));
    const bc=data?.business_context||{};if(bc.site_type)setSiteType(bc.site_type);if(bc.open_time)setOpenTime(String(bc.open_time).slice(0,5));if(bc.close_time)setCloseTime(String(bc.close_time).slice(0,5));if(Array.isArray(bc.working_days)&&bc.working_days.length)setWorkingDays(bc.working_days.map(Number));
  },[]);

  useEffect(()=>{load();},[load]);
  useEffect(()=>{if(siteId)loadContext(siteId);},[siteId,loadContext]);
  useEffect(()=>{if(!siteId)return;const t=setInterval(()=>loadContext(siteId),10000);return()=>clearInterval(t);},[siteId,loadContext]);

  const site=sites.find((s)=>s.id===siteId);
  const steps=context?.onboarding?.steps||[];
  const next=firstOpenStep(context);
  const progress=steps.length?Math.round((steps.filter((s)=>s.done).length/steps.length)*100):0;
  const monitored=cameras.filter((c)=>c.monitor).length;
  const configured=cameras.filter((c)=>c.name&&c.name.trim()&&c.purpose&&c.purpose!=="general").length;

  async function addSite(e){e.preventDefault();if(!canManage||!newSite.trim())return;setBusy(true);setError("");const{error}=await supabase().rpc("wl_add_site",{p_name:newSite.trim()});setBusy(false);if(error){setError(say(error));return;}setNewSite("");setNote("Site created. Select it below to continue setup.");await load();}
  async function issueCode(){if(!siteId||!canManage)return;setBusy(true);const{data,error}=await supabase().rpc("wl_issue_code",{p_site_id:siteId,p_days:14});setBusy(false);if(error){setError(say(error));return;}setNote(`Setup code ${data.code} created. It can be used once and expires in 14 days.`);await load();}
  async function copy(v){try{await navigator.clipboard.writeText(v);setNote("Copied.");}catch{setError("Could not copy automatically. Select the value and copy it manually.");}}

  function patchCamera(id,patch){setCameras((all)=>all.map((c)=>c.id===id?{...c,...patch}:c));}
  async function saveCamera(c){if(!canManage)return;const{error}=await supabase().rpc("wl_ai_setup_camera",{p_site_id:siteId,p_camera_id:c.id,p_name:c.name||null,p_purpose:c.purpose||null,p_monitor:Boolean(c.monitor)});if(error){setError(say(error));return false;}return true;}
  async function saveCameras(){if(!canManage)return;setBusy(true);setError("");for(const c of cameras){const ok=await saveCamera(c);if(!ok){setBusy(false);return;}}const{error}=await supabase().rpc("wl_onboarding_advance",{p_site_id:siteId,p_step:"cameras_mapped",p_done:true});setBusy(false);if(error){setError(say(error));return;}setNote("Camera roles saved. WatchLog will now use only monitored cameras for health and intelligence.");await loadContext(siteId);}

  async function saveBusinessContext(){if(!canManage||!siteType)return;const entrances=cameras.filter((c)=>c.monitor&&["entrance","perimeter","loading"].includes(c.purpose)).map((c)=>c.id);const reception=cameras.filter((c)=>c.monitor&&c.purpose==="reception").map((c)=>c.id);const management=cameras.filter((c)=>c.monitor&&c.purpose==="management").map((c)=>c.id);const critical=cameras.filter((c)=>c.monitor&&c.purpose==="restricted").map((c)=>c.id);setBusy(true);setError("");const{error}=await supabase().rpc("wl_upsert_site_context",{p_site_id:siteId,p_site_type:siteType,p_open_time:openTime||null,p_close_time:closeTime||null,p_overnight:false,p_working_days:workingDays,p_entrance_camera_ids:entrances,p_reception_camera_ids:reception,p_management_camera_ids:management,p_critical_camera_ids:critical,p_restricted_purposes:null,p_reporting_prefs:null,p_notification_prefs:null});setBusy(false);if(error){setError(say(error));return;}setNote("Site context saved. WatchLog can now make recommendations using your actual hours and camera roles.");await loadContext(siteId);}

  async function askRecommendation(){if(!siteId)return;setBusy(true);setError("");const prompt="Review this site's current WatchLog setup. Explain what this exact recorder supports, what WatchLog software should handle, which setup details are still missing, and recommend a practical monitoring configuration. Do not execute recorder changes.";const{data,error}=await supabase().functions.invoke("watchlog-ai",{body:{prompt,site_id:siteId,conversation_id:conversationId||null}});setBusy(false);if(error||data?.error){setError(data?.message||say(error)||"WatchLog AI could not prepare the recommendation.");return;}setConversationId(data.conversation_id||conversationId);setRecommendation(data);if(canManage)await supabase().rpc("wl_onboarding_advance",{p_site_id:siteId,p_step:"recommendation_reviewed",p_done:true});await loadContext(siteId);}
  async function approveSetup(){if(!canManage)return;setBusy(true);const{error}=await supabase().rpc("wl_onboarding_advance",{p_site_id:siteId,p_step:"approved",p_done:true});setBusy(false);if(error){setError(say(error));return;}setNote("WatchLog setup approved. Recorder-side changes, if any are recommended later, still require their own safe approval before they are applied.");await loadContext(siteId);}

  return <div className={styles.page}>
    <header className={styles.header}><a href="/ai/" className={styles.brand}><Mark size={29}/><b>WatchLog</b></a><div className={styles.headerCenter}><span>Guided setup</span>{site&&<b>{site.name}</b>}</div><div className={styles.headerActions}><a href="/ai/">Ask WatchLog</a><a href="/settings/">Settings</a><span>{email}</span></div></header>
    <main className={styles.main}>
      <section className={styles.hero}><div className={styles.heroCopy}><div className={styles.eyebrow}>WatchLog Setup</div><h1>{site?`Let's finish setting up ${site.name}.`:"Let's set up your first site."}</h1><p>WatchLog guides the process using your actual recorder model, camera health and device capabilities. It asks for the business context the camera system cannot know, then recommends the safest monitoring setup.</p></div>{site&&<div className={styles.progressCard}><div className={styles.progressTop}><span>Setup progress</span><b>{progress}%</b></div><div className={styles.progressTrack}><span style={{width:`${progress}%`}}/></div><p>{next?`Next: ${next.label}`:"Recorded setup checklist complete"}</p></div>}</section>
      {error&&<div className={styles.error}>{error}</div>}{note&&<div className={styles.note}>{note}</div>}

      <section className={styles.siteChooser}><div><label>Site</label><select value={siteId} onChange={(e)=>{setSiteId(e.target.value);setRecommendation(null);}}><option value="">Select a site</option>{sites.map((s)=><option key={s.id} value={s.id}>{s.name}</option>)}</select></div>{canManage&&<form onSubmit={addSite}><input value={newSite} onChange={(e)=>setNewSite(e.target.value)} placeholder="Add a new site"/><button disabled={busy||!newSite.trim()}>Add site</button></form>}</section>

      {!siteId?<section className={styles.empty}><Mark size={44}/><h2>Add or select a site</h2><p>Once a site exists, WatchLog can generate its secure setup code and guide the connection.</p></section>:<div className={styles.layout}>
        <div className={styles.flow}>
          <section className={styles.stepCard}><div className={styles.stepHead}><span className={styles.stepNo}>1</span><div><h2>Tell WatchLog about this site</h2><p>This context powers after-hours intelligence, journeys, reporting and better recommendations.</p></div>{context?.business_context?.captured&&<span className={styles.complete}>✓ Saved</span>}</div>
            <div className={styles.siteTypes}>{SITE_TYPES.map(([v,label,desc])=><button key={v} className={siteType===v?styles.selected:""} onClick={()=>setSiteType(v)} disabled={!canManage}><b>{label}</b><span>{desc}</span></button>)}</div>
            <div className={styles.hours}><div><label>Opens</label><input type="time" value={openTime} onChange={(e)=>setOpenTime(e.target.value)} disabled={!canManage}/></div><div><label>Closes</label><input type="time" value={closeTime} onChange={(e)=>setCloseTime(e.target.value)} disabled={!canManage}/></div><div className={styles.dayField}><label>Working days</label><div className={styles.days}>{DAYS.map(([n,l])=><button type="button" key={n} className={workingDays.includes(n)?styles.dayOn:""} onClick={()=>setWorkingDays((d)=>d.includes(n)?d.filter((x)=>x!==n):[...d,n].sort())} disabled={!canManage}>{l}</button>)}</div></div></div>
            {canManage&&<button className={styles.primary} onClick={saveBusinessContext} disabled={busy||!siteType}>Save site context</button>}
          </section>

          <section className={styles.stepCard}><div className={styles.stepHead}><span className={styles.stepNo}>2</span><div><h2>Connect WatchLog to the camera system</h2><p>Install WatchLog on the Windows computer at this location. Recorder credentials stay on that computer.</p></div>{context?.connectivity?.agent_online&&<span className={styles.complete}>✓ Connected</span>}</div>
            <div className={styles.connectGrid}><div className={styles.downloadCard}><span>Windows site computer</span><h3>WatchLog for Windows</h3><p>Run setup on a computer connected to the same network as the NVR/DVR.</p>{INSTALLER_URL?<a href={INSTALLER_URL}>Download WatchLog</a>:<div className={styles.unavailable}>Installer URL is not configured for this environment.</div>}</div><div className={styles.codeCard}><span>Single-use setup code</span>{site?.open_code?<><strong>{site.open_code}</strong><button onClick={()=>copy(site.open_code)}>Copy code</button></>:<><strong>Not issued</strong>{canManage&&<button onClick={issueCode} disabled={busy}>Create setup code</button>}</>}<small>Valid for 14 days. A used code cannot enroll another Agent.</small></div></div>
            <div className={styles.connectionState}><div><i className={context?.connectivity?.agent_online?styles.ok:""}/><span>WatchLog Agent</span><b>{context?.connectivity?.agent_online?"Online":"Waiting"}</b></div><div><i className={context?.recorder?.identified?styles.ok:""}/><span>Recorder</span><b>{recorderName(context)}</b></div><div><i className={cameras.length?styles.ok:""}/><span>Cameras</span><b>{cameras.length?`${cameras.length} channels discovered`:"Waiting"}</b></div></div>
          </section>

          <section className={styles.stepCard}><div className={styles.stepHead}><span className={styles.stepNo}>3</span><div><h2>Choose the cameras WatchLog should understand</h2><p>Rename active cameras and give them a business purpose. Empty recorder slots can stay ignored and will not create false health alerts.</p></div>{steps.find((s)=>s.key==="map_cameras")?.done&&<span className={styles.complete}>✓ Mapped</span>}</div>
            {!cameras.length?<div className={styles.waiting}>Camera channels will appear here after the Site Agent discovers the recorder.</div>:<div className={styles.cameraGrid}>{cameras.map((c)=><article className={`${styles.cameraCard} ${!c.monitor?styles.ignored:""}`} key={c.id}><div className={styles.cameraTop}><span>Channel {c.channel}</span><label className={styles.switch}><input type="checkbox" checked={Boolean(c.monitor)} onChange={(e)=>patchCamera(c.id,{monitor:e.target.checked})} disabled={!canManage}/><i/></label></div><div className={styles.cameraPlaceholder}><span>{c.health_state==="offline"?"Camera unavailable":"Camera preview"}</span><small>{stateLabel(c.health_state)}</small></div><label>Name<input value={c.name||""} onChange={(e)=>patchCamera(c.id,{name:e.target.value})} disabled={!canManage||!c.monitor}/></label><label>Purpose<select value={c.purpose||"general"} onChange={(e)=>patchCamera(c.id,{purpose:e.target.value})} disabled={!canManage||!c.monitor}>{PURPOSES.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label><div className={styles.cameraFoot}><span className={c.recording_state==="recording"?styles.goodText:""}>{c.monitor?`Recording: ${stateLabel(c.recording_state)}`:"Ignored / unused"}</span></div></article>)}</div>}
            {canManage&&cameras.length>0&&<div className={styles.actionRow}><span>{monitored} monitored · {cameras.length-monitored} ignored · {configured} purpose-mapped</span><button className={styles.primary} onClick={saveCameras} disabled={busy}>Save camera setup</button></div>}
          </section>

          <section className={styles.stepCard}><div className={styles.stepHead}><span className={styles.stepNo}>4</span><div><h2>Let WatchLog recommend the monitoring setup</h2><p>The recommendation combines your site context with the exact recorder capability profile. Unsupported or unknown recorder features are never guessed.</p></div>{steps.find((s)=>s.key==="recommendation")?.done&&<span className={styles.complete}>✓ Reviewed</span>}</div>
            <div className={styles.deviceTruth}><div><span>Detected recorder</span><b>{recorderName(context)}</b></div><div><span>Capability evidence</span><b>{context?.capability_known?"Exact model profile loaded":"Not confirmed yet"}</b></div><div><span>Recorder changes</span><b>Human approval required</b></div></div>
            {!recommendation?<button className={styles.aiButton} onClick={askRecommendation} disabled={busy||!context?.recorder?.identified}><Mark size={22}/><span><b>{busy?"Preparing recommendation…":"Ask WatchLog to configure this site"}</b><small>Uses recorder capability truth + site context</small></span><em>→</em></button>:<div className={styles.recommendation}><div className={styles.aiBubble}><Mark size={24}/><p>{recommendation.answer}</p></div>{(recommendation.cards||[]).map((card,i)=><div className={styles.recCard} key={i}><b>{card.title||stateLabel(card.type)}</b><pre>{JSON.stringify(card.data||{},null,2)}</pre></div>)}{(recommendation.suggestions||[]).length>0&&<div className={styles.suggestions}>{recommendation.suggestions.map((s)=><a key={s} href={`/ai/?site=${siteId}`}>{s}</a>)}</div>}</div>}
          </section>

          <section className={`${styles.stepCard} ${styles.finish}`}><div className={styles.stepHead}><span className={styles.stepNo}>5</span><div><h2>Review and start monitoring</h2><p>Approving this setup confirms the WatchLog configuration. Any future recorder-side change still has its own separate safe approval.</p></div>{steps.find((s)=>s.key==="approval")?.done&&<span className={styles.complete}>✓ Approved</span>}</div>
            <div className={styles.readiness}>{steps.map((s)=><div key={s.key}><span className={s.done?styles.readyYes:styles.readyNo}>{s.done?"✓":"○"}</span><b>{s.label}</b></div>)}</div>
            <div className={styles.finalActions}>{canManage&&<button className={styles.primary} onClick={approveSetup} disabled={busy||!steps.find((s)=>s.key==="recommendation")?.done}>Approve WatchLog setup</button>}<a href="/ai/">Open WatchLog AI</a><a href="/site-health/">View Site Health</a></div>
          </section>
        </div>

        <aside className={styles.guide}><div className={styles.guideHead}><Mark size={27}/><div><b>WatchLog AI</b><span>Setup guide</span></div></div><p>{next?`I'll help you with the next step: ${next.label.toLowerCase()}.`:"The recorded setup checklist is complete. I can keep helping you improve monitoring from the AI workspace."}</p><div className={styles.guideFacts}><div><span>Agent</span><b>{context?.connectivity?.agent_online?"Online":"Not connected"}</b></div><div><span>Recorder</span><b>{recorderName(context)}</b></div><div><span>Cameras</span><b>{monitored} monitored</b></div><div><span>Faults</span><b>{(context?.faults||[]).length}</b></div></div><a className={styles.askLink} href="/ai/">Ask WatchLog anything →</a><div className={styles.safety}><b>Safe by design</b><p>WatchLog AI can read tenant-safe site context, but recorder credentials stay at the site. Recorder writes require explicit authorized approval.</p></div></aside>
      </div>}
    </main>
  </div>;
}
