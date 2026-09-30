"use client";
import {useState} from "react";
import {setupPill} from "../shell";
import {withSite} from "../site-context";
import ui from "../portal.module.css";

function fmt(ts){return ts?new Date(ts).toLocaleString():"No activity yet"}

export default function SiteList({settings}){
  const[target,setTarget]=useState(null);
  const[confirmName,setConfirmName]=useState("");
  const[result,setResult]=useState("");

  async function remove(){
    if(!target||confirmName!==target.name)return;
    const out=await settings.removeSite(target);
    if(!out)return;
    setResult(`${target.name} was removed. ${out.agents_disconnected||0} site Agent${Number(out.agents_disconnected||0)===1?"":"s"} disconnected.`);
    setTarget(null);setConfirmName("");
  }

  return <>
    <section className="settings-site-grid">
      {settings.sites.map(s=>{
        const[label,cls]=setupPill(s.setup_state,s.online);
        return <article className={`settings-site-card ${s.id===settings.siteId?"active":""}`} key={s.id} onClick={()=>settings.choose(s.id)}>
          <div className="settings-site-head">
            <div><b>{s.name}</b><small>{fmt(s.last_event)}</small></div>
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
        <a className={ui.roleCard} href={withSite("/setup/",settings.current.id)}><strong>Guided Setup</strong><p>Connect WatchLog and choose what to monitor.</p><span>Continue setup →</span></a>
        <a className={ui.roleCard} href={withSite("/site-health/",settings.current.id)}><strong>Site Health</strong><p>Check camera and recording status.</p><span>Open health →</span></a>
        <a className={ui.roleCard} href={withSite("/ai/",settings.current.id)}><strong>WatchLog AI</strong><p>Ask what happened or adjust monitoring.</p><span>Ask WatchLog →</span></a>
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
        <p>This permanently removes the site from WatchLog and disconnects its enrolled site Agent. The Agent will no longer be able to authenticate with its existing identity.</p>
        <label>Type <b>{target.name}</b> to confirm
          <input autoFocus value={confirmName} onChange={e=>setConfirmName(e.target.value)} />
        </label>
        <div className={ui.confirmActions}>
          <button type="button" className={ui.closeBtn} onClick={()=>{setTarget(null);setConfirmName("")}}>Cancel</button>
          <button type="button" className={ui.dangerButton} disabled={confirmName!==target.name||settings.removing} onClick={remove}>{settings.removing?"Removing…":"Remove site and disconnect Agent"}</button>
        </div>
      </div>
    </div>}
  </>;
}
