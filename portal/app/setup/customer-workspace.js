"use client";
import {useState} from "react";
import {withSite} from "../site-context";
import {OwnerPage,SiteSelect,Lead,RailSection,Stat,Summary,Empty,Loading,Notice} from "../owner/ui";
import useSetupBase from "./use-setup-base";
import SiteDetails from "./site-details";
import ConnectSite from "./connect-site";
import CameraSetup from "./camera-setup";
import ReviewSetup from "./review-setup";
import s from "./customer.module.css";

const COPY={1:["Tell me about this site","A few details help WatchLog understand normal hours and unusual activity."],2:["Connect the camera system","Install WatchLog at the site and I’ll find the camera system and cameras."],3:["Choose the cameras that matter","Name the cameras you use and tell WatchLog what each one watches."],4:["Review the monitoring plan","I’ll recommend a practical setup based on this site and its cameras."],5:["Finish setup","Review the current plan before WatchLog begins monitoring this site."],6:["WatchLog is connected","Monitoring is active. Use this page to review or change the site setup when needed."]};
const STEPS=[[1,"Site details"],[2,"Connect cameras"],[3,"Choose cameras"],[4,"Recommendation"],[5,"Approve"]];

function StepLedger({stage}){
  return <ol className={s.steps} aria-label="Setup progress">
    {STEPS.map(([n,label])=>{
      const state=stage>n?"done":stage===n?"current":"pending";
      return <li key={n} className={s[state]} aria-current={state==="current"?"step":undefined}>
        <span className={s.stepNo} aria-hidden="true">{state==="done"?"✓":n}</span>
        <span className={s.stepText}><b>{label}</b><small>{state==="done"?"Done":state==="current"?"Current step":"Pending"}</small></span>
      </li>;
    })}
  </ol>;
}

export default function CustomerSetup(){
  const b=useSetupBase();
  const[note,setNote]=useState("");
  async function done(msg){setNote(msg);await b.refresh()}

  const ctx=b.ctx;
  const online=ctx?.connectivity?.agent_online;
  const recorder=ctx?.recorder?.identified;
  const monitored=b.cameras.filter(c=>c.monitor).length;
  const known=Boolean(ctx);
  const connection=!known?null:online?"Connected":"Waiting";
  const cameraSystem=!known?null:recorder?"Connected":"Waiting";
  const found=!known?null:b.cameras.length?String(b.cameras.length):"None yet";
  const stepNo=b.stage>=1&&b.stage<=5?b.stage:null;

  let leadTone="unknown";
  let leadBody=COPY[b.stage]?.[1];
  if(b.stage===6){
    leadTone=online?"ok":"warn";
    if(!online)leadBody="Monitoring was set up, but the site connection is not responding right now. Check System Health.";
  }

  const rail=b.siteId&&known?<>
    <RailSection label="Site connection">
      <Stat label="WatchLog connection" value={connection}/>
      <Stat label="Camera system" value={cameraSystem}/>
      <Stat label="Cameras found" value={found}/>
      <Stat label="Cameras monitored" value={b.cameras.length?String(monitored):null} muted={!b.cameras.length}/>
    </RailSection>
    <RailSection label="Help">
      <a className="ow-rail-link" href={withSite("/site-health/",b.siteId)}>System Health<i>→</i></a>
      <div className="ow-ask">
        <a href={withSite("/ai/?prompt="+encodeURIComponent("What is still missing from this site's setup, and what should I do next?"),b.siteId)}>What should I do next?</a>
      </div>
    </RailSection>
  </>:null;

  const summary=rail?<Summary items={[
    {value:stepNo?"Step "+stepNo+" of 5":"Complete",label:"Setup"},
    {value:connection||"Not available",label:"WatchLog connection",muted:!online},
    {value:b.cameras.length?String(monitored):"None yet",label:"Cameras monitored",muted:!b.cameras.length}
  ]}/>:null;

  const signedIn=Boolean(b.email);

  return <OwnerPage active="Setup" email={b.email} siteId={b.siteId}
    kicker={["Setup & Support",b.site?.name]}
    title={b.site?`Set up ${b.site.name} with WatchLog`:"Set up your first site"}
    actions={<SiteSelect sites={b.sites} value={b.siteId} onChange={b.choose}/>}
    rail={rail}
    summary={summary}>
    {b.error&&<div style={{marginBottom:14}}><Notice tone="bad">{b.error}</Notice></div>}
    {note&&<div style={{marginBottom:14}}><Notice tone="ok">{note}</Notice></div>}
    {!signedIn&&!b.error?<Loading label="Opening setup"/>
      :!b.siteId?<Empty title="Add a site first" action={<a className="ow-btn" href="/settings/">Open Settings</a>}>Create the site in Settings, then come back here and WatchLog will guide the connection.</Empty>
      :!known&&!b.error?<Loading label="Checking this site's setup"/>
      :<>
        <StepLedger stage={b.stage}/>
        <Lead tone={leadTone} title={COPY[b.stage]?.[0]||"Setup"} body={leadBody}/>
        {b.stage===1&&<SiteDetails siteId={b.siteId} ctx={b.ctx} canManage={b.canManage} onSaved={done} onError={b.setError}/>}
        {b.stage===2&&<ConnectSite site={b.site} siteId={b.siteId} ctx={b.ctx} cameras={b.cameras} canManage={b.canManage} onRefresh={b.refresh} onMessage={setNote} onError={b.setError}/>}
        {b.stage===3&&<CameraSetup siteId={b.siteId} siteType={b.ctx?.business_context?.site_type||b.site?.site_type} cameras={b.cameras} patch={b.patch} canManage={b.canManage} onSaved={done} onError={b.setError}/>}
        {b.stage>=4&&<ReviewSetup siteId={b.siteId} ctx={b.ctx} cameras={b.cameras} stage={b.stage} canManage={b.canManage} onRefresh={b.refresh} onMessage={setNote} onError={b.setError}/>}
        {!b.canManage&&b.stage<6&&<div style={{marginTop:16}}><Notice>Only an account owner or admin can change this site's setup.</Notice></div>}
      </>}
  </OwnerPage>;
}
