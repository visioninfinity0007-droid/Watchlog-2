"use client";
import {Nav} from "../shell";
import {withSite} from "../site-context";
import Mark from "../mark";
import ui from "../portal.module.css";
import styles from "./reports.module.css";
import useReport from "./use-report";

const VIEWS=[["daily","Today"],["yesterday","Yesterday"],["monthly","30 days"],["executive","Executive"]];
const RESTAURANT_VIEWS=[["daily","Today"],["yesterday","Yesterday"],["week","Last 7 days"],["monthly","Last 30 days"]];
function dateLabel(v){if(!v)return"—";const d=new Date(v+"T12:00:00");return d.toLocaleDateString([], {weekday:"long",month:"long",day:"numeric",year:"numeric"})}
function shortDate(v){if(!v)return"—";const d=new Date(v+"T12:00:00");return d.toLocaleDateString([], {month:"short",day:"numeric"})}
function severityClass(v){return v==="critical"?styles.dotCritical:v==="attention"?styles.dotAttention:""}
function val(v,suffix=""){return v==null?"—":String(v)+suffix}
function num(v){return v==null||Number.isNaN(Number(v))?null:Number(v)}
function pctWidth(v,max){const n=num(v),m=num(max);if(n==null||!m)return 0;return Math.max(3,Math.min(100,(n/m)*100))}
function coverageLabel(v){const n=num(v);return n==null?"—":Math.round(n*100)+"%"}
function signed(v,suffix=""){const n=num(v);if(n==null)return"—";return(n>0?"+":"")+String(n)+suffix}
function managementDate(v){return v?dateLabel(v):"selected service day"}
function scorePct(v){const n=num(v);return n==null?null:Math.round(Math.max(0,Math.min(1,n))*100)}
function roleLabel(v){return String(v||"").replaceAll("_"," ").replace(/\b\w/g,m=>m.toUpperCase())}

function AnalyticsQuality({quality}){
  const q=quality||{},cameras=q.camera_quality||[],recs=[...(q.recommendations||[])].sort((a,b)=>(a.severity==="high"?0:1)-(b.severity==="high"?0:1));
  const validation=q.accuracy_validation||{};
  if(!Number(q.scored_frames||0)&&!cameras.length){
    return <section className={styles.qualityShell}>
      <div className={styles.sectionHead}><div><h3>Analytics quality & improvement recommendations</h3><p>Camera geometry and image quality determine how trustworthy customer/table analytics can be.</p></div></div>
      <div className={styles.qualityWaiting}><b>Quality scoring is waiting for processed restaurant frames.</b><span>WatchLog will measure glare, overexposure, occlusion, obstruction, camera angle, visible-diner count confidence and movable-table tracking confidence. It will not publish a customer-count accuracy percentage until representative Floor 1 and Floor 2 frames are manually validated.</span></div>
    </section>;
  }
  return <section className={styles.qualityShell}>
    <div className={styles.sectionHead}><div><h3>Analytics quality & improvement recommendations</h3><p>Repeated camera-view problems are surfaced before WatchLog recommends operational changes.</p></div><span className={styles.qualityScored}>{val(q.scored_frames)} scored frames</span></div>
    <div className={styles.qualityGrid}>{cameras.map(c=>{
      const visibility=scorePct(c.visibility_quality),count=scorePct(c.people_count_confidence),tables=scorePct(c.table_tracking_confidence),glare=scorePct(c.glare_level),occ=scorePct(c.occlusion_level),angle=scorePct(c.camera_angle_adequacy);
      return <article className={styles.qualityCard} key={c.camera_id||c.camera}>
        <div className={styles.qualityCardHead}><div><b>{c.camera}</b><span>{roleLabel(c.role)}</span></div><strong>{visibility==null?"—":visibility+"%"}</strong></div>
        <div className={styles.qualityTrack}><div className={styles.qualityFill} style={{width:String(visibility||0)+"%"}}/></div>
        <div className={styles.qualityStats}>
          <span><small>Visibility</small><b>{visibility==null?"—":visibility+"%"}</b></span>
          {c.role==="dining_floor"&&<span><small>People count</small><b>{count==null?"—":count+"%"}</b></span>}
          {c.role==="dining_floor"&&<span><small>Table tracking</small><b>{tables==null?"—":tables+"%"}</b></span>}
          <span><small>Glare risk</small><b>{glare==null?"—":glare+"%"}</b></span>
          <span><small>Occlusion</small><b>{occ==null?"—":occ+"%"}</b></span>
          <span><small>Angle quality</small><b>{angle==null?"—":angle+"%"}</b></span>
        </div>
        {(c.blocked_regions||[]).length>0&&<div className={styles.blockedList}><small>Repeatedly blocked:</small>{c.blocked_regions.map((x,i)=><span key={i}>{x}</span>)}</div>}
      </article>
    })}</div>
    <div className={styles.improvementList}>{recs.length?recs.map((r,i)=><article className={styles.improvementCard+" "+(r.severity==="high"?styles.improvementHigh:styles.improvementMedium)} key={(r.camera||"camera")+"-"+i}>
      <div className={styles.improvementTop}><span>{String(r.severity||"medium").toUpperCase()}</span><b>{r.camera}</b></div>
      <h4>{r.issue}</h4>
      <p>{r.evidence}</p>
      <div className={styles.improvementAction}><b>Recommended improvement</b><span>{r.recommendation}</span></div>
      {(r.metrics_impacted||[]).length>0&&<small>Affects: {r.metrics_impacted.join(" · ")}</small>}
    </article>):<div className={styles.qualityClear}>No repeated camera-quality problem has crossed the recommendation threshold in the analyzed frames.</div>}</div>
    <div className={styles.accuracyNote}><b>Customer-count accuracy:</b> {validation.note||"Confidence is not the same as measured accuracy. Manual validation against representative Floor 1 and Floor 2 frames is required before publishing an accuracy percentage."}</div>
  </section>;
}

function RestaurantOperations({data,periodLabel}){
  if(!data?.enabled)return null;
  const quality=data.data_quality||{},sessions=data.sessions||{},hourly=(data.hourly||[]).filter(x=>Number(x.samples||0)>0),tables=data.tables||[],floors=(data.floors||[]).filter(x=>Number(x.samples||0)>0);
  const observations=Number(quality.camera_observations||0),tableObservations=Number(quality.table_observations||0),coverage=num(quality.business_analytics_coverage_ratio);
  if(observations===0&&tableObservations===0){
    return <section className={styles.restaurantShell}>
      <div className={styles.restaurantHead}><div><div className={styles.kicker}>Restaurant operations · {periodLabel||"service day"}</div><h2>Visual business analytics is configured.</h2><p>WatchLog is waiting for structured visual observations before showing restaurant figures. No estimates are being fabricated from unprocessed snapshots.</p></div><span className={styles.processingPill}>Processing</span></div>
      <AnalyticsQuality quality={data.analytics_quality}/>
      <div className={styles.truthNote}><b>Measurement boundary:</b> current cameras can report visible diners and table activity, but not true unique customer footfall. A clean customer-entry counting line is required for footfall.</div>
    </section>;
  }

  const peakVisible=hourly.reduce((m,x)=>Math.max(m,Number(x.peak_visible_customers||0)),0);
  const peakOccupied=hourly.reduce((m,x)=>Math.max(m,Number(x.peak_occupied_tables||0)),0);
  const activeTables=tables.filter(x=>Number(x.samples||0)>0);
  const maxBar=Math.max(1,...hourly.map(x=>Number(x.peak_visible_customers||0)));
  const peakKitchen=Math.max(0,...hourly.map(x=>Number(x.avg_kitchen_load||0)));
  const peakHandoff=Math.max(0,...hourly.map(x=>Number(x.avg_handoff_load||0)));
  return <section className={styles.restaurantShell}>
    <div className={styles.restaurantHead}><div><div className={styles.kicker}>Restaurant operations · {periodLabel||"service day"}</div><h2>{data.service_date?"Service day · "+dateLabel(data.service_date):"Restaurant service day"}</h2><p>Chai Wala operating view for the 4 PM–4 AM service window. Values marked estimated or observed are camera-derived, not POS data.</p></div><span className={styles.readyPill}>{coverage==null?observations+" analyzed frames":coverageLabel(coverage)+" coverage · "+observations+" frames"}</span></div>

    <div className={styles.restaurantMetrics}>
      <div className={styles.restaurantMetric}><strong>{val(peakVisible)}</strong><span>Peak visible diners</span><small>Complete dining-floor composite only</small></div>
      <div className={styles.restaurantMetric}><strong>{val(peakOccupied)}</strong><span>Peak occupied tables</span><small>Observed simultaneously occupied tables</small></div>
      <div className={styles.restaurantMetric}><strong>{val(sessions.estimated_covers)}</strong><span>Estimated covers</span><small>Camera-derived estimate across observed table sessions</small></div>
      <div className={styles.restaurantMetric}><strong>{val(sessions.served_sessions)}</strong><span>Served table sessions</span><small>Sessions where food became visibly present</small></div>
      <div className={styles.restaurantMetric}><strong>{sessions.median_observed_time_to_food_minutes==null?"—":String(sessions.median_observed_time_to_food_minutes)+" min"}</strong><span>Median observed time to food</span><small>Seated to first food visible, not POS timing</small></div>
      <div className={styles.restaurantMetric}><strong>{coverageLabel(coverage)}</strong><span>Analytics coverage</span><small>Actual analyzed samples versus configured target</small></div>
    </div>

    <AnalyticsQuality quality={data.analytics_quality}/>

    <div className={styles.restaurantGrid}>
      <div className={styles.restaurantPanel}>
        <div className={styles.sectionHead}><div><h3>Dining activity by hour</h3><p>Peak site-wide visible diners when both dining floors have a same-minute observation.</p></div></div>
        <div className={styles.hourChart}>{hourly.length?hourly.map(h=><div className={styles.hourRow} key={h.hour_start}>
          <span className={styles.hourLabel}>{h.local_hour}</span>
          <div className={styles.hourTrack}><div className={styles.hourFill} style={{width:String(pctWidth(h.peak_visible_customers,maxBar))+"%"}}/></div>
          <b>{val(h.peak_visible_customers)}</b>
        </div>):<div className={styles.restaurantEmpty}>No complete dining-floor hourly observations are available yet.</div>}</div>
      </div>

      <div className={styles.restaurantPanel}>
        <div className={styles.sectionHead}><div><h3>Floor comparison</h3><p>Keep Floor 1 and Floor 2 separate before using site-wide totals.</p></div></div>
        <div className={styles.floorCards}>{floors.length?floors.map(f=><div className={styles.floorCard} key={f.camera_id||f.floor}>
          <div className={styles.floorName}>{f.floor}</div>
          <div className={styles.floorStats}><span><b>{val(f.peak_visible_customers)}</b> peak diners</span><span><b>{val(f.peak_occupied_tables)}</b> peak tables</span><span><b>{val(f.avg_visible_customers)}</b> avg visible</span></div>
        </div>):<div className={styles.restaurantEmpty}>No floor-level observations are available yet.</div>}</div>
        <div className={styles.pressureStrip}>
          <div><span>Peak kitchen pressure</span><b>{peakKitchen?Math.round(peakKitchen*100)+"%":"—"}</b></div>
          <div><span>Peak handoff pressure</span><b>{peakHandoff?Math.round(peakHandoff*100)+"%":"—"}</b></div>
        </div>
      </div>
    </div>

    <div className={styles.restaurantPanel}>
      <div className={styles.sectionHead}><div><h3>Table utilization</h3><p>Share of valid analyzed observations where each calibrated table was occupied.</p></div></div>
      <div className={styles.tableColumns}>{activeTables.length?activeTables.map(t=><div className={styles.utilRow} key={t.table_key}>
        <div className={styles.utilTop}><span>{t.label||t.table_key}</span><b>{t.occupancy_pct==null?"—":String(t.occupancy_pct)+"%"}</b></div>
        <div className={styles.utilTrack}><div className={styles.utilFill} style={{width:String(Math.max(0,Math.min(100,Number(t.occupancy_pct||0))))+"%"}}/></div>
        <small>{t.samples} observations · peak party {val(t.peak_party)}</small>
      </div>):<div className={styles.restaurantEmpty}>Table calibration is ready; table observations are still being processed.</div>}</div>
    </div>

    <div className={styles.truthNote}><b>How to read this:</b> “visible diners” is concurrent visible people, not unique footfall. “Estimated covers” can be affected by occlusion or customers moving tables. “Observed time to food” starts when a party is first visibly seated and ends when food first becomes visible; it is not POS order-to-serve time. Missing observation periods are missing coverage, not zero activity.</div>
  </section>;
}

function RestaurantPeriodReport({data,days}){
  if(!data?.enabled)return null;
  const summary=data.summary||{},prev=data.previous_period||{},cmp=data.comparison||{},daily=data.daily||[],hours=data.hour_profile||[],floors=data.floor_profile||[],tables=data.table_profile||[],weekdays=data.weekday_profile||[],weeks=data.weekly_trend||[],dist=data.service_time_distribution||{};
  const observed=Number(summary.observed_service_days||0),coverage=num(summary.avg_coverage_ratio);
  const title=days===7?"7-day operations review":"30-day management review";
  const maxDaily=Math.max(1,...daily.map(x=>Number(x.estimated_covers||0)));
  const maxWeekly=Math.max(1,...weeks.map(x=>Number(x.estimated_covers||0)));
  const maxHour=Math.max(1,...hours.map(x=>Number(x.avg_peak_visible_diners||0)));
  const trend=days===7?daily:weeks;
  const maxTrend=days===7?maxDaily:maxWeekly;
  const topTables=tables.slice(0,10);
  const lowTables=[...tables].filter(x=>num(x.occupancy_pct)!=null).sort((a,b)=>Number(a.occupancy_pct)-Number(b.occupancy_pct)).slice(0,5);
  const busiestDay=summary.busiest_day||null,busiestHour=summary.busiest_hour||null;

  return <section className={styles.periodShell}>
    <div className={styles.periodHero}>
      <div><div className={styles.kicker}>Chai Wala · restaurant intelligence</div><h2>{title}</h2><p>{shortDate(data.period?.start_service_date)} – {shortDate(data.period?.end_service_date)} · service days run 4 PM–4 AM</p></div>
      <div className={styles.periodHeroFacts}>
        <span><b>{observed}</b>/{days} observed days</span>
        <span><b>{coverageLabel(coverage)}</b> avg coverage</span>
      </div>
    </div>

    {observed===0?<div className={styles.restaurantEmpty}>No processed restaurant observations are available in this period yet. The layout is ready, but WatchLog will not fabricate demand, cover or service-time figures.</div>:<>
      <div className={styles.periodMetrics}>
        <div className={styles.restaurantMetric}><strong>{val(summary.total_estimated_covers)}</strong><span>Estimated covers</span><small>Total camera-derived estimate for observed sessions</small></div>
        <div className={styles.restaurantMetric}><strong>{val(summary.avg_estimated_covers_per_observed_day)}</strong><span>Avg covers / observed day</span><small>Uses only service days with observations</small></div>
        <div className={styles.restaurantMetric}><strong>{val(summary.served_sessions)}</strong><span>Served table sessions</span><small>Food became visibly present</small></div>
        <div className={styles.restaurantMetric}><strong>{summary.median_observed_time_to_food_minutes==null?"—":summary.median_observed_time_to_food_minutes+" min"}</strong><span>Median observed time to food</span><small>Across observed sessions in this period</small></div>
        <div className={styles.restaurantMetric}><strong>{coverageLabel(coverage)}</strong><span>Average coverage</span><small>Missing time is never treated as zero demand</small></div>
        <div className={styles.restaurantMetric}><strong>{busiestHour?.local_hour||"—"}</strong><span>Busiest observed hour</span><small>{busiestHour?.avg_peak_visible_diners==null?"No complete composite":"Avg peak visible diners "+busiestHour.avg_peak_visible_diners}</small></div>
      </div>

      <div className={styles.compareStrip}>
        <div><span>Estimated covers vs prior {days} days</span><b>{signed(cmp.estimated_covers_delta)}</b><small>{cmp.estimated_covers_pct==null?"Prior period had no comparable covers":signed(cmp.estimated_covers_pct,"%")}</small></div>
        <div><span>Served sessions vs prior</span><b>{signed(cmp.served_sessions_delta)}</b><small>{cmp.served_sessions_pct==null?"No prior comparison":signed(cmp.served_sessions_pct,"%")}</small></div>
        <div><span>Median time-to-food change</span><b>{signed(cmp.median_time_to_food_delta_minutes," min")}</b><small>Current minus prior period; camera-observed</small></div>
        <div><span>Coverage change</span><b>{signed(cmp.coverage_delta_points," pts")}</b><small>Percentage-point change in analysis coverage</small></div>
      </div>

      <AnalyticsQuality quality={data.analytics_quality}/>

      <div className={styles.periodGrid}>
        <div className={styles.restaurantPanel}>
          <div className={styles.sectionHead}><div><h3>{days===7?"Service day trend":"Weekly trend"}</h3><p>{days===7?"Estimated covers by service day.":"Estimated covers grouped by calendar week."}</p></div></div>
          <div className={styles.trendChart}>{trend.length?trend.map((x,i)=>{
            const value=Number(x.estimated_covers||0),label=days===7?shortDate(x.service_date):shortDate(x.week_start);
            return <div className={styles.trendRow} key={(x.service_date||x.week_start||i)+"-"+i}><span>{label}</span><div className={styles.hourTrack}><div className={styles.trendFill} style={{width:String(pctWidth(value,maxTrend))+"%"}}/></div><b>{value}</b></div>
          }):<div className={styles.restaurantEmpty}>No trend data is available yet.</div>}</div>
          {busiestDay&&<div className={styles.panelFoot}>Busiest observed service day: <b>{managementDate(busiestDay.service_date)}</b> · estimated covers {val(busiestDay.estimated_covers)} · peak visible diners {val(busiestDay.peak_visible_diners)}</div>}
        </div>

        <div className={styles.restaurantPanel}>
          <div className={styles.sectionHead}><div><h3>Demand by hour</h3><p>Average peak visible diners across service days with a complete dining-floor composite.</p></div></div>
          <div className={styles.hourChart}>{hours.length?hours.map(h=><div className={styles.hourRow} key={h.local_hour}>
            <span className={styles.hourLabel}>{h.local_hour}</span><div className={styles.hourTrack}><div className={styles.hourFill} style={{width:String(pctWidth(h.avg_peak_visible_diners,maxHour))+"%"}}/></div><b>{val(h.avg_peak_visible_diners)}</b>
          </div>):<div className={styles.restaurantEmpty}>No complete hourly composites are available yet.</div>}</div>
        </div>
      </div>

      {days===30&&<div className={styles.restaurantPanel}>
        <div className={styles.sectionHead}><div><h3>Weekday pattern</h3><p>Average camera-derived demand and service timing by day of week.</p></div></div>
        <div className={styles.weekdayGrid}>{weekdays.map(w=><div className={styles.weekdayCard} key={w.iso_day}><b>{w.weekday}</b><strong>{val(w.avg_estimated_covers)}</strong><span>avg est. covers</span><small>{val(w.avg_peak_visible_diners)} avg peak diners · {w.avg_daily_median_time_to_food==null?"—":w.avg_daily_median_time_to_food+" min"} time to food</small></div>)}</div>
      </div>}

      <div className={styles.periodGrid}>
        <div className={styles.restaurantPanel}>
          <div className={styles.sectionHead}><div><h3>Floor comparison</h3><p>Customer-area demand by dining floor over this period.</p></div></div>
          <div className={styles.floorCards}>{floors.length?floors.map(f=><div className={styles.floorCard} key={f.camera_id||f.floor}><div className={styles.floorName}>{f.floor}</div><div className={styles.floorStats}><span><b>{val(f.peak_visible_diners)}</b> peak diners</span><span><b>{val(f.avg_visible_diners)}</b> avg visible</span><span><b>{val(f.peak_occupied_tables)}</b> peak occupied tables</span></div><small>{f.observed_days} observed service days · {f.samples} samples</small></div>):<div className={styles.restaurantEmpty}>No floor comparison is available yet.</div>}</div>
        </div>
        <div className={styles.restaurantPanel}>
          <div className={styles.sectionHead}><div><h3>Observed service-time distribution</h3><p>Seated/occupied to first food visible. This is not POS ticket time.</p></div></div>
          <div className={styles.distributionGrid}>
            <div><strong>{val(dist.under_15_minutes)}</strong><span>Under 15 min</span></div>
            <div><strong>{val(dist["15_to_30_minutes"])}</strong><span>15–30 min</span></div>
            <div><strong>{val(dist["30_to_45_minutes"])}</strong><span>30–45 min</span></div>
            <div><strong>{val(dist["45_plus_minutes"])}</strong><span>45+ min</span></div>
          </div>
          <div className={styles.panelFoot}>{val(dist.sample_sessions)} sessions had a defensible observed time-to-food measurement.</div>
        </div>
      </div>

      <div className={styles.periodGrid}>
        <div className={styles.restaurantPanel}>
          <div className={styles.sectionHead}><div><h3>Most-used calibrated tables</h3><p>Occupancy share across valid observations in this period.</p></div></div>
          <div className={styles.utilList}>{topTables.length?topTables.map(t=><div className={styles.utilRow} key={t.table_key}><div className={styles.utilTop}><span>{t.label||t.table_key}</span><b>{val(t.occupancy_pct,"%")}</b></div><div className={styles.utilTrack}><div className={styles.utilFill} style={{width:String(Math.max(0,Math.min(100,Number(t.occupancy_pct||0))))+"%"}}/></div><small>{t.observed_days} observed days · {t.samples} observations · peak party {val(t.peak_party)}</small></div>):<div className={styles.restaurantEmpty}>No table observations are available yet.</div>}</div>
        </div>
        <div className={styles.restaurantPanel}>
          <div className={styles.sectionHead}><div><h3>Lower-utilization tables</h3><p>Useful for layout review only when coverage is adequate and table anchors stayed visible.</p></div></div>
          <div className={styles.utilList}>{lowTables.length?lowTables.map(t=><div className={styles.utilRow} key={t.table_key}><div className={styles.utilTop}><span>{t.label||t.table_key}</span><b>{val(t.occupancy_pct,"%")}</b></div><div className={styles.utilTrack}><div className={styles.utilFill} style={{width:String(Math.max(0,Math.min(100,Number(t.occupancy_pct||0))))+"%"}}/></div><small>{t.observed_days} observed days · {t.samples} valid observations</small></div>):<div className={styles.restaurantEmpty}>No comparable table utilization is available yet.</div>}</div>
        </div>
      </div>
    </>}

    <div className={styles.truthNote}><b>Management boundary:</b> these are camera-derived operational measurements. Visible diners are not unique footfall; covers and sessions are estimates; service timing is visually observed, not POS order-to-serve. Period-to-period changes should only be acted on when coverage is sufficiently comparable.</div>
  </section>;
}

function ManagementReading({answer,label}){
  if(!answer)return null;
  return <section className={styles.managementReading}><div className={styles.kicker}>WatchLog management reading</div><h3>{label||"What the period suggests"}</h3><p>{answer}</p></section>;
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
  const r=useReport();
  const views=r.isChaiWalaRestaurant?RESTAURANT_VIEWS:VIEWS;
  const label=views.find(([k])=>k===r.view)?.[1]||"Report";
  const prompts={
    daily:"Explain today's Chai Wala restaurant operations. Focus on demand, tables, observed service timing, service pressure, coverage and any security attention.",
    yesterday:"Explain yesterday's Chai Wala restaurant report. Focus on demand, tables, observed service timing, service pressure, coverage and any security attention.",
    week:"Explain the last 7 Chai Wala service days. Identify repeated demand, table-utilization and observed service-time patterns, and practical improvements supported by the evidence.",
    monthly:"Explain the last 30 Chai Wala service days. Identify weekly, weekday and hourly patterns, floor/table utilization, observed service-time trends and practical improvements supported by the evidence."
  };
  const prompt=r.isChaiWalaRestaurant?(prompts[r.view]||prompts.daily):(r.view==="yesterday"?"Explain the Report of Yesterday for this site. Include restaurant operations when available, distinguish visible diners from unique footfall, and distinguish observed service timing from POS order-to-serve time.":"Explain this management report and tell me the priority action.");

  let reportBody=null;
  if(r.isChaiWalaRestaurant){
    if(r.view==="daily"||r.view==="yesterday"){
      reportBody=<>
        <RestaurantOperations data={r.restaurant} periodLabel={r.view==="daily"?"Today":"Yesterday"}/>
        <ManagementReading answer={r.answer} label={r.view==="daily"?"Today's management reading":"Yesterday's management reading"}/>
        {r.view==="yesterday"&&(r.snapshot?<EvidenceReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>No saved security/evidence report is available for this service day yet.</div>)}
      </>;
    }else if(r.view==="week"||r.view==="monthly"){
      const days=r.view==="week"?7:30;
      reportBody=<>
        <RestaurantPeriodReport data={r.restaurantPeriod} days={days}/>
        <ManagementReading answer={r.answer} label={days===7?"What the last 7 days suggest":"What the last 30 days suggest"}/>
      </>;
    }
  }else{
    reportBody=r.view==="yesterday"?<><RestaurantOperations data={r.restaurant} periodLabel="Yesterday"/>{r.snapshot?<EvidenceReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>No saved evidence report is available for this service day yet.</div>}</>:<><section className="daily-report-card"><div className="daily-report-head"><Mark size={26}/><b>{label} report</b></div><div className="daily-report-body"><p style={{whiteSpace:"pre-wrap",margin:0}}>{r.answer||"No report is available yet."}</p></div></section>{r.view==="daily"&&<RestaurantOperations data={r.restaurant} periodLabel="Today"/>}</>;
  }

  return <div className="shell"><Nav active="Reports" email={r.email} currentSiteId={r.siteId}/><main className="main"><header className="target-page-head"><div><div className="target-eyebrow">Reports</div><h1>{r.isChaiWalaRestaurant?"Chai Wala management report":"Management report"}</h1><p>{r.isChaiWalaRestaurant?"Demand, table utilization, observed service timing, operating pressure and monitoring coverage.":"What happened, what needs attention, and whether WatchLog was watching reliably."}</p></div><div className="target-actions"><a className={ui.secondaryLink} href={withSite("/reports/delivery/",r.siteId)}>Delivery</a></div></header>{r.error&&<div className="err">{r.error}</div>}<div className={ui.tabs}>{views.map(([k,l])=><button key={k} className={ui.tab+" "+(r.view===k?ui.tabActive:"")} onClick={()=>r.setView(k)}>{l}</button>)}</div>{r.busy?<div className={ui.emptyCard}>Preparing the report…</div>:reportBody}<div className={ui.sectionHead}><div><h2>Need more detail?</h2><p>Ask WatchLog about any part of this report.</p></div><a className={ui.primaryLink} href={withSite("/ai/?prompt="+encodeURIComponent(prompt),r.siteId)}>Ask WatchLog</a></div></main></div>;
}
