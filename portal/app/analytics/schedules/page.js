"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { requireTenant } from "../../shell";
import { OwnerPage, Section, Row, Empty, Loading, Notice } from "../../owner/ui";

const DAYS=[["mon","Monday"],["tue","Tuesday"],["wed","Wednesday"],["thu","Thursday"],["fri","Friday"],["sat","Saturday"],["sun","Sunday"]];
function initialWindows(){return Object.fromEntries(DAYS.map(([k])=>[k,{enabled:!["sat","sun"].includes(k),start:"08:00",end:"18:00"}]));}
function fromSchedule(schedule){const out=initialWindows(),days=schedule?.days||{};for(const[key]of DAYS){const first=days[key]?.[0];out[key]=first?{enabled:true,start:first[0],end:first[1]}:{...out[key],enabled:false};}return out;}
function toSchedule(windows){const days={};for(const[key]of DAYS){const w=windows[key];days[key]=w?.enabled?[[w.start,w.end]]:[];}return{days};}

export default function Schedules(){
  const[email,setEmail]=useState(""),[studio,setStudio]=useState(null),[siteId,setSiteId]=useState(""),[editing,setEditing]=useState(null),[name,setName]=useState("Business hours"),[windows,setWindows]=useState(initialWindows()),[error,setError]=useState(""),[note,setNote]=useState(""),[busy,setBusy]=useState(false);
  const load=useCallback(async()=>{const guard=await requireTenant();if(!guard)return;setEmail(guard.session.user.email||"");const{data,error}=await supabase().rpc("wl_analytics_studio");if(error){setError(say(error));return;}setStudio(data||{sites:[],can_manage:false});const sites=data?.sites||[];setSiteId((old)=>sites.some((s)=>s.id===old)?old:(sites[0]?.id||""));},[]);
  useEffect(()=>{load();},[load]);
  const sites=studio?.sites||[],site=sites.find((s)=>s.id===siteId),schedules=site?.schedules||[],canManage=studio?.can_manage!==false;

  function newSchedule(preset="business"){if(!canManage)return;setEditing(null);if(preset==="after"){setName("Night shift");const w=initialWindows();for(const[key]of DAYS)w[key]={enabled:!["sat","sun"].includes(key),start:"18:00",end:"08:00"};setWindows(w);}else if(preset==="weekend"){setName("Weekend");const w=initialWindows();for(const[key]of DAYS)w[key]={enabled:["sat","sun"].includes(key),start:"00:00",end:"23:59"};setWindows(w);}else{setName("Business hours");setWindows(initialWindows());}}
  function edit(s){if(!canManage)return;setEditing(s.id);setName(s.name);setWindows(fromSchedule(s.schedule));}
  async function save(){if(!site||!canManage)return;if(!name.trim()){setError("Give these hours a name.");return;}setBusy(true);setError("");setNote("");const{error}=await supabase().rpc("wl_upsert_monitoring_schedule",{p_id:editing,p_site_id:site.id,p_name:name.trim(),p_timezone:site.timezone||"Asia/Karachi",p_schedule:toSchedule(windows),p_enabled:true});if(error)setError(say(error));else{setNote("Hours saved. Activity rules that use this schedule will update shortly.");setEditing(null);await load();}setBusy(false);}
  async function remove(s){if(!canManage)return;if(Number(s.rule_count||0)>0){setError(`These hours are used by ${s.rule_count} activity rule${Number(s.rule_count)===1?"":"s"}. Choose different hours for those rules first.`);return;}if(!confirm("Delete these hours?"))return;setBusy(true);setError("");const{error}=await supabase().rpc("wl_delete_monitoring_schedule",{p_schedule_id:s.id});if(error)setError(say(error));else{setNote("Hours deleted.");await load();}setBusy(false);}
  function setDay(key,patch){if(canManage)setWindows((w)=>({...w,[key]:{...w[key],...patch}}));}

  return <OwnerPage active="Analytics" email={email} siteId={siteId}
    kicker={["Insights",site?.name]}
    title="Schedules"
    actions={sites.length>1?<select value={siteId} onChange={(e)=>setSiteId(e.target.value)} aria-label="Site">{sites.map((s)=><option key={s.id} value={s.id}>{s.name}</option>)}</select>:null}>
    <nav className="ow-tabs" aria-label="Insights sections"><a className="ow-tab" href="/analytics/">Overview</a><a className="ow-tab" href="/analytics/studio/">Activity Rules</a><a className="ow-tab active" aria-current="page" href="/analytics/schedules/">Schedules</a></nav>
    {error&&<Notice tone="bad">{error}</Notice>}
    {note&&<Notice tone="ok">{note}</Notice>}
    {!canManage&&<Notice>You have read-only access. An Owner or Admin can change activity rule schedules.</Notice>}
    {!studio?(error?null:<Loading label="Loading schedules"/>):!site?<Empty title="No site available."/>:<div className="ow-grid2">
      <Section first title="Saved schedules" count={schedules.length||null} note={"Times use "+site.name+"’s local time"+(site.timezone?" ("+site.timezone+")":"")+"."}>
        {schedules.length?<div className="ow-rows">{schedules.map((s)=><Row key={s.id} compact tone={editing===s.id?"info":"neutral"} title={s.name}
          meta={[s.timezone,Number(s.rule_count||0)+" activity rule"+(Number(s.rule_count||0)===1?"":"s")]}
          action={canManage?<><button type="button" className="ow-btn quiet small" onClick={()=>edit(s)}>Edit</button><button type="button" className="ow-btn danger small" onClick={()=>remove(s)} disabled={busy||Number(s.rule_count||0)>0} title={Number(s.rule_count||0)>0?"Used by activity rules":undefined}>Delete</button></>:null}/>)}</div>
        :<Empty title="No saved schedules yet."/>}
        {canManage&&<div style={{marginTop:18}}>
          <div className="ow-label" style={{marginBottom:8}}>Quick starts</div>
          <div className="ow-rows">
            <Row compact tone="neutral" title="Business hours" body="Mon to Fri, 08:00 to 18:00" action={<button type="button" className="ow-btn quiet small" onClick={()=>newSchedule("business")}>Use</button>}/>
            <Row compact tone="neutral" title="Night shift" body="Mon to Fri, 18:00 to 08:00" action={<button type="button" className="ow-btn quiet small" onClick={()=>newSchedule("after")}>Use</button>}/>
            <Row compact tone="neutral" title="Weekend" body="Saturday and Sunday" action={<button type="button" className="ow-btn quiet small" onClick={()=>newSchedule("weekend")}>Use</button>}/>
          </div>
        </div>}
      </Section>
      <Section first title={canManage?(editing?"Edit schedule":"Schedule editor"):"Schedule details"} note="If the end time is earlier than the start time, WatchLog treats it as an overnight period.">
        <label className="ow-field">Name<input disabled={!canManage} value={name} onChange={(e)=>setName(e.target.value)}/></label>
        <table className="ow-table" style={{marginTop:12}}>
          <thead><tr><th>Day</th><th>Starts</th><th>Ends</th></tr></thead>
          <tbody>{DAYS.map(([key,label])=>{const w=windows[key];return <tr key={key}>
            <td><label className="ow-check"><input type="checkbox" disabled={!canManage} checked={w.enabled} onChange={(e)=>setDay(key,{enabled:e.target.checked})}/>{label}</label></td>
            <td><input type="time" aria-label={label+" starts"} disabled={!canManage||!w.enabled} value={w.start} onChange={(e)=>setDay(key,{start:e.target.value})}/></td>
            <td><input type="time" aria-label={label+" ends"} disabled={!canManage||!w.enabled} value={w.end} onChange={(e)=>setDay(key,{end:e.target.value})}/></td>
          </tr>})}</tbody>
        </table>
        {canManage&&<div style={{display:"flex",flexWrap:"wrap",gap:8,marginTop:14}}><button type="button" className="ow-btn" disabled={busy} aria-busy={busy} onClick={save}>{busy?"Saving...":"Save schedule"}</button><button type="button" className="ow-btn quiet" onClick={()=>{setEditing(null);newSchedule("business")}}>Reset</button></div>}
      </Section>
    </div>}
  </OwnerPage>;
}
