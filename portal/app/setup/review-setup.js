"use client";
import RichText from "../rich-text";
import {useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {Section,Notice} from "../owner/ui";
import SetupChecks from "./setup-checks";
import s from "./customer.module.css";

export default function ReviewSetup({siteId,ctx,cameras,stage,canManage,onRefresh,onMessage,onError}){
  const[busy,setBusy]=useState(false);const[answer,setAnswer]=useState("");
  async function recommend(){setBusy(true);const prompt="Review this site's WatchLog setup in plain customer language. Recommend what WatchLog should monitor, point out anything still missing, and clearly separate what the camera system already provides from what WatchLog adds. Do not make any camera-system changes.";const{data,error}=await supabase().functions.invoke("watchlog-ai",{body:{prompt,site_id:siteId,conversation_id:null}});setBusy(false);if(error||data?.error){onError(data?.message||say(error)||"WatchLog could not prepare the recommendation.");return}setAnswer(data.answer||"");if(canManage)await supabase().rpc("wl_onboarding_advance",{p_site_id:siteId,p_step:"recommendation_reviewed",p_done:true});onRefresh()}
  async function approve(){setBusy(true);const{error}=await supabase().rpc("wl_onboarding_advance",{p_site_id:siteId,p_step:"approved",p_done:true});setBusy(false);if(error){onError(say(error));return}onMessage(monitoringLive?"Current setup approved. Monitoring remains active.":"Setup approved. WatchLog is ready to monitor this site.");onRefresh()}

  const approvalDone=(ctx?.onboarding?.steps||[]).find(x=>x.key==="approval")?.done===true;
  const monitoringLive=(ctx?.onboarding?.steps||[]).find(x=>x.key==="monitoring")?.done===true;
  const online=ctx?.connectivity?.agent_online;
  const recorder=ctx?.recorder?.identified;
  const monitored=cameras.filter(c=>c.monitor).length;
  const enc=encodeURIComponent(siteId);

  if(stage===6)return <Section first className={s.card} title="Current setup" note="Monitoring is active for this site. Review the current setup here when you need to change cameras, business context or monitoring preferences.">
    <SetupChecks items={[
      {label:"WatchLog connection",value:online?"Connected":"Needs attention",tone:online?"ok":"warn"},
      {label:"Camera system",value:recorder?"Connected":"Not verified",tone:recorder?"ok":"unknown"},
      {label:"Cameras monitored",value:String(monitored)}
    ]}/>
    {!approvalDone&&<div style={{marginTop:16}}><Notice><div><b>Setup review still open</b><div>Monitoring is already active, but the current setup has not been recorded as approved. Approving it does not start monitoring again; it records the current plan as reviewed.</div></div></Notice></div>}
    <div className={s.actions}>
      {canManage&&!approvalDone&&<button type="button" className="ow-btn" onClick={approve} disabled={busy} aria-busy={busy}>{busy?"Saving…":"Approve current setup"}</button>}
      <a className="ow-btn quiet" href={`/home/?site=${enc}`}>Open Home</a>
      <a className="ow-btn quiet" href={`/site-health/?site=${enc}`}>System Health</a>
    </div>
  </Section>;

  if(stage===4)return <Section first className={s.card} title="Review the recommended monitoring" note="WatchLog will combine your site details, camera choices and what your camera system can actually provide.">
    {answer&&<div className={`ow-panel ${s.answer}`}><div className="ow-label">WatchLog recommendation</div><RichText text={answer}/></div>}
    <div className={s.actions}>
      <button type="button" className={answer?"ow-btn quiet":"ow-btn"} onClick={recommend} disabled={busy} aria-busy={busy}>{busy?"Reviewing…":answer?"Refresh recommendation":"Prepare recommendation"}</button>
    </div>
  </Section>;

  return <Section first className={s.card} title="Finish the monitoring setup" note="Review the essentials, then approve the current WatchLog plan for this site.">
    <SetupChecks items={[
      {label:"WatchLog connection",value:online?"Connected":"Needs attention",tone:online?"ok":"warn"},
      {label:"Camera system",value:recorder?"Connected":"Needs attention",tone:recorder?"ok":"warn"},
      {label:"Cameras monitored",value:String(monitored)}
    ]}/>
    <div className={s.actions}>
      {canManage&&<button type="button" className="ow-btn" onClick={approve} disabled={busy} aria-busy={busy}>{busy?"Saving…":"Approve setup"}</button>}
      <a className="ow-btn quiet" href={`/ai/?site=${enc}`}>Ask WatchLog</a>
    </div>
  </Section>;
}
