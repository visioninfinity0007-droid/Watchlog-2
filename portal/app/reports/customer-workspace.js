"use client";
import RichText from "../rich-text";
import {Nav} from "../shell";
import {withSite} from "../site-context";
import Mark from "../mark";
import ui from "../portal.module.css";
import styles from "./reports.module.css";
import useReport from "./use-report";

const VIEWS=[["yesterday","Yesterday"],["daily","Today"],["monthly","30 days"],["executive","Executive"]];
function dateLabel(v){if(!v)return"Yesterday";const d=new Date(`${v}T12:00:00`);return d.toLocaleDateString("en-PK", {weekday:"long",day:"numeric",month:"long",year:"numeric"})}
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
      <div className={styles.heroTop}><div className={styles.heroTitle}><div className={styles.heroMark}><Mark size={26}/></div><div><div className={styles.kicker}>Yesterday at a glance</div><h2>{p.title||"Report of Yesterday"}</h2></div></div><div className={styles.date}>{dateLabel(p.report_date||snapshot?.report_date)}</div></div>
      <p className={styles.summary}>{summary}</p>
      <div className={styles.meta}><span>{p.site_name||"Main site"}</span><span>Owner’s daily brief</span></div>
    </section>

    {metrics.length>0&&<section className={styles.metrics}>{metrics.map((m,i)=><div className={styles.metric} key={`${m.label}-${i}`}><div className={styles.metricValue}>{m.value}</div><div className={styles.metricLabel}>{m.label}</div>{m.note&&<div className={styles.metricNote}>{m.note}</div>}</div>)}</section>}

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Security & incidents</h3><p>Anything that needed the owner’s attention yesterday.</p></div></div><InsightCards items={incidents}/></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>What happened yesterday</h3><p>The most useful things to know about staff presence and office use.</p></div></div><InsightCards items={insights}/></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Monitoring</h3><p>What WatchLog could see clearly, and what happened outside that window.</p></div></div><div className={styles.findings}><article className={styles.finding}><div className={styles.findingTop}><span className={styles.dot}/><b>{coverage.status||"Coverage overview"}</b></div><div className={styles.metricValue} style={{fontSize:20,marginTop:9}}>{coverage.period||"—"}</div><p>{coverage.summary||"Coverage information is not available."}</p></article>{coverage.note&&<article className={styles.finding}><div className={styles.findingTop}><span className={`${styles.dot} ${styles.dotAttention}`}/><b>What we could not see</b></div><p>{coverage.note}</p></article>}</div></section>

    {cameras.length>0&&<section className={styles.section}><div className={styles.sectionHead}><div><h3>Key areas</h3><p>A simple view of the parts of the office that mattered yesterday.</p></div></div><div className={styles.cameraGrid}>{cameras.map((c,i)=><article className={styles.camera} key={`${c.camera}-${i}`}><div className={styles.cameraTop}><div className={styles.cameraName}>{c.camera}</div><span className={styles.channel}>{c.status||"Covered"}</span></div><div className={styles.cameraStats}><div className={styles.cameraStat}><span>When</span><b>{c.period||"—"}</b></div><div className={styles.cameraStat}><span>What it means</span><b>{c.management_view||"Routine"}</b></div></div><p className={styles.assessment}>{c.assessment}</p></article>)}</div></section>}

    {actions.length>0&&<section className={styles.section}><div className={styles.sectionHead}><div><h3>What needs attention</h3><p>Only the practical things worth following up.</p></div></div><div className={styles.actions}>{actions.map((a,i)=><div className={styles.action} key={i}>{a}</div>)}</div></section>}
  </div>
}

export default function CustomerReports(){
  const r=useReport(),label=VIEWS.find(([k])=>k===r.view)?.[1]||"Report";
  const prompt=r.view==="yesterday"?"Explain yesterday's Al-Khalid Office report like you are briefing the business owner. Focus on what happened, anything unusual, staff and office activity, Armory access, and what needs attention. Keep it short and non-technical.":"Explain this report like you are briefing the business owner. Focus on what happened and what needs attention.";
  return <div className="shell"><Nav active="Reports" email={r.email} currentSiteId={r.siteId}/><main className="main"><header className="target-page-head"><div><div className="target-eyebrow">Reports</div><h1>Management report</h1><p>A short owner-friendly view of what happened yesterday and what needs attention.</p></div><div className="target-actions"><a className={ui.secondaryLink} href={withSite("/reports/delivery/",r.siteId)}>Delivery</a></div></header>{r.error&&<div className="err">{r.error}</div>}<div className={ui.tabs}>{VIEWS.map(([k,l])=><button key={k} className={`${ui.tab} ${r.view===k?ui.tabActive:""}`} onClick={()=>r.setView(k)}>{l}</button>)}</div>{r.busy?<div className={ui.emptyCard}>Preparing your management report…</div>:r.view==="yesterday"?(r.snapshot?<ManagementReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>Yesterday's report is not available yet.</div>):<section className="daily-report-card"><div className="daily-report-head"><Mark size={26}/><b>{label} report</b></div><div className="daily-report-body">{r.answer?<RichText text={r.answer}/>:<p style={{margin:0}}>No report is available yet.</p>}</div></section>}<div className={ui.sectionHead}><div><h2>Want to go deeper?</h2><p>Ask WatchLog a question about this report.</p></div><a className={ui.primaryLink} href={withSite(`/ai/?prompt=${encodeURIComponent(prompt)}`,r.siteId)}>Ask WatchLog</a></div></main></div>;
}
