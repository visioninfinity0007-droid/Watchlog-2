"use client";
import {useState} from "react";
import {setupPill} from "../shell";
import {withSite} from "../site-context";
import {Section,Row,Status,Empty,Notice} from "../owner/ui";
import styles from "./settings.module.css";

function fmt(ts,timeZone){if(!ts)return"No activity yet";try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",year:"numeric",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(ts))+" · site time"}catch{return String(ts)}}
function setupTone(cls){return cls==="s-ok"?"ok":cls==="s-warn"?"warn":cls==="s-bad"?"bad":"unknown"}

export default function SiteList({settings}){
  const[target,setTarget]=useState(null);
  const[confirmName,setConfirmName]=useState("");
  const[result,setResult]=useState("");

  async function remove(){
    if(!target||confirmName!==target.name)return;
    const out=await settings.removeSite(target);
    if(!out)return;
    setResult(`${target.name} was removed from WatchLog. Its site connection can no longer connect to this account.`);
    setTarget(null);setConfirmName("");
  }

  const current=settings.current;

  return <>
    <div className="ow-rows">
      {settings.sites.map(s=>{
        const[label,cls]=setupPill(s.setup_state,s.online);
        const t=setupTone(cls);
        return <article role="button" tabIndex={0} aria-pressed={s.id===settings.siteId} className={"ow-row "+styles.site} key={s.id} onClick={()=>settings.choose(s.id)} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();settings.choose(s.id)}}}>
          <i className={"ow-row-tick "+t} aria-hidden="true"/>
          <div className="ow-row-main">
            <h3>{s.name}</h3>
            <div className="ow-row-meta"><span>{s.cameras==null?"Cameras not counted yet":s.cameras+" camera"+(s.cameras===1?"":"s")}</span><span>{s.timezone||"Timezone not set"}</span><span>Last activity {fmt(s.last_event,s.timezone)}</span></div>
          </div>
          <div className="ow-row-act"><Status tone={t}>{label}</Status></div>
        </article>
      })}
    </div>
    {!settings.sites.length&&<Empty title="No sites yet.">Add your first site above.</Empty>}

    {result&&<div style={{marginTop:12}}><Notice tone="ok"><span><b>Site removed.</b> {result}</span></Notice></div>}

    {current&&<>
      <Section title={current.name+" settings"} note="Settings below apply to this site only.">
        <div className="ow-rows">
          <Row compact tone="neutral" title="Setup & Support" body={current.online?"Review or change the current WatchLog setup.":"Connect WatchLog and choose what to monitor."} action={<a href={withSite("/setup/",current.id)}>{current.online?"Review setup":"Continue setup"}</a>}/>
          <Row compact tone="neutral" title="Camera settings" body="Camera areas and supported camera-system settings." action={<a href={withSite("/site-control/",current.id)}>Open</a>}/>
          <Row compact tone="neutral" title="System Health" body="Connection, camera health and recording verification." action={<a href={withSite("/site-health/",current.id)}>Open</a>}/>
          <Row compact tone="neutral" title="Report delivery" body="Who receives this site's reports, and how." action={<a href={withSite("/reports/delivery/",current.id)}>Manage</a>}/>
        </div>
      </Section>

      <Section title="Ask WatchLog privacy" note="Controlled per site and off by default. These permissions only affect how Ask WatchLog may process information when answering questions." action={!settings.canManage?<span className="ow-pill">View only</span>:null}>
        <div className="ow-rows" aria-busy={!settings.privacy.loaded||Boolean(settings.privacyBusy)}>
          <Row tone={settings.privacy.loaded?"neutral":"unknown"} title="Written site information"
            body="Allows approved online AI processing to use text such as site status, reports and your questions. Camera images are not included by this permission."
            meta={[settings.privacy.loaded?(settings.privacy.text?"Allowed":"Off"):"Checking…"]}
            action={settings.canManage?<button type="button" aria-pressed={settings.privacy.text} className="ow-btn quiet small" disabled={!settings.privacy.loaded||Boolean(settings.privacyBusy)||settings.privacy.evidence} onClick={()=>settings.setPrivacyPermission("text",!settings.privacy.text)}>{settings.privacyBusy==="text"?"Saving…":settings.privacy.text?"Turn off":"Allow text only"}</button>:null}/>
          <Row tone={settings.privacy.loaded?"neutral":"unknown"} title="Camera evidence"
            body="Allows approved online AI processing to use camera evidence when a question needs it. Enabling this also allows written site information."
            meta={[settings.privacy.loaded?(settings.privacy.evidence?"Allowed":"Off"):"Checking…"]}
            action={settings.canManage?<button type="button" aria-pressed={settings.privacy.evidence} className="ow-btn quiet small" disabled={!settings.privacy.loaded||Boolean(settings.privacyBusy)} onClick={()=>settings.setPrivacyPermission("evidence",!settings.privacy.evidence)}>{settings.privacyBusy==="evidence"?"Saving…":settings.privacy.evidence?"Turn off":"Allow evidence"}</button>:null}/>
        </div>
        <p className="ow-muted" style={{fontSize:12.5,marginTop:10}}>{settings.privacy.evidence?"Camera-evidence permission includes written site information. Turn off camera evidence before turning off text-only processing.":settings.canManage?"You can change these permissions at any time.":"Only an account owner or admin can change these permissions."}</p>
        {settings.privacyNote&&<div style={{marginTop:10}}><Notice>{settings.privacyNote}</Notice></div>}
      </Section>

      {settings.canManage&&<Section title="Remove this site">
        <div className="ow-rows">
          <Row tone="neutral" title={"Remove "+current.name} body="Disconnect WatchLog from this location and remove its cameras, monitoring history, reports, incidents and site chats from this account."
            action={target?null:<button type="button" className="ow-btn danger small" onClick={()=>{setTarget(current);setConfirmName("");setResult("")}}>Remove site</button>}/>
        </div>
        {target&&<div className={styles.confirm} role="group" aria-label="Remove site">
          <Notice tone="bad"><span><b>Remove {target.name}?</b> This permanently removes the site from WatchLog and disconnects the existing WatchLog site connection. It will no longer be able to connect to this account.</span></Notice>
          <label className="ow-field">Type {target.name} to confirm
            <input autoFocus value={confirmName} onChange={e=>setConfirmName(e.target.value)}/>
          </label>
          <div className={styles.confirmActions}>
            <button type="button" className="ow-btn danger" disabled={confirmName!==target.name||settings.removing} aria-busy={settings.removing} onClick={remove}>{settings.removing?"Removing…":"Remove site"}</button>
            <button type="button" className="ow-btn quiet" onClick={()=>{setTarget(null);setConfirmName("")}}>Cancel</button>
          </div>
        </div>}
      </Section>}
    </>}
  </>;
}
