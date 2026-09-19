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
function val(v,suffix=""){return v==null?"—":`${v}${suffix}`}

function EvidenceReport({snapshot}){
  const p=snapshot?.payload||{},metrics=p.metrics||[],findings=p.findings||[],cameras=p.camera_breakdown||[],actions=p.priority_actions||[];
  return <div className={styles.report}>
    <section className={styles.hero}>
      <div className={styles.heroTop}><div className={styles.heroTitle}><div className={styles.heroMark}><Mark size={26}/></div><div><div className={styles.kicker}>Evidence report</div><h2>{p.title||"Report of Yesterday"}</h2></div></div><div className={styles.date}>{dateLabel(p.report_date||snapshot?.report_date)}</div></div>
      <p className={styles.summary}>{p.executive_summary||"No executive summary is available for this report."}</p>
      <div className={styles.meta}><span>Snapshot evidence</span><span>Site: {p.site_name||"Main site"}</span><span>Generated {snapshot?.generated_at?new Date(snapshot.generated_at).toLocaleString():"recently"}</span></div>
    </section>
    {metrics.length>0&&<section className={styles.metrics}>{metrics.map((m,i)=><div className={styles.metric} key={`${m.label}-${i}`}><div className={styles.metricValue}>{m.value}</div><div className={styles.metricLabel}>{m.label}</div>{m.note&&<div className={styles.metricNote}>{m.note}</div>}</div>)}</section>}
    <section className={styles.section}><div className={styles.sectionHead}><div><h3>What matters</h3><p>Management findings derived from yesterday's camera evidence.</p></div></div><div className={styles.findings}>{findings.map((f,i)=><article className={styles.finding} key={`${f.title}-${i}`}><div className={styles.findingTop}><span className={`${styles.dot} ${severityClass(f.severity)}`}/><b>{f.title}</b></div><p>{f.body}</p></article>)}</div></section>
    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Camera evidence</h3><p>Detector volume, snapshot availability and independent human verification.</p></div></div><div className={styles.cameraGrid}>{cameras.map(c=><article className={styles.camera} key={c.channel}><div className={styles.cameraTop}><div className={styles.cameraName}>{c.camera}</div><span className={styles.channel}>Channel {c.channel}</span></div><div className={styles.cameraStats}><div className={styles.cameraStat}><span>Events</span><b>{val(c.events)}</b></div><div className={styles.cameraStat}><span>Snapshots</span><b>{val(c.snapshots)} · {val(c.snapshot_coverage_pct,"%")}</b></div><div className={styles.cameraStat}><span>Verified human</span><b>{c.verified_human==null?"Not measured":`${c.verified_human} · ${val(c.verification_pct,"%")}`}</b></div></div><p className={styles.assessment}>{c.assessment}</p></article>)}</div></section>
    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Priority actions</h3><p>Recommended follow-up from the evidence, ordered for operational value.</p></div></div><div className={styles.actions}>{actions.map((a,i)=><div className={styles.action} key={i}>{a}</div>)}</div></section>
    {p.evidence_note&&<div className={styles.note}><b>Evidence note:</b> {p.evidence_note}</div>}
  </div>
}

export default function CustomerReports(){
  const r=useReport(),label=VIEWS.find(([k])=>k===r.view)?.[1]||"Report";
  const prompt=r.view==="yesterday"?`Explain the Report of Yesterday for Main site. Focus on the important security and monitoring implications, and distinguish detector events from confirmed people.`:"Explain this management report and tell me the priority action.";
  return <div className="shell"><Nav active="Reports" email={r.email} currentSiteId={r.siteId}/><main className="main"><header className="target-page-head"><div><div className="target-eyebrow">Reports</div><h1>Management report</h1><p>What happened, what needs attention, and whether WatchLog was watching reliably.</p></div><div className="target-actions"><a className={ui.secondaryLink} href={withSite("/reports/delivery/",r.siteId)}>Delivery</a></div></header>{r.error&&<div className="err">{r.error}</div>}<div className={ui.tabs}>{VIEWS.map(([k,l])=><button key={k} className={`${ui.tab} ${r.view===k?ui.tabActive:""}`} onClick={()=>r.setView(k)}>{l}</button>)}</div>{r.busy?<div className={ui.emptyCard}>Preparing the report…</div>:r.view==="yesterday"?(r.snapshot?<EvidenceReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>No saved report is available for yesterday yet.</div>):<section className="daily-report-card"><div className="daily-report-head"><Mark size={26}/><b>{label} report</b></div><div className="daily-report-body"><p style={{whiteSpace:"pre-wrap",margin:0}}>{r.answer||"No report is available yet."}</p></div></section>}<div className={ui.sectionHead}><div><h2>Need more detail?</h2><p>Ask WatchLog about any part of this report.</p></div><a className={ui.primaryLink} href={withSite(`/ai/?prompt=${encodeURIComponent(prompt)}`,r.siteId)}>Ask WatchLog</a></div></main></div>;
}
