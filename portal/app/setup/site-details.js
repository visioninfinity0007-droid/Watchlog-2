"use client";
import {useEffect,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {Section} from "../owner/ui";
import s from "./customer.module.css";
const TYPES=[["office","Office"],["warehouse","Warehouse"],["factory","Factory"],["retail","Retail"],["restaurant","Restaurant"],["clinic","Clinic"],["other","Other"]];
const DAYS=[[1,"Mon"],[2,"Tue"],[3,"Wed"],[4,"Thu"],[5,"Fri"],[6,"Sat"],[7,"Sun"]];

export default function SiteDetails({siteId,ctx,canManage,onSaved,onError}){
  const[siteType,setSiteType]=useState("");const[open,setOpen]=useState("");const[close,setClose]=useState("");const[days,setDays]=useState([]);const[busy,setBusy]=useState(false);
  useEffect(()=>{const b=ctx?.business_context||{};if(b.site_type)setSiteType(b.site_type);if(b.open_time)setOpen(String(b.open_time).slice(0,5));if(b.close_time)setClose(String(b.close_time).slice(0,5));if(Array.isArray(b.working_days))setDays(b.working_days.map(Number))},[ctx]);
  async function save(){if(!canManage||!siteType||!open||!close||open===close||!days.length)return;const overnight=close<open;setBusy(true);const{error}=await supabase().rpc("wl_upsert_site_context",{p_site_id:siteId,p_site_type:siteType,p_open_time:open,p_close_time:close,p_overnight:overnight,p_working_days:days,p_entrance_camera_ids:[],p_reception_camera_ids:[],p_management_camera_ids:[],p_critical_camera_ids:[],p_restricted_purposes:null,p_reporting_prefs:null,p_notification_prefs:null});setBusy(false);if(error){onError(say(error));return}onSaved("Site details saved.")}

  const missing=!siteType?"Choose the type of place to continue.":!open||!close?"Add opening and closing times to continue.":open===close?"Opening and closing times must differ.":!days.length?"Choose at least one working day.":"";
  return <Section first className={s.card} title="What kind of place is this?" note="This helps WatchLog understand normal hours and what should happen outside them.">
    <div className="ow-choices" role="group" aria-label="Type of place">
      {TYPES.map(([v,l])=><button type="button" aria-pressed={siteType===v} key={v} onClick={()=>setSiteType(v)} disabled={!canManage}>{l}</button>)}
    </div>

    <div className={s.group}>
      <div className="ow-label">Business hours</div>
      <div className={s.hours}>
        <label className="ow-field">Opens<input type="time" value={open} onChange={e=>setOpen(e.target.value)} disabled={!canManage}/></label>
        <label className="ow-field">Closes<input type="time" value={close} onChange={e=>setClose(e.target.value)} disabled={!canManage}/></label>
      </div>
      <p className={s.hint}>{open&&close?(open===close?"Choose different opening and closing times.":close<open?"Closing time is on the next day.":"These are the site’s local business hours."):"Choose the business hours for this site. WatchLog will not assume them."}</p>
    </div>

    <div className={s.group}>
      <div className="ow-label" id="setup-working-days">Working days</div>
      <div className="ow-choices" role="group" aria-labelledby="setup-working-days">
        {DAYS.map(([n,l])=><button type="button" aria-pressed={days.includes(n)} key={n} onClick={()=>setDays(x=>x.includes(n)?x.filter(d=>d!==n):[...x,n].sort())} disabled={!canManage}>{l}</button>)}
      </div>
    </div>

    {canManage&&<div className={s.actions}>
      <button type="button" className="ow-btn" onClick={save} disabled={busy||!siteType||!open||!close||open===close||!days.length} aria-busy={busy}>{busy?"Saving…":"Continue"}</button>
      {missing&&<span className={s.reason}>{missing}</span>}
    </div>}
  </Section>;
}
