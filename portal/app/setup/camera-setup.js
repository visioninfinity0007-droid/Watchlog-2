"use client";
import {useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {Section,Status,Empty} from "../owner/ui";
import s from "./customer.module.css";
import {purposeOptions,resolveSiteType} from "../owner/site-profiles";
// Camera purposes depend on the site type (an office has meeting rooms, a warehouse has loading docks).
// A purpose saved earlier that is not in the current list stays selectable so it is never silently lost.
const LEGACY={general:"General area",management:"Office / management",reception:"Reception / lobby",loading:"Loading / service",queue:"Queue / service area"};
function purposesFor(siteType,current){const list=purposeOptions(siteType);const extra=[...new Set(current.filter(v=>v&&!list.some(([k])=>k===v)))].map(v=>[v,LEGACY[v]||v.replace(/_/g," ")]);return[...list,...extra]}

export default function CameraSetup({siteId,siteType,cameras,patch,canManage,onSaved,onError}){
  const[busy,setBusy]=useState(false);
  const PURPOSES=purposesFor(resolveSiteType(siteType),cameras.map(c=>String(c.purpose||"")));
  const incomplete=cameras.some(c=>Boolean(c.monitor)&&!String(c.purpose||"").trim());
  async function save(){if(incomplete){onError("Choose what each monitored camera watches before saving.");return}setBusy(true);for(const c of cameras){const{error}=await supabase().rpc("wl_ai_setup_camera",{p_site_id:siteId,p_camera_id:c.id,p_name:c.name||null,p_purpose:c.purpose||null,p_monitor:Boolean(c.monitor)});if(error){setBusy(false);onError(say(error));return}}const{error}=await supabase().rpc("wl_onboarding_advance",{p_site_id:siteId,p_step:"cameras_mapped",p_done:true});setBusy(false);if(error){onError(say(error));return}onSaved("Camera choices saved.")}

  const monitored=cameras.filter(c=>c.monitor).length;
  return <Section first className={s.card} title="Which cameras should WatchLog monitor?" count={cameras.length?monitored+" of "+cameras.length+" monitored":null} note="Name the cameras you use and choose what each one watches. Turn off unused channels.">
    {cameras.length?<table className={`ow-table ${s.cameras}`}>
      <thead><tr><th>Camera</th><th>Monitor</th><th>Name</th><th>Area</th></tr></thead>
      <tbody>{cameras.map(c=>{
        const unset=Boolean(c.monitor)&&!String(c.purpose||"").trim();
        return <tr key={c.id} className={c.monitor?"":s.off}>
          <td><b>Camera {c.channel}</b></td>
          <td><label className="ow-check"><input type="checkbox" checked={Boolean(c.monitor)} onChange={e=>patch(c.id,{monitor:e.target.checked})} disabled={!canManage}/> Monitor</label></td>
          <td><input aria-label={`Name for camera ${c.channel}`} value={c.name||""} onChange={e=>patch(c.id,{name:e.target.value})} placeholder={`Camera ${c.channel}`} disabled={!canManage||!c.monitor}/></td>
          <td>
            <select aria-label={`Area for camera ${c.channel}`} value={c.purpose||""} onChange={e=>patch(c.id,{purpose:e.target.value})} disabled={!canManage||!c.monitor}><option value="">Choose area</option>{PURPOSES.map(([v,l])=><option value={v} key={v}>{l}</option>)}</select>
            {unset&&<div className={s.cellNote}><Status tone="warn">Purpose not set</Status></div>}
          </td>
        </tr>;
      })}</tbody>
    </table>:<Empty title="No cameras yet">Keep WatchLog running at the site while it connects to the camera system.</Empty>}
    {canManage&&cameras.length>0&&<div className={s.actions}>
      <button type="button" className="ow-btn" onClick={save} disabled={busy||incomplete} aria-busy={busy}>{busy?"Saving…":"Save cameras"}</button>
      {incomplete&&<span className={s.reason}>Choose an area for every monitored camera before saving.</span>}
    </div>}
    {!canManage&&incomplete&&<p className={s.hint}>Choose an area for every monitored camera before saving.</p>}
  </Section>;
}
