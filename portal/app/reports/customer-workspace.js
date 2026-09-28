"use client";
import {Nav} from "../shell";
import {withSite} from "../site-context";
import Mark from "../mark";
import ui from "../portal.module.css";
import styles from "./reports.module.css";
import useReport from "./use-report";

const VIEWS=[["daily","Today"],["yesterday","Yesterday"],["monthly","30 days"],["executive","Executive"]];
function dateLabel(v){if(!v)return"Yesterday";const d=new Date(v+"T12:00:00");return d.toLocaleDateString([], {weekday:"long",month:"long",day:"numeric",year:"numeric"})}
function severityClass(v){return v==="critical"?styles.dotCritical:v==="attention"?styles.dotAttention:""}
function val(v,suffix=""){return v==null?"—":String(v)+suffix}
function num(v){return v==null||Number.isNaN(Number(v))?null:Number(v)}
function pctWidth(v,max){const n=num(v),m=num(max);if(n==null||!m)return 0;return Math.max(3,Math.min(100,(n/m)*100))}

function RestaurantOperations({data}){
  if(!data?.enabled)return null;
  const quality=data.data_quality||{},sessions=data.sessions||{},hourly=(data.hourly||[]).filter(x=>Number(x.samples||0)>0),tables=data.tables||[];
  const observations=Number(quality.camera_observations||0),tableObservations=Number(quality.table_observations||0);
  if(observations===0&&tableObservations===0){
    return <section className={styles.restaurantShell}>
      <div className={styles.restaurantHead}><div><div className={styles.kicker}>Restaurant operations</div><h2>Visual business analytics is configured.</h2><p>WatchLog is waiting for structured visual observations before showing restaurant figures. No estimates are being fabricated from unprocessed snapshots.</p></div><span className={styles.processingPill}>Processing</span></div>
      <div className={styles.truthNote}><b>Measurement boundary:</b> current cameras can report visible diners and table activity, but not true unique customer footfall. A clean customer-entry counting line is required for footfall.</div>
    </section>;
  }

  const peakVisible=hourly.reduce((m,x)=>Math.max(m,Number(x.peak_visible_customers||0)),0);
  const peakOccupied=hourly.reduce((m,x)=>Math.max(m,Number(x.peak_occupied_tables||0)),0);
  const activeTables=tables.filter(x=>Number(x.samples||0)>0);
  const maxBar=Math.max(1,...hourly.map(x=>Number(x.peak_visible_customers||0)));
  return <section className={styles.restaurantShell}>
    <div className={styles.restaurantHead}><div><div className={styles.kicker}>Restaurant operations</div><h2>{data.service_date?"Service day · "+dateLabel(data.service_date):"Restaurant service day"}</h2><p>Visual dining and service-flow measurements for the configured opening window. Values marked estimated or observed are camera-derived, not POS data.</p></div><span className={styles.readyPill}>{observations} analyzed frames</span></div>

    <div className={styles.restaurantMetrics}>
      <div className={styles.restaurantMetric}><strong>{val(peakVisible)}</strong><span>Peak visible diners</span><small>Concurrent people visible on dining cameras</small></div>
      <div className={styles.restaurantMetric}><strong>{val(peakOccupied)}</strong><span>Peak occupied tables</span><small>Observed simultaneously occupied tables</small></div>
      <div className={styles.restaurantMetric}><strong>{val(sessions.estimated_covers)}</strong><span>Estimated covers</span><small>Peak party sizes summed across observed table sessions</small></div>
      <div className={styles.restaurantMetric}><strong>{val(sessions.served_sessions)}</strong><span>Served table sessions</span><small>Sessions where food became visibly present</small></div>
      <div className={styles.restaurantMetric}><strong>{sessions.median_observed_time_to_food_minutes==null?"—":String(sessions.median_observed_time_to_food_minutes)+" min"}</strong><span>Median observed time to food</span><small>Seated to first food visible, not order-to-serve</small></div>
    </div>

    <div className={styles.restaurantGrid}>
      <div className={styles.restaurantPanel}>
        <div className={styles.sectionHead}><div><h3>Dining activity by hour</h3><p>Peak visible diners from analyzed dining-floor observations.</p></div></div>
        <div className={styles.hourChart}>{hourly.length?hourly.map(h=><div className={styles.hourRow} key={h.hour_start}>
          <span className={styles.hourLabel}>{h.local_hour}</span>
          <div className={styles.hourTrack}><div className={styles.hourFill} style={{width:String(pctWidth(h.peak_visible_customers,maxBar))+"%"}}/></div>
          <b>{val(h.peak_visible_customers)}</b>
        </div>):<div className={styles.restaurantEmpty}>No hourly dining observations are available yet.</div>}</div>
      </div>

      <div className={styles.restaurantPanel}>
        <div className={styles.sectionHead}><div><h3>Table utilization</h3><p>Share of analyzed observations where each calibrated table was occupied.</p></div></div>
        <div className={styles.utilList}>{activeTables.length?activeTables.map(t=><div className={styles.utilRow} key={t.table_key}>
          <div className={styles.utilTop}><span>{t.label||t.table_key}</span><b>{t.occupancy_pct==null?"—":String(t.occupancy_pct)+"%"}</b></div>
          <div className={styles.utilTrack}><div className={styles.utilFill} style={{width:String(Math.max(0,Math.min(100,Number(t.occupancy_pct||0))))+"%"}}/></div>
          <small>{t.samples} observations · peak party {val(t.peak_party)}</small>
        </div>):<div className={styles.restaurantEmpty}>Table calibration is ready; table observations are still being processed.</div>}</div>
      </div>
    </div>

    <div className={styles.truthNote}><b>How to read this:</b> “visible diners” is concurrent visible people, not unique footfall. “Estimated covers” can be affected by occlusion or customers moving tables. “Observed time to food” starts when a party is first visibly seated and ends when food first becomes visible; it is not POS order-to-serve time.</div>
  </section>;
}

function EvidenceReport({snapshot}){
  const p=snapshot?.payload||{},metrics=p.metrics||[],findings=p.findings||[],cameras=p.camera_breakdown||[],actions=p.priority_actions||[];
  return <div className={styles.report}>
    <section className={styles.hero}>
      <div className={styles.heroTop}><div className={styles.heroTitle}><div className={styles.heroMark}><Mark size={26}/></div><div><div className={styles.kicker}>Evidence report</div><h2>{p.title||"Report of Yesterday"}</h2></div></div><div className={styles.date}>{dateLabel(p.report_date||snapshot?.report_date)}</div></div>
      <p className={styles.summary}>{p.executive_summary||"No executive summary is available for this report."}</p>
      <div className={styles.meta}><span>Snapshot evidence</span><span>Site: {p.site_name||"Main site"}</span><span>Generated {snapshot?.generated_at?new Date(snapshot.generated_at).toLocaleString():"recently"}</span></div>
    </section>
    {metrics.length>0&&<section className={styles.metrics}>{metrics.map((m,i)=><div className={styles.metric} key={m.label+"-"+i}><div className={styles.metricValue}>{m.value}</div><div className={styles.metricLabel}>{m.label}</div>{m.note&&<div className={styles.metricNote}>{m.note}</div>}</div>)}</section>}
    <section className={styles.section}><div className={styles.sectionHead}><div><h3>What matters</h3><p>Management findings derived from yesterday's camera evidence.</p></div></div><div className={styles.findings}>{findings.map((f,i)=><article className={styles.finding} key={f.title+"-"+i}><div className={styles.findingTop}><span className={styles.dot+" "+severityClass(f.severity)}/><b>{f.title}</b></div><p>{f.body}</p></article>)}</div></section>
    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Camera evidence</h3><p>Detector volume, snapshot availability and independent human verification.</p></div></div><div className={styles.cameraGrid}>{cameras.map(c=><article className={styles.camera} key={c.channel}><div className={styles.cameraTop}><div className={styles.cameraName}>{c.camera}</div><span className={styles.channel}>Channel {c.channel}</span></div><div className={styles.cameraStats}><div className={styles.cameraStat}><span>Events</span><b>{val(c.events)}</b></div><div className={styles.cameraStat}><span>Snapshots</span><b>{val(c.snapshots)} · {val(c.snapshot_coverage_pct,"%")}</b></div><div className={styles.cameraStat}><span>Verified human</span><b>{c.verified_human==null?"Not measured":String(c.verified_human)+" · "+val(c.verification_pct,"%")}</b></div></div><p className={styles.assessment}>{c.assessment}</p></article>)}</div></section>
    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Priority actions</h3><p>Recommended follow-up from the evidence, ordered for operational value.</p></div></div><div className={styles.actions}>{actions.map((a,i)=><div className={styles.action} key={i}>{a}</div>)}</div></section>
    {p.evidence_note&&<div className={styles.note}><b>Evidence note:</b> {p.evidence_note}</div>}
  </div>
}

export default function CustomerReports(){
  const r=useReport(),label=VIEWS.find(([k])=>k===r.view)?.[1]||"Report";
  const prompt=r.view==="yesterday"?"Explain the Report of Yesterday for this site. Include restaurant operations when available, distinguish visible diners from unique footfall, and distinguish observed service timing from POS order-to-serve time.":"Explain this management report and tell me the priority action.";
  return <div className="shell"><Nav active="Reports" email={r.email} currentSiteId={r.siteId}/><main className="main"><header className="target-page-head"><div><div className="target-eyebrow">Reports</div><h1>Management report</h1><p>What happened, what needs attention, and whether WatchLog was watching reliably.</p></div><div className="target-actions"><a className={ui.secondaryLink} href={withSite("/reports/delivery/",r.siteId)}>Delivery</a></div></header>{r.error&&<div className="err">{r.error}</div>}<div className={ui.tabs}>{VIEWS.map(([k,l])=><button key={k} className={ui.tab+" "+(r.view===k?ui.tabActive:"")} onClick={()=>r.setView(k)}>{l}</button>)}</div>{r.busy?<div className={ui.emptyCard}>Preparing the report…</div>:r.view==="yesterday"?<><RestaurantOperations data={r.restaurant}/>{r.snapshot?<EvidenceReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>No saved evidence report is available for this service day yet.</div>}</>:<><section className="daily-report-card"><div className="daily-report-head"><Mark size={26}/><b>{label} report</b></div><div className="daily-report-body"><p style={{whiteSpace:"pre-wrap",margin:0}}>{r.answer||"No report is available yet."}</p></div></section>{r.view==="daily"&&<RestaurantOperations data={r.restaurant}/>}</>}<div className={ui.sectionHead}><div><h2>Need more detail?</h2><p>Ask WatchLog about any part of this report.</p></div><a className={ui.primaryLink} href={withSite("/ai/?prompt="+encodeURIComponent(prompt),r.siteId)}>Ask WatchLog</a></div></main></div>;
}
