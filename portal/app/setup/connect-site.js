"use client";
import {useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {Section} from "../owner/ui";
import SetupChecks from "./setup-checks";
import s from "./customer.module.css";
const INSTALLER=process.env.NEXT_PUBLIC_INSTALLER_URL||"";

export default function ConnectSite({site,siteId,ctx,cameras,canManage,onRefresh,onMessage,onError}){
  const[busy,setBusy]=useState(false);
  async function code(){setBusy(true);const{data,error}=await supabase().rpc("wl_issue_code",{p_site_id:siteId,p_days:14});setBusy(false);if(error){onError(say(error));return}onMessage(`Setup code ${data.code} created.`);onRefresh()}
  async function copy(v){try{await navigator.clipboard.writeText(v);onMessage("Setup code copied.")}catch{onError("Could not copy automatically. Select the code and copy it manually.")}}

  const online=ctx?.connectivity?.agent_online;
  const recorder=ctx?.recorder?.identified;
  return <Section first className={s.card} title="Connect WatchLog to this site's cameras" note="Install WatchLog on a Windows computer at this location. Your camera-system sign-in stays at the site.">
    <ol className={s.tasks}>
      <li>
        <span className={s.taskNo} aria-hidden="true">1</span>
        <div className={s.taskMain}>
          <h3>Install WatchLog for Windows</h3>
          <p>Use a computer connected to the same local network as the camera system.</p>
        </div>
        <div className={s.taskAct}>
          {INSTALLER?<a className="ow-btn" href={INSTALLER}>Download WatchLog</a>:<span className={s.reason}>Download will appear here when the installer is published.</span>}
        </div>
      </li>
      <li>
        <span className={s.taskNo} aria-hidden="true">2</span>
        <div className={s.taskMain}>
          <h3>Enter the setup code</h3>
          {site?.open_code?<p><span className={s.code}>{site.open_code}</span></p>:<p>No active code{canManage?"":". Ask an account owner or admin to create one."}</p>}
        </div>
        <div className={`${s.taskAct} ${s.actionsInline}`}>
          {site?.open_code
            ?<button type="button" className="ow-btn quiet" onClick={()=>copy(site.open_code)}>Copy code</button>
            :canManage&&<button type="button" className="ow-btn quiet" onClick={code} disabled={busy} aria-busy={busy}>{busy?"Creating…":"Create setup code"}</button>}
        </div>
      </li>
    </ol>
    <div className="ow-label" style={{marginTop:20}}>Connection status</div>
    <SetupChecks items={[
      {label:"WatchLog connection",value:online?"Connected":"Waiting",tone:online?"ok":"unknown"},
      {label:"Camera system",value:recorder?([ctx.recorder.vendor,ctx.recorder.model].filter(Boolean).join(" ")||"Connected"):"Waiting",tone:recorder?"ok":"unknown"},
      {label:"Cameras found",value:cameras.length?String(cameras.length):"Waiting",tone:cameras.length?"ok":"unknown"}
    ]}/>
  </Section>;
}
