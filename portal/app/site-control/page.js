"use client";

// Site Control — capability-aware recorder/site diagnosis (items 4 & 5).
//
// Everything shown here is driven by the recorder Capability KB via wl_my_site_diagnosis:
// a control is only offered when the capability supports it, an UNKNOWN capability is shown
// as "Not verified" (never as supported), an UNSUPPORTED one is disabled with the reason, and
// the Read -> Recommend -> Approve tiers are gated by the caller's role. No raw CGI/recorder
// action is ever exposed, and no recorder credential ever reaches the browser.

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";

function human(v){if(!v)return"";return String(v).replaceAll("_"," ").replace(/^./,(c)=>c.toUpperCase());}
function ago(ts){if(!ts)return"never";const s=Math.max(0,Math.round((Date.now()-Date.parse(ts))/1000));if(s<60)return`${s}s ago`;if(s<3600)return`${Math.round(s/60)}m ago`;if(s<86400)return`${Math.round(s/3600)}h ago`;return`${Math.round(s/86400)}d ago`;}

// Capability verdict x evidence -> how the UI treats it. The two hard rules are structural:
// an UNKNOWN is never "supported", and OFFICIAL documentation is never "verified".
function capView(cap){
  const v=cap.verdict,e=cap.evidence_class;
  if(v==="supported"&&e==="FIELD_VERIFIED")return{cls:"s-ok",label:"Configurable",note:"Verified on your device",action:"configure"};
  if(v==="supported")return{cls:"s-warn",label:"Documented",note:"Per datasheet — verify on your device first",action:"verify"};
  if(v==="by_camera")return{cls:"s-unk",label:"Camera-side",note:"Configured on the camera, not the recorder",action:"none"};
  if(v==="unsupported")return{cls:"s-bad",label:"Not available",note:cap.constraints||"Not supported by this recorder",action:"disabled"};
  return{cls:"s-unk",label:"Not verified",note:"Run a check to confirm on your device",action:"check"};
}
function reachPill(v,ok,bad){if(v===true)return <span className="pill s-ok">{ok}</span>;if(v===false)return <span className="pill s-bad">{bad}</span>;return <span className="pill s-unk">not verified</span>;}

export default function SiteControl(){
  const[email,setEmail]=useState(""),[sites,setSites]=useState([]),[siteId,setSiteId]=useState(""),
        [diag,setDiag]=useState(null),[ctx,setCtx]=useState(null),[onb,setOnb]=useState(null),
        [error,setError]=useState(""),[msg,setMsg]=useState("");

  const loadSite=useCallback(async(sid)=>{const sb=supabase();const[d,c,o]=await Promise.all([
      sb.rpc("wl_my_site_diagnosis",{p_site_id:sid}),
      sb.rpc("wl_my_site_context",{p_site_id:sid}),
      sb.rpc("wl_onboarding_status",{p_site_id:sid})]);
    if(d.error){setError(say(d.error));return;}setError("");
    setDiag(d.data||null);setCtx(c.data||null);setOnb(o.data||null);},[]);

  const load=useCallback(async()=>{const guard=await requireTenant();if(!guard)return;setEmail(guard.session.user.email||"");
    const sb=supabase();const s=await sb.rpc("wl_sites");if(s.error){setError(say(s.error));return;}
    const list=s.data||[];setSites(list);const first=list[0]?.id;if(first){setSiteId(first);await loadSite(first);}},[loadSite]);
  useEffect(()=>{load();},[load]);

  async function pick(e){const sid=e.target.value;setSiteId(sid);setDiag(null);await loadSite(sid);}
  async function saveType(e){const t=e.target.value;const sb=supabase();const r=await sb.rpc("wl_upsert_site_context",{p_site_id:siteId,p_site_type:t});
    if(r.error)setError(say(r.error));else{setMsg("Business context saved.");await loadSite(siteId);setTimeout(()=>setMsg(""),2500);}}

  const rec=diag?.recorder||{},conn=diag?.connectivity||{},caps=diag?.capabilities||[],cams=diag?.cameras||[],tiers=diag?.tiers||{},faults=diag?.faults||[];
  const cov=diag?.coverage||{};

  return <div className="shell"><Nav active="Site Control" email={email}/><main className="main">
    <header className="target-page-head"><div><div className="target-eyebrow">Site Control</div>
      <h1>Diagnose a site — safely.</h1>
      <p>WatchLog reads what your recorder can and can&rsquo;t do and only ever offers a safe, reversible change with your approval. It never signs into your recorder from here and never shows a raw device command.</p></div>
      {sites.length>1&&<select value={siteId} onChange={pick} className="select">{sites.map((s)=><option key={s.id} value={s.id}>{s.name}</option>)}</select>}
    </header>
    {error&&<div className="err">{error}</div>}
    {msg&&<div className="ok">{msg}</div>}
    {!diag?<div className="panel"><div className="empty">Loading site diagnosis…</div></div>:<>

      <div className="panel"><h2>Recorder</h2><table><tbody>
        <tr><td>Identity</td><td>{rec.identified?`${rec.vendor} ${rec.model}`:<span className="pill s-unk">not identified yet</span>}</td></tr>
        <tr><td>Connection</td><td>{reachPill(conn.agent_online,"online","offline")} <span className="muted">{conn.last_seen?`· last seen ${ago(conn.last_seen)}`:""}</span></td></tr>
        <tr><td>Clock / NTP</td><td><span className="pill s-unk">not verified</span> <span className="muted">· confirmed by a Site Control read, never assumed</span></td></tr>
        <tr><td>Monitoring coverage</td><td>{cov.coverage_ratio!=null?`${Math.round(cov.coverage_ratio*100)}% of today verified`:<span className="pill s-unk">not verified</span>}</td></tr>
      </tbody></table></div>

      <div className="panel"><h2>Cameras &amp; current faults</h2>{cams.length?<table><thead><tr><th>Channel</th><th>Camera</th><th>Purpose</th><th>Status</th></tr></thead>
        <tbody>{cams.map((c)=><tr key={c.channel}><td className="mono">{c.channel}</td><td>{c.name}</td><td className="muted">{c.purpose||""}</td>
          <td>{c.video_loss?<span className="pill s-bad">video loss</span>:c.health_state==="operational"?<span className="pill s-ok">ok</span>:c.health_state==="unknown"?<span className="pill s-unk">not verified</span>:<span className="pill s-warn">{human(c.health_state)}</span>}</td></tr>)}</tbody></table>
        :<div className="empty">No cameras discovered yet.</div>}
        {faults.length>0&&<p className="muted">{faults.length} camera(s) currently in fault.</p>}</div>

      <div className="panel"><h2>What this recorder can do</h2>
        <p className="muted">From the recorder capability knowledge base. WatchLog only offers a change where the capability is verified on your exact device. Where a native capability is missing, WatchLog software analytics may be available instead.</p>
        {diag.capability_known?<table><thead><tr><th>Capability</th><th>Status</th><th>Evidence</th><th>Action</th></tr></thead>
          <tbody>{caps.map((cap)=>{const view=capView(cap);const canApprove=tiers.approve;const canRecommend=tiers.recommend;
            return <tr key={cap.capability}><td>{human(cap.capability)}</td>
              <td><span className={"pill "+view.cls}>{view.label}</span><div className="muted" style={{fontSize:11}}>{view.note}</div></td>
              <td className="muted">{human(cap.evidence_class)}</td>
              <td>{view.action==="configure"?
                    (canApprove?<button className="small">Recommend &amp; approve</button>:canRecommend?<button className="small ghost">Recommend</button>:<span className="muted">read only</span>)
                  :view.action==="verify"?<button className="small ghost">Check on device</button>
                  :view.action==="check"?<button className="small ghost">Check on device</button>
                  :view.action==="disabled"?<button className="small ghost" disabled title={view.note}>Not available</button>
                  :<span className="muted">on camera</span>}</td></tr>;})}</tbody></table>
          :<div className="empty">This recorder is not in the capability knowledge base yet. Run a Site Control read to discover it, or add the model.</div>}
        <p className="muted" style={{marginTop:8}}>Every change is <b>Read → Recommend → Approve</b>: WatchLog reads the current setting, proposes a safe reversible change, and applies it only after {tiers.approve?"you approve":"an owner/admin approves"}. Raw recorder commands are never exposed here.</p>
      </div>

      <div className="panel"><h2>Business context</h2>
        <p className="muted">Tell WatchLog how this site operates so the daily intelligence is accurate. Technical recorder settings stay in the advanced view above.</p>
        <label>Site type <select value={ctx?.site_type||""} onChange={saveType} className="select">
          <option value="">Choose…</option>{["office","retail","factory","restaurant","warehouse","clinic","other"].map((t)=><option key={t} value={t}>{human(t)}</option>)}</select></label>
        {onb&&<div style={{marginTop:12}}><h3 style={{fontSize:13}}>Setup checklist</h3>
          <div className="health-camera-list">{(onb.steps||[]).map((st)=><div className="health-camera-row" key={st.key}><i/><span>{st.label}</span><span>{st.done?<span className="pill s-ok">done</span>:<span className="pill s-unk">pending</span>}</span></div>)}</div></div>}
      </div>
    </>}
  </main></div>;
}
