"use client";
import {Nav} from "../shell";
import {withSite} from "../site-context";
import Mark from "../mark";
import ui from "../portal.module.css";
import styles from "./reports.module.css";
import useReport from "./use-report";

const VIEWS=[["yesterday","Yesterday"],["daily","Today"],["monthly","30 days"],["executive","Executive"]];
function dateLabel(v){if(!v)return"Yesterday";const d=new Date(`${v}T12:00:00`);return d.toLocaleDateString([], {weekday:"long",month:"long",day:"numeric",year:"numeric"})}
function severityClass(v){return v==="critical"?styles.dotCritical:v==="attention"?styles.dotAttention:v==="none"||v==="clear"?styles.dotGood:""}

function InsightCards({items=[]}){
  if(!items.length)return null;
  return <div className={styles.findings}>{items.map((f,i)=><article className={styles.finding} key={`${f.title}-${i}`}><div className={styles.findingTop}><span className={`${styles.dot} ${severityClass(f.severity)}`}/><b>{f.title}</b></div>{f.value&&<div className={styles.metricValue} style={{fontSize:20,marginTop:9}}>{f.value}</div>}<p>{f.body}</p></article>)}</div>;
}

function ManagementReport({snapshot}){
  const p=snapshot?.payload||{},metrics=p.metrics||[],incidents=p.incidents||[],coverage=p.coverage||{},cameras=p.camera_coverage||[],insights=p.site_insights||[],actions=p.priority_actions||[];
  const summary=p.ai_summary||p.executive_summary||"No management summary is available for this report.";
  return <div className={styles.report}>
    <section className={styles.hero}>
      <div className={styles.heroTop}><div className={styles.heroTitle}><div className={styles.heroMark}><Mark size={26}/></div><div><div className={styles.kicker}>AI summary</div><h2>{p.title||"Report of Yesterday"}</h2></div></div><div className={styles.date}>{dateLabel(p.report_date||snapshot?.report_date)}</div></div>
      <p className={styles.summary}>{summary}</p>
      <div className={styles.meta}><span>{p.site_name||"Main site"}</span><span>Daily management brief</span></div>
    </section>

    {metrics.length>0&&<section className={styles.metrics}>{metrics.map((m,i)=><div className={styles.metric} key={`${m.label}-${i}`}><div className={styles.metricValue}>{m.value}</div><div className={styles.metricLabel}>{m.label}</div>{m.note&&<div className={styles.metricNote}>{m.note}</div>}</div>)}</section>}

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Incidents</h3><p>Serious security or safety matters that required attention.</p></div></div><InsightCards items={incidents}/></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Office activity</h3><p>The operational picture management should know from yesterday.</p></div></div><InsightCards items={insights}/></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Camera coverage</h3><p>How well the important areas of the site were covered during the reporting period.</p></div></div><div className={styles.findings}><article className={styles.finding}><div className={styles.findingTop}><span className={styles.dot}/><b>{coverage.status||"Coverage overview"}</b></div><div className={styles.metricValue} style={{fontSize:20,marginTop:9}}>{coverage.period||"—"}</div><p>{coverage.summary||"Coverage information is not available."}</p></article>{coverage.note&&<article className={styles.finding}><div className={styles.findingTop}><span className={`${styles.dot} ${styles.dotAttention}`}/><b>What is not confirmed</b></div><p>{coverage.note}</p></article>}</div></section>

    {cameras.length>0&&<section className={styles.section}><div className={styles.sectionHead}><div><h3>Coverage by area</h3><p>A simple view of the camera areas that matter most to day-to-day management.</p></div></div><div className={styles.cameraGrid}>{cameras.map((c,i)=><article className={styles.camera} key={`${c.camera}-${i}`}><div className={styles.cameraTop}><div className={styles.cameraName}>{c.camera}</div><span className={styles.channel}>{c.status||"Covered"}</span></div><div className={styles.cameraStats}><div className={styles.cameraStat}><span>Observed period</span><b>{c.period||"—"}</b></div><div className={styles.cameraStat}><span>Management view</span><b>{c.management_view||"Routine"}</b></div></div><p className={styles.assessment}>{c.assessment}</p></article>)}</div></section>}

    {actions.length>0&&<section className={styles.section}><div className={styles.sectionHead}><div><h3>Management actions</h3><p>Practical next steps to strengthen security and make future daily reporting more useful.</p></div></div><div className={styles.actions}>{actions.map((a,i)=><div className={styles.action} key={i}>{a}</div>)}</div></section>}
  </div>
}

export default function CustomerReports(){
  const r=useReport(),label=VIEWS.find(([k])=>k===r.view)?.[1]||"Report";
  const prompt=r.view==="yesterday"?"Explain yesterday's management report for Main site in clear business language. Summarize serious incidents, office activity, Armory activity, coverage confidence and the most important management actions. Avoid technical implementation details.":"Explain this management report in clear business language and tell me the priority action.";
  return <div className="shell"><Nav active="Reports" email={r.email} currentSiteId={r.siteId}/><main className="main"><header className="target-page-head"><div><div className="target-eyebrow">Reports</div><h1>Management report</h1><p>A clear daily view of security, office activity and anything management should act on.</p></div><div className="target-actions"><a className={ui.secondaryLink} href={withSite("/reports/delivery/",r.siteId)}>Delivery</a></div></header>{r.error&&<div className="err">{r.error}</div>}<div className={ui.tabs}>{VIEWS.map(([k,l])=><button key={k} className={`${ui.tab} ${r.view===k?ui.tabActive:""}`} onClick={()=>r.setView(k)}>{l}</button>)}</div>{r.busy?<div className={ui.emptyCard}>Preparing your management report…</div>:r.view==="yesterday"?(r.snapshot?<ManagementReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>Yesterday's report is not available yet.</div>):<section className="daily-report-card"><div className="daily-report-head"><Mark size={26}/><b>{label} report</b></div><div className="daily-report-body"><p style={{whiteSpace:"pre-wrap",margin:0}}>{r.answer||"No report is available yet."}</p></div></section>}<div className={ui.sectionHead}><div><h2>Want to go deeper?</h2><p>Ask WatchLog a question about this report.</p></div><a className={ui.primaryLink} href={withSite(`/ai/?prompt=${encodeURIComponent(prompt)}`,r.siteId)}>Ask WatchLog</a></div></main></div>;
}
