"use client";
import {useEffect,useRef,useState} from "react";
import {Nav} from "../shell";
import {withSite} from "../site-context";
import Mark from "../mark";
import useCustomerChat from "./use-customer-chat";
import CustomerMessage from "./customer-message";
import CustomerHeader from "./customer-header";
import CustomerWelcome from "./customer-welcome";
import CustomerComposer from "./customer-composer";
import styles from "./customer.module.css";

function ago(ts){if(!ts)return"Not connected yet";const sec=Math.max(0,Math.floor((Date.now()-Date.parse(ts))/1000));if(sec<60)return"Just now";if(sec<3600)return`${Math.floor(sec/60)}m ago`;if(sec<86400)return`${Math.floor(sec/3600)}h ago`;return`${Math.floor(sec/86400)}d ago`}
function faultLabel(f){const key=String(f?.reason||f?.reason_code||f?.fault_type||"").toLowerCase();const labels={agent_unreachable:"WatchLog connection lost",nvr_unreachable:"Camera system unreachable",nvr_auth_failed:"Camera system sign-in failed",storage_fault:"Camera system storage issue",storage_degraded:"Camera system storage needs attention",disk_full:"Camera system storage is full",video_loss:"Camera offline",camera_offline:"Camera offline",not_recording:"Recording is not confirmed",recording_storage_fault:"Recording storage issue"};return labels[key]||"Monitoring needs attention"}

function HeaderLoader(){return <header className={styles.topbar} aria-hidden="true"><div className={styles.headerSkeleton}><span/><small/></div><div className={styles.topActions}><span className={styles.chipSkeleton}/></div></header>}
function ChatLoader(){return <div className={styles.loadingStage} aria-label="Loading WatchLog workspace"><div className={styles.loadingMark}/><div className={styles.loadingTitle}/><div className={styles.loadingCopy}/><div className={styles.loadingStarterGrid}>{[0,1,2,3].map(i=><div className={styles.loadingStarter} key={i}/>)}</div></div>}
function ComposerLoader(){return <footer className={styles.composerWrap} aria-hidden="true"><small className="watchlogComposerDisclaimer">WatchLog can make mistakes. Check important information.</small><div className={`${styles.composer} ${styles.composerSkeleton}`}><span/><i/></div></footer>}
function ContextLoader(){return <div className={styles.contextLoading} aria-hidden="true"><div className={styles.contextLoadingHead}/>{[0,1,2].map(i=><div className={styles.contextLoadingCard} key={i}><span/><small/><small/></div>)}</div>}

export default function CustomerAI(){
  const c=useCustomerChat();
  const[railCollapsed,setRailCollapsed]=useState(false);
  const endRef=useRef(null);

  useEffect(()=>{
    if(!c.booting&&c.messages.length){endRef.current?.scrollIntoView({behavior:c.busy?"smooth":"auto",block:"end"})}
  },[c.booting,c.messages.length,c.busy]);

  const ctx=c.ctx||{},cams=(ctx.cameras||[]).filter(x=>x.monitor),faults=ctx.faults||[],connected=Boolean(ctx.connectivity?.agent_online),seen=Boolean(ctx.connectivity?.last_seen),healthy=cams.filter(x=>String(x.health_state||"").toLowerCase()==="operational").length,recorder=[ctx.recorder?.vendor,ctx.recorder?.model].filter(Boolean).join(" ")||"Not identified",steps=ctx.onboarding?.steps||[];

  return <div className="shell">
    <Nav active="WatchLog AI" email={c.email} currentSiteId={c.siteId}/>
    <div className={`${styles.workspace} ${railCollapsed?"watchlogContextCollapsed":""}`}>
      <main className={styles.main} aria-busy={c.booting}>
        {c.booting?<HeaderLoader/>:<CustomerHeader site={c.site} ctx={c.ctx} styles={styles}/>} 
        <section className={styles.chat}>
          {c.booting?<ChatLoader/>:c.messages.length?<div className={styles.thread}>{c.messages.map((m,i)=><CustomerMessage key={m.id||i} message={m} siteId={c.siteId} styles={styles}/>)}{c.busy&&<div className={`${styles.message} ${styles.assistant}`}><div className={styles.avatar}><Mark size={18}/></div><div className={styles.thinking}><span>Thinking</span><i/><i/><i/></div></div>}<div ref={endRef}/></div>:<CustomerWelcome site={c.site} ctx={c.ctx} send={c.send} styles={styles}/>} 
        </section>
        {c.error&&<div className={styles.error}>{c.error}</div>}
        {c.booting?<ComposerLoader/>:<CustomerComposer chat={c} styles={styles}/>} 
      </main>

      <aside className={`${styles.contextRail} ${railCollapsed?"watchlogContextRailCollapsed":""}`} aria-label="Site and report context" aria-busy={c.booting}>
        {c.booting?<ContextLoader/>:<>
          <div className={`${styles.contextHead} watchlogContextHead`}>
            {!railCollapsed&&<div><span>Site brief</span><b>{c.site?.name||"Selected site"}</b></div>}
            <button type="button" className="watchlogContextToggle" onClick={()=>setRailCollapsed(v=>!v)} aria-label={railCollapsed?"Expand site and report context":"Minimize site and report context"} title={railCollapsed?"Expand context":"Minimize context"}>{railCollapsed?"←":"→"}</button>
          </div>
          {!railCollapsed&&<div>
            <section className={styles.contextCard}><div className={styles.contextTitle}>Monitoring</div><div className={styles.contextRow}><span>WatchLog</span><b className={connected?styles.okText:seen?styles.warnText:styles.mutedText}>{connected?"Connected":seen?"Offline":"Not connected"}</b></div><div className={styles.contextRow}><span>Last contact</span><b>{ago(ctx.connectivity?.last_seen)}</b></div><div className={styles.contextRow}><span>Cameras</span><b>{cams.length?`${healthy}/${cams.length} confirmed healthy`:"Not configured"}</b></div></section>
            <section className={styles.contextCard}><div className={styles.contextTitle}>Attention</div>{faults.length?<div className={styles.alertList}>{faults.slice(0,4).map((f,i)=><div className={styles.alertRow} key={f.id||i}><span className={styles.alertDot}/><div><b>{f.camera||"Site monitoring"}</b><small>{faultLabel(f)}</small></div></div>)}</div>:<p className={styles.contextMuted}>No current monitoring issue is reported. Anything not verified remains marked as such elsewhere.</p>}</section>
            <section className={styles.contextCard}><div className={styles.contextTitle}>Management reports</div><a className={styles.reportLink} href={withSite("/reports/?view=yesterday",c.siteId)}><span>Yesterday</span><b>Visual management report →</b></a><a className={styles.reportLink} href={withSite("/reports/?view=daily",c.siteId)}><span>Today</span><b>Management report →</b></a><a className={styles.reportLink} href={withSite("/reports/?view=monthly",c.siteId)}><span>30 days</span><b>Management trend →</b></a><a className={styles.reportLink} href={withSite("/reports/?view=executive",c.siteId)}><span>Executive</span><b>Priority summary →</b></a></section>
            {steps.length>0&&<section className={styles.contextCard}><div className={styles.contextTitle}>Setup</div><div className={styles.stepList}>{steps.slice(0,5).map(s=><div className={styles.contextStep} key={s.key}><span className={s.done?styles.stepDone:styles.stepOpen}>{s.done?"✓":"○"}</span><small>{s.label}</small></div>)}</div><a className={styles.contextAction} href={withSite("/setup/",c.siteId)}>Open Guided Setup →</a></section>}
            <section className={styles.contextCard}><div className={styles.contextTitle}>Camera system</div><div className={styles.contextRow}><span>Recorder</span><b>{recorder}</b></div><div className={styles.contextRow}><span>Support status</span><b>{ctx.capability_known?"Checked":"Not yet confirmed"}</b></div></section>
          </div>}
        </>}
      </aside>
    </div>
  </div>;
}
