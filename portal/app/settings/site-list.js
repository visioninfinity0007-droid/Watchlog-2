"use client";
import {useState} from "react";
import {setupPill} from "../shell";
import {withSite} from "../site-context";
import ui from "../portal.module.css";

function fmt(ts,timeZone){if(!ts)return"No activity yet";try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",year:"numeric",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(ts))+" · site time"}catch{return String(ts)}}

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

  return <>
    <section className="settings-site-grid">
      {settings.sites.map(s=>{
        const[label,cls]=setupPill(s.setup_state,s.online);
        return <article role="button" tabIndex={0} aria-pressed={s.id===settings.siteId} className={`settings-site-card ${s.id===settings.siteId?"active":""}`} key={s.id} onClick={()=>settings.choose(s.id)} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();settings.choose(s.id)}}}>
          <div className="settings-site-head">
            <div><b>{s.name}</b><small>{fmt(s.last_event,s.timezone)}</small></div>
            <span className={`pill ${cls}`}>{label}</span>
          </div>
          <div className="settings-site-facts">
            <div><span>Cameras</span><b>{s.cameras??0}</b></div>
            <div><span>Timezone</span><b>{s.timezone||"Not set"}</b></div>
          </div>
        </article>
      })}
      {!settings.sites.length&&<div className={ui.emptyCard}>No sites yet. Add your first site above.</div>}
    </section>

    {result&&<div className={ui.callout}><span className={ui.statusDot}/><div><strong>Site removed</strong><p>{result}</p></div></div>}

    {settings.current&&<>
      <div className={ui.sectionHead}><div><h2>{settings.current.name}</h2><p>What would you like to do?</p></div></div>
      <section className={ui.threeCol}>
        <a className={ui.roleCard} href={withSite("/setup/",settings.current.id)}><strong>Setup & Support</strong><p>{settings.current.online?"Review or change the current WatchLog setup.":"Connect WatchLog and choose what to monitor."}</p><span>{settings.current.online?"Review setup →":"Continue setup →"}</span></a>
        <a className={ui.roleCard} href={withSite("/site-health/",settings.current.id)}><strong>System Health</strong><p>Check connection, camera health and recording verification.</p><span>Open system health →</span></a>
        <a className={ui.roleCard} href={withSite("/ai/",settings.current.id)}><strong>Ask WatchLog</strong><p>Ask what happened or explore information about this site.</p><span>Open Ask WatchLog →</span></a>
      </section>

      <section className={ui.privacyCard} aria-busy={!settings.privacy.loaded||Boolean(settings.privacyBusy)}>
        <div className={ui.privacyHead}>
          <div><span>Ask WatchLog privacy</span><h3>Choose what can be used for online AI processing</h3><p>These permissions are controlled per site and are off by default. They only affect how Ask WatchLog may process information when answering questions.</p></div>
          {!settings.canManage&&<span className="pill s-unk">View only</span>}
        </div>
        <div className={ui.privacyOptions}>
          <div className={ui.privacyOption}>
            <div><strong>Written site information</strong><p>Allows approved online AI processing to use text such as site status, reports and your questions. Camera images are not included by this permission.</p></div>
            <div className={ui.privacyControl}>
              <span className={"pill "+(settings.privacy.text?"s-ok":"s-unk")}>{settings.privacy.loaded?(settings.privacy.text?"Allowed":"Off"):"Checking…"}</span>
              {settings.canManage&&<button type="button" aria-pressed={settings.privacy.text} className="ghost small" disabled={!settings.privacy.loaded||Boolean(settings.privacyBusy)||settings.privacy.evidence} onClick={()=>settings.setPrivacyPermission("text",!settings.privacy.text)}>{settings.privacyBusy==="text"?"Saving…":settings.privacy.text?"Turn off":"Allow text only"}</button>}
            </div>
          </div>
          <div className={ui.privacyOption}>
            <div><strong>Camera evidence</strong><p>Allows approved online AI processing to use camera evidence when a question needs it. Enabling this also allows written site information.</p></div>
            <div className={ui.privacyControl}>
              <span className={"pill "+(settings.privacy.evidence?"s-ok":"s-unk")}>{settings.privacy.loaded?(settings.privacy.evidence?"Allowed":"Off"):"Checking…"}</span>
              {settings.canManage&&<button type="button" aria-pressed={settings.privacy.evidence} className="ghost small" disabled={!settings.privacy.loaded||Boolean(settings.privacyBusy)} onClick={()=>settings.setPrivacyPermission("evidence",!settings.privacy.evidence)}>{settings.privacyBusy==="evidence"?"Saving…":settings.privacy.evidence?"Turn off":"Allow evidence"}</button>}
            </div>
          </div>
        </div>
        <p className={ui.privacyFoot}>{settings.privacy.evidence?"Camera-evidence permission includes written site information. Turn off camera evidence before turning off text-only processing.":settings.canManage?"You can change these permissions at any time.":"Only an account owner or admin can change these permissions."}</p>
        {settings.privacyNote&&<div className={ui.privacyNote} role="status">{settings.privacyNote}</div>}
      </section>

      <section className={ui.dangerZone}>
        <div>
          <strong>Remove this site</strong>
          <p>Disconnect WatchLog from this location and remove its cameras, monitoring history, reports, incidents and site chats from this account.</p>
        </div>
        <button type="button" className={ui.dangerButton} onClick={()=>{setTarget(settings.current);setConfirmName("");setResult("")}}>Remove site</button>
      </section>
    </>}

    {target&&<div className={ui.modalBackdrop} role="dialog" aria-modal="true" aria-label="Remove site">
      <div className={ui.confirmModal}>
        <h2>Remove {target.name}?</h2>
        <p>This permanently removes the site from WatchLog and disconnects the existing WatchLog site connection. It will no longer be able to connect to this account.</p>
        <label>Type <b>{target.name}</b> to confirm
          <input autoFocus value={confirmName} onChange={e=>setConfirmName(e.target.value)} />
        </label>
        <div className={ui.confirmActions}>
          <button type="button" className={ui.closeBtn} onClick={()=>{setTarget(null);setConfirmName("")}}>Cancel</button>
          <button type="button" className={ui.dangerButton} disabled={confirmName!==target.name||settings.removing} onClick={remove}>{settings.removing?"Removing…":"Remove site"}</button>
        </div>
      </div>
    </div>}
  </>;
}
