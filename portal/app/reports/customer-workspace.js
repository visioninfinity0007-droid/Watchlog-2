"use client";
import {Nav} from "../shell";
import {withSite} from "../site-context";
import Mark from "../mark";
import ui from "../portal.module.css";
import styles from "./reports.module.css";
import useReport from "./use-report";

const VIEWS=[["daily","Today"],["yesterday","Yesterday"],["monthly","30 days"],["executive","Executive"]];
function dateLabel(v){if(!v)return"Yesterday";const d=new Date(`${v}T12:00:00`);return d.toLocaleDateString([], {weekday:"long",month:"long",day:"numeric",year:"numeric"})}
function severityClass(v){return v==="critical"?styles.dotCritical:v==="attention"?styles.dotAttention:""}

function InsightCards({items=[]}){
  if(!items.length)return null;
  return <div className={styles.findings}>{items.map((f,i)=><article className={styles.finding} key={`${f.title}-${i}`}><div className={styles.findingTop}><span className={`${styles.dot} ${severityClass(f.severity)}`}/><b>{f.title}</b></div>{f.value&&<div className={styles.metricValue} style={{fontSize:20,marginTop:9}}>{f.value}</div>}<p>{f.body}</p></article>)}</div>;
}

function VisualReport({snapshot}){
  const p=snapshot?.payload||{},metrics=p.metrics||[],incidents=p.incidents||[],coverage=p.coverage||{},cameras=p.camera_coverage||[],insights=p.site_insights||[],actions=p.priority_actions||[];
  const summary=p.ai_summary||p.executive_summary||"No AI summary is available for this report.";
  return <div className={styles.report}>
    <section className={styles.hero}>
      <div className={styles.heroTop}><div className={styles.heroTitle}><div className={styles.heroMark}><Mark size={26}/></div><div><div className={styles.kicker}>AI visual review</div><h2>{p.title||"Report of Yesterday"}</h2></div></div><div className={styles.date}>{dateLabel(p.report_date||snapshot?.report_date)}</div></div>
      <p className={styles.summary}>{summary}</p>
      <div className={styles.meta}><span>Visual-first evidence</span><span>Site: {p.site_name||"Main site"}</span><span>Generated {snapshot?.generated_at?new Date(snapshot.generated_at).toLocaleString():"recently"}</span></div>
    </section>

    {metrics.length>0&&<section className={styles.metrics}>{metrics.map((m,i)=><div className={styles.metric} key={`${m.label}-${i}`}><div className={styles.metricValue}>{m.value}</div><div className={styles.metricLabel}>{m.label}</div>{m.note&&<div className={styles.metricNote}>{m.note}</div>}</div>)}</section>}

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Serious incidents</h3><p>Only events requiring security or management attention belong here. Routine movement is not an incident.</p></div></div><InsightCards items={incidents}/></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Coverage</h3><p>What the available camera imagery can and cannot establish for the day.</p></div></div><div className={styles.findings}><article className={styles.finding}><div className={styles.findingTop}><span className={styles.dot}/><b>Overall visual coverage</b></div><div className={styles.metricValue} style={{fontSize:20,marginTop:9}}>{coverage.frames??"—"} frames · {coverage.cameras??"—"} cameras</div><p>{coverage.window_local?`${coverage.window_local}. `:""}{coverage.assessment||"Coverage assessment is not available."}</p></article><article className={styles.finding}><div className={styles.findingTop}><span className={`${styles.dot} ${styles.dotAttention}`}/><b>Coverage limitation</b></div><p>{coverage.limitation||p.evidence_note||"This report only describes evidence that was available for review."}</p></article></div></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Site insights</h3><p>Management questions answered from the available visual evidence.</p></div></div><InsightCards items={insights}/></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Coverage by camera</h3><p>Stored image coverage and visual assessment for each evidence-producing camera.</p></div></div><div className={styles.cameraGrid}>{cameras.map((c,i)=><article className={styles.camera} key={`${c.channel}-${i}`}><div className={styles.cameraTop}><div className={styles.cameraName}>{c.camera}</div><span className={styles.channel}>Channel {c.channel}</span></div><div className={styles.cameraStats}><div className={styles.cameraStat}><span>Images</span><b>{c.frames??"—"}</b></div><div className={styles.cameraStat}><span>Evidence window</span><b>{c.window||"—"}</b></div><div className={styles.cameraStat}><span>Evidence type</span><b>Snapshots</b></div></div><p className={styles.assessment}>{c.assessment}</p></article>)}</div></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Recommended actions</h3><p>Changes that will make future daily reports more useful and more defensible.</p></div></div><div className={styles.actions}>{actions.map((a,i)=><div className={styles.action} key={i}>{a}</div>)}</div></section>

    {p.evidence_note&&<div className={styles.note}><b>How to read this report:</b> {p.evidence_note}</div>}
  </div>
}

export default function CustomerReports(){
  const r=useReport(),label=VIEWS.find(([k])=>k===r.view)?.[1]||"Report";
  const prompt=r.view==="yesterday"?"Explain yesterday's visual report for Main site. Focus on serious incidents, camera coverage, office opening/closing evidence, occupancy limitations and Armory access. Do not treat routine camera events as incidents or event counts as unique people.":"Explain this management report and tell me the priority action.";
  return <div className="shell"><Nav active="Reports" email={r.email} currentSiteId={r.siteId}/><main className="main"><header className="target-page-head"><div><div className="target-eyebrow">Reports</div><h1>Management report</h1><p>What happened, what needs attention, and whether WatchLog was watching reliably.</p></div><div className="target-actions"><a className={ui.secondaryLink} href={withSite("/reports/delivery/",r.siteId)}>Delivery</a></div></header>{r.error&&<div className="err">{r.error}</div>}<div className={ui.tabs}>{VIEWS.map(([k,l])=><button key={k} className={`${ui.tab} ${r.view===k?ui.tabActive:""}`} onClick={()=>r.setView(k)}>{l}</button>)}</div>{r.busy?<div className={ui.emptyCard}>Preparing the report…</div>:r.view==="yesterday"?(r.snapshot?<VisualReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>No saved report is available for yesterday yet.</div>):<section className="daily-report-card"><div className="daily-report-head"><Mark size={26}/><b>{label} report</b></div><div className="daily-report-body"><p style={{whiteSpace:"pre-wrap",margin:0}}>{r.answer||"No report is available yet."}</p></div></section>}<div className={ui.sectionHead}><div><h2>Need more detail?</h2><p>Ask WatchLog about any part of this report.</p></div><a className={ui.primaryLink} href={withSite(`/ai/?prompt=${encodeURIComponent(prompt)}`,r.siteId)}>Ask WatchLog</a></div></main></div>;
}
