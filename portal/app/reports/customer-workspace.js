"use client";
import {useState} from "react";
import RichText from "../rich-text";
import {Nav} from "../shell";
import {withSite} from "../site-context";
import Mark from "../mark";
import ui from "../portal.module.css";
import styles from "./reports.module.css";
import useReport from "./use-report";

const VIEWS=[["daily","Today"],["yesterday","Yesterday"],["monthly","30 days"],["executive","Executive"]];
const RESTAURANT_VIEWS=[["daily","Today"],["yesterday","Yesterday"],["week","Last 7 days"],["monthly","Last 30 days"]];
const OFFICE_VIEWS=[["daily","Today"],["yesterday","Yesterday"],["week","Last 7 days"],["monthly","Last 30 days"]];
function dateLabel(v){if(!v)return"—";const d=new Date(v+"T12:00:00");return d.toLocaleDateString([], {weekday:"long",month:"long",day:"numeric",year:"numeric"})}
function shortDate(v){if(!v)return"—";const d=new Date(v+"T12:00:00");return d.toLocaleDateString([], {month:"short",day:"numeric"})}
function severityClass(v){return v==="critical"?styles.dotCritical:v==="attention"?styles.dotAttention:v==="none"||v==="clear"?styles.dotGood:""}
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
      <div className={styles.qualityWaiting}><b>Camera-quality insights are not available for this period yet.</b><span>WatchLog only publishes visibility and counting-confidence guidance when it can support the result. A customer-count accuracy percentage is never shown without representative manual validation.</span></div>
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
      <div className={styles.restaurantHead}><div><div className={styles.kicker}>Restaurant operations · {periodLabel||"service day"}</div><h2>Business analytics are not available for this period yet.</h2><p>WatchLog will only show diner, table and service figures when the available evidence is strong enough to support them.</p></div><span className={styles.processingPill}>Not available</span></div>
      <AnalyticsQuality quality={data.analytics_quality}/>
      <div className={styles.truthNote}><b>Measurement boundary:</b> the current dining views can support visible diner and table activity, but not true unique customer footfall. A dedicated entrance counting line is required for footfall.</div>
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

    {observed===0?<div className={styles.restaurantEmpty}>No reliable restaurant business analytics are available for this period yet. WatchLog will not fabricate demand, cover or service-time figures.</div>:<>
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

function OfficeDayReport({data,periodLabel}){
  if(!data)return <div className={ui.emptyCard}>No structured office intelligence is available for this working day yet.</div>;
  const office=data.office||{},attn=data.attention||{},coverage=data.coverage||{},bounds=data.day_boundaries||{},meta=data.meta||{};
  const byArea=office.by_area||[],restricted=data.restricted||[],incidents=data.incidents||[];
  const activity=Number(office?.coverage?.person_events||0);
  const afterHours=Number(data?.after_hours?.count??office.after_hours_total??0);
  const cov=num(coverage.coverage_ratio);
  const maxArea=Math.max(1,...byArea.map(x=>Number(x.events||0)));
  return <section className={styles.periodShell}>
    <div className={styles.periodHero}>
      <div><div className={styles.kicker}>Office operations · {periodLabel}</div><h2>{meta.date?dateLabel(meta.date):periodLabel}</h2><p>Security, operating-day activity and monitoring reliability. Activity detections are not unique people.</p></div>
      <div className={styles.periodHeroFacts}><span><b>{coverageLabel(cov)}</b> coverage</span><span><b>{activity}</b> activity detections</span></div>
    </div>
    <div className={styles.periodMetrics}>
      <div className={styles.restaurantMetric}><strong>{val(attn.incidents_total)}</strong><span>Attention items</span><small>{val(attn.critical)} critical · {val(attn.warning)} warning</small></div>
      <div className={styles.restaurantMetric}><strong>{activity}</strong><span>Activity detections</span><small>Camera detections, not unique people</small></div>
      <div className={styles.restaurantMetric}><strong>{afterHours}</strong><span>After-hours observations</span><small>Outside configured working hours</small></div>
      <div className={styles.restaurantMetric}><strong>{office?.peak_hour?.hour==null?"—":String(office.peak_hour.hour).padStart(2,"0")+":00"}</strong><span>Peak activity hour</span><small>{val(office?.peak_hour?.count)} detections in peak hour</small></div>
      <div className={styles.restaurantMetric}><strong>{bounds.opening_at||"—"}</strong><span>Observed opening</span><small>Coverage/mapping dependent</small></div>
      <div className={styles.restaurantMetric}><strong>{bounds.closing_at||"—"}</strong><span>Observed closing</span><small>Coverage/mapping dependent</small></div>
    </div>

    <div className={styles.periodGrid}>
      <div className={styles.restaurantPanel}>
        <div className={styles.sectionHead}><div><h3>Activity by monitored area</h3><p>Detection volume by camera/area. This is not a unique-person count.</p></div></div>
        <div className={styles.hourChart}>{byArea.length?byArea.map((a,i)=><div className={styles.hourRow} key={(a.camera||"area")+"-"+i}>
          <span className={styles.hourLabel}>{a.camera||"Area"}</span>
          <div className={styles.hourTrack}><div className={styles.hourFill} style={{width:String(pctWidth(a.events,maxArea))+"%"}}/></div>
          <b>{val(a.events)}</b>
        </div>):<div className={styles.restaurantEmpty}>No office-area activity detections are available for this day.</div>}</div>
      </div>
      <div className={styles.restaurantPanel}>
        <div className={styles.sectionHead}><div><h3>Security & restricted-area attention</h3><p>Only mapped/verified evidence is shown as role-specific activity.</p></div></div>
        <div className={styles.floorCards}>
          <div className={styles.floorCard}><div className={styles.floorName}>Incidents / alerts</div><div className={styles.floorStats}><span><b>{val(attn.incidents_total)}</b> total</span><span><b>{val(attn.critical)}</b> critical</span><span><b>{val(attn.warning)}</b> warning</span></div></div>
          <div className={styles.floorCard}><div className={styles.floorName}>Restricted-area episodes</div><div className={styles.floorStats}><span><b>{restricted.reduce((n,x)=>n+Number(x.episodes||0),0)}</b> episodes</span><span><b>{restricted.reduce((n,x)=>n+Number(x.after_hours||0),0)}</b> after-hours</span><span><b>{incidents.length}</b> incident records</span></div></div>
        </div>
      </div>
    </div>

    <div className={styles.truthNote}><b>How to read this:</b> activity detections are not a headcount or unique visitor count. Role-specific office conclusions require confirmed physical camera mapping. Missing monitoring is missing evidence, not zero activity.</div>
  </section>;
}

function OfficePeriodReport({data,days}){
  if(!data?.enabled)return <div className={ui.emptyCard}>No structured office period data is available yet.</div>;
  const s=data.summary||{},p=data.previous_period||{},c=data.comparison||{},daily=data.daily||[];
  const maxActivity=Math.max(1,...daily.map(x=>Number(x.activity_detections||0)));
  const coverage=num(s.avg_coverage_ratio);
  const working=daily.filter(x=>x.working_day),nonWorking=daily.filter(x=>!x.working_day);
  const sum=(rows,key)=>rows.reduce((n,x)=>n+Number(x[key]||0),0);
  return <section className={styles.periodShell}>
    <div className={styles.periodHero}>
      <div><div className={styles.kicker}>Office intelligence</div><h2>{days===7?"7 completed working days":"30-day management review"}</h2><p>{shortDate(data.period?.start_date)} – {shortDate(data.period?.end_date)} · {data.window_type==="completed_working_days"?"working days only":"working and non-working days separated"}</p></div>
      <div className={styles.periodHeroFacts}><span><b>{val(s.observed_days)}</b> observed days</span><span><b>{coverageLabel(coverage)}</b> avg coverage</span></div>
    </div>
    <div className={styles.periodMetrics}>
      <div className={styles.restaurantMetric}><strong>{val(s.incidents_total)}</strong><span>Attention / incident items</span><small>{val(s.critical_total)} critical</small></div>
      <div className={styles.restaurantMetric}><strong>{val(s.after_hours_total)}</strong><span>After-hours observations</span><small>Across the selected period</small></div>
      <div className={styles.restaurantMetric}><strong>{val(s.activity_detections)}</strong><span>Activity detections</span><small>Not unique people</small></div>
      <div className={styles.restaurantMetric}><strong>{coverageLabel(coverage)}</strong><span>Average coverage</span><small>Missing coverage is unknown activity</small></div>
      <div className={styles.restaurantMetric}><strong>{days===30?sum(working,"activity_detections"):val(s.days)}</strong><span>{days===30?"Working-day detections":"Working days reviewed"}</span><small>{days===30?working.length+" working days":"Completed configured working days"}</small></div>
      <div className={styles.restaurantMetric}><strong>{days===30?sum(nonWorking,"activity_detections"):val(p.activity_detections)}</strong><span>{days===30?"Non-working-day detections":"Prior-period detections"}</span><small>{days===30?nonWorking.length+" non-working days":"Comparison baseline"}</small></div>
    </div>

    <div className={styles.compareStrip}>
      <div><span>Attention items vs prior period</span><b>{signed(c.incidents_delta)}</b><small>Current minus previous comparable period</small></div>
      <div><span>Critical attention vs prior</span><b>{signed(c.critical_delta)}</b><small>Current minus previous comparable period</small></div>
      <div><span>After-hours vs prior</span><b>{signed(c.after_hours_delta)}</b><small>Observed after-hours items</small></div>
      <div><span>Coverage change</span><b>{signed(c.coverage_delta_points," pts")}</b><small>Percentage-point change</small></div>
    </div>

    <div className={styles.restaurantPanel}>
      <div className={styles.sectionHead}><div><h3>Office activity trend</h3><p>Activity detections by report day. Counts are not unique people.</p></div></div>
      <div className={styles.trendChart}>{daily.length?daily.map((d,i)=><div className={styles.trendRow} key={(d.date||i)+"-"+i}>
        <span>{shortDate(d.date)}</span><div className={styles.hourTrack}><div className={styles.trendFill} style={{width:String(pctWidth(d.activity_detections,maxActivity))+"%"}}/></div><b>{val(d.activity_detections)}</b>
      </div>):<div className={styles.restaurantEmpty}>No comparable office activity trend is available yet.</div>}</div>
    </div>

    <div className={styles.truthNote}><b>Management boundary:</b> detections are not unique staff/visitors. Compare periods only when coverage is reasonably comparable, and only make area-specific claims where camera mapping is confirmed.</div>
  </section>;
}

function ManagementReading({answer,label}){
  if(!answer)return null;
  return <section className={styles.managementReading}><div className={styles.kicker}>WatchLog management reading</div><h3>{label||"What the period suggests"}</h3><p>{answer}</p></section>;
}

function InsightCards({items=[]}){
  if(!items.length)return null;
  return <div className={styles.findings}>{items.map((f,i)=><article className={styles.finding} key={`${f.title}-${i}`}><div className={styles.findingTop}><span className={`${styles.dot} ${severityClass(f.severity)}`}/><b>{f.title}</b></div>{f.value&&<div className={styles.metricValue} style={{fontSize:20,marginTop:9}}>{f.value}</div>}<p>{f.body}</p></article>)}</div>;
}

function DemandTimeline({points=[]}){
  const rows=(points||[]).filter(x=>x&&x.time);
  const[active,setActive]=useState(0);
  if(!rows.length)return null;
  const selected=rows[Math.min(active,rows.length-1)]||rows[0];
  return <section className={styles.demandSection}>
    <div className={styles.reportSectionHead}><div><span className={styles.sectionEyebrow}>Customer demand</span><h3>How the night moved</h3><p>Relative demand through the observed evening. Tap a point for the management reading.</p></div><span className={styles.chartTruth}>Relative demand · not POS footfall</span></div>
    <div className={styles.demandChartWrap}>
      <div className={styles.demandScale}><span>High</span><span>Low</span></div>
      <div className={styles.demandBars}>
        {rows.map((p,i)=>{
          const level=Math.max(0,Math.min(4,Number(p.level||0)));
          return <button type="button" className={styles.demandPoint+" "+(i===active?styles.demandPointActive:"")} key={p.time+"-"+i} onClick={()=>setActive(i)} aria-pressed={i===active}>
            <span className={styles.demandBarZone}><span className={styles.demandBar} style={{height:String(18+level*19)+"%"}}/></span>
            <span className={styles.demandTime}>{p.time}</span>
          </button>;
        })}
      </div>
    </div>
    <div className={styles.chartReading}><div><b>{selected.label||"Observed pattern"}</b><span>{selected.detail||""}</span></div>{selected.status&&<em>{selected.status}</em>}</div>
  </section>;
}

function ReviewedRestaurantReport({snapshot}){
  const p=snapshot?.payload||{};
  const metrics=p.metrics||[];
  const highlights=p.highlights||[];
  const timeline=p.demand_timeline||[];
  const operations=p.operations||[];
  const security=p.incidents||[];
  const actionItems=p.action_items||[];
  const actions=p.priority_actions||[];
  const coverage=p.coverage||{};
  const visibility=p.visibility_notes||[];
  const summary=p.narrative_summary||p.ai_summary||p.executive_summary||"";
  const primary=actionItems.length?actionItems.slice(0,3):actions.slice(0,3).map((x,i)=>({title:"Action "+(i+1),body:x,priority:i===0?"Priority":"Next"}));
  const more=actionItems.length?actionItems.slice(3):actions.slice(3).map((x,i)=>({title:"Additional action "+(i+4),body:x,priority:"Next"}));
  return <div className={styles.reviewedReport}>
    <section className={styles.reportIntro}>
      <div className={styles.reportIntroTop}><div><span className={styles.sectionEyebrow}>Yesterday · {dateLabel(p.report_date||snapshot?.report_date)}</span><h2>What mattered yesterday</h2></div></div>
      {summary&&<p className={styles.reportLead}>{summary}</p>}
      {highlights.length>0&&<ul className={styles.reportPointers}>{highlights.map((x,i)=><li key={i}>{x}</li>)}</ul>}
    </section>

    {metrics.length>0&&<section className={styles.businessMetrics}>{metrics.map((m,i)=><article className={styles.businessMetric} key={(m.label||"metric")+"-"+i}><strong>{m.value}</strong><span>{m.label}</span>{m.note&&<small>{m.note}</small>}</article>)}</section>}

    <DemandTimeline points={timeline}/>

    {operations.length>0&&<section className={styles.cleanSection}>
      <div className={styles.reportSectionHead}><div><span className={styles.sectionEyebrow}>Operations</span><h3>How the restaurant ran</h3><p>The parts of the operation that mattered to customer experience and revenue opportunity.</p></div></div>
      <div className={styles.operationGrid}>{operations.map((x,i)=><article className={styles.operationCard} key={(x.title||i)+"-"+i}><div className={styles.operationTop}><b>{x.title}</b><span>{x.status}</span></div><p>{x.body}</p>{x.takeaway&&<div className={styles.operationTakeaway}>{x.takeaway}</div>}</article>)}</div>
    </section>}

    {security.length>0&&<section className={styles.cleanSection}>
      <div className={styles.reportSectionHead}><div><span className={styles.sectionEyebrow}>Security & control</span><h3>Anything that needs attention</h3><p>Only security or access matters that are useful to management.</p></div></div>
      <div className={styles.securityList}>{security.map((x,i)=><article className={styles.securityRow} key={(x.title||i)+"-"+i}><span className={styles.securitySignal+" "+severityClass(x.severity)}/><div><b>{x.title}</b><p>{x.body}</p></div>{x.value&&<strong>{x.value}</strong>}</article>)}</div>
    </section>}

    {primary.length>0&&<section className={styles.actionSection}>
      <div className={styles.reportSectionHead}><div><span className={styles.sectionEyebrow}>Next actions</span><h3>What I would follow up</h3><p>Short, practical actions tied directly to yesterday’s operating pattern.</p></div></div>
      <div className={styles.priorityActions}>{primary.map((a,i)=><article className={styles.priorityAction} key={(a.title||i)+"-"+i}><span>{i+1}</span><div><div className={styles.priorityActionTitle}><b>{a.title||"Action"}</b>{a.priority&&<em>{a.priority}</em>}</div><p>{a.body||a.detail||a}</p></div></article>)}</div>
      {more.length>0&&<details className={styles.reportDisclosure}><summary>More improvement actions</summary><div className={styles.disclosureBody}>{more.map((a,i)=><div className={styles.disclosureItem} key={i}><b>{a.title||"Action"}</b><span>{a.body||a.detail||a}</span></div>)}</div></details>}
    </section>}

    <div className={styles.reportDetailsGrid}>
      <details className={styles.reportDisclosure}>
        <summary>How to read the figures</summary>
        <div className={styles.disclosureBody}><div className={styles.disclosureItem}><b>{coverage.status||"Observed period"}</b><span>{coverage.summary||coverage.note||"The figures reflect the observed business period."}</span></div>{coverage.note&&<div className={styles.disclosureItem}><b>Boundary</b><span>{coverage.note}</span></div>}</div>
      </details>
      {visibility.length>0&&<details className={styles.reportDisclosure}>
        <summary>Visibility improvements</summary>
        <div className={styles.disclosureBody}>{visibility.map((x,i)=><div className={styles.disclosureItem} key={i}><b>{x.title}</b><span>{x.body}</span></div>)}</div>
      </details>}
    </div>
  </div>;
}

function EvidenceReport({snapshot}){
  const p=snapshot?.payload||{},metrics=p.metrics||[],incidents=p.incidents||[],coverage=p.coverage||{},cameras=p.camera_coverage||[],insights=p.site_insights||[],actions=p.priority_actions||[];
  const summary=p.ai_summary||p.executive_summary||"No management summary is available for this report.";
  const restaurant=p.site_type==="restaurant"||p.report_profile==="restaurant_business_owner_v1";
  return <div className={styles.report}>
    <section className={styles.hero}>
      <div className={styles.heroTop}><div className={styles.heroTitle}><div className={styles.heroMark}><Mark size={26}/></div><div><div className={styles.kicker}>Yesterday at a glance</div><h2>{p.title||"Report of Yesterday"}</h2></div></div><div className={styles.date}>{dateLabel(p.report_date||snapshot?.report_date)}</div></div>
      <p className={styles.summary}>{summary}</p>
      <div className={styles.meta}><span>{p.site_name||"Main site"}</span><span>Owner’s daily brief</span></div>
    </section>

    {metrics.length>0&&<section className={styles.metrics}>{metrics.map((m,i)=><div className={styles.metric} key={`${m.label}-${i}`}><div className={styles.metricValue}>{m.value}</div><div className={styles.metricLabel}>{m.label}</div>{m.note&&<div className={styles.metricNote}>{m.note}</div>}</div>)}</section>}

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>Security & incidents</h3><p>{restaurant?"Security matters worth the owner’s attention.":"Anything that needed the owner’s attention yesterday."}</p></div></div><InsightCards items={incidents}/></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>{restaurant?"What happened in the business":"What happened yesterday"}</h3><p>{restaurant?"Demand, service flow, operating patterns and opportunities that matter to the owner.":"The most useful things to know about staff presence and office use."}</p></div></div><InsightCards items={insights}/></section>

    <section className={styles.section}><div className={styles.sectionHead}><div><h3>{restaurant?"Business observation window":"Monitoring"}</h3><p>{restaurant?"The period represented by this brief and the limits to how the customer figures should be read.":"What WatchLog could see clearly, and what happened outside that window."}</p></div></div><div className={styles.findings}><article className={styles.finding}><div className={styles.findingTop}><span className={styles.dot}/><b>{coverage.status||"Coverage overview"}</b></div><div className={styles.metricValue} style={{fontSize:20,marginTop:9}}>{coverage.period||"—"}</div><p>{coverage.summary||"Coverage information is not available."}</p></article>{coverage.note&&<article className={styles.finding}><div className={styles.findingTop}><span className={`${styles.dot} ${styles.dotAttention}`}/><b>{restaurant?"How to read the numbers":"What we could not see"}</b></div><p>{coverage.note}</p></article>}</div></section>

    {cameras.length>0&&<section className={styles.section}><div className={styles.sectionHead}><div><h3>Key areas</h3><p>{restaurant?"How the important parts of the restaurant performed yesterday.":"A simple view of the parts of the office that mattered yesterday."}</p></div></div><div className={styles.cameraGrid}>{cameras.map((c,i)=><article className={styles.camera} key={`${c.camera}-${i}`}><div className={styles.cameraTop}><div className={styles.cameraName}>{c.camera}</div><span className={styles.channel}>{c.status||"Covered"}</span></div><div className={styles.cameraStats}><div className={styles.cameraStat}><span>When</span><b>{c.period||"—"}</b></div><div className={styles.cameraStat}><span>What it means</span><b>{c.management_view||"Routine"}</b></div></div><p className={styles.assessment}>{c.assessment}</p></article>)}</div></section>}

    {actions.length>0&&<section className={styles.section}><div className={styles.sectionHead}><div><h3>What needs attention</h3><p>Only the practical things worth following up.</p></div></div><div className={styles.actions}>{actions.map((a,i)=><div className={styles.action} key={i}>{a}</div>)}</div></section>}
  </div>
}

export default function CustomerReports(){
  const r=useReport();
  const views=r.isChaiWalaRestaurant?RESTAURANT_VIEWS:r.isOffice?OFFICE_VIEWS:VIEWS;
  const label=views.find(([k])=>k===r.view)?.[1]||"Report";
  const prompts={
    daily:"Explain today's Chai Wala restaurant operations. Focus on demand, tables, observed service timing, service pressure, coverage and any security attention.",
    yesterday:"Explain yesterday's Chai Wala restaurant report. Focus on demand, tables, observed service timing, service pressure, coverage and any security attention.",
    week:"Explain the last 7 Chai Wala service days. Identify repeated demand, table-utilization and observed service-time patterns, and practical improvements supported by the evidence.",
    monthly:"Explain the last 30 Chai Wala service days. Identify weekly, weekday and hourly patterns, floor/table utilization, observed service-time trends and practical improvements supported by the evidence."
  };
  const officePrompts={
    daily:"Explain today's office report in natural management language. Lead with what needs attention, monitoring confidence and practical improvements. Do not call activity detections unique people.",
    yesterday:"Explain the last completed working-day office report. Lead with security attention, office activity, after-hours exceptions, coverage and practical improvements.",
    week:"Explain the last 7 completed working days for this office. Identify repeated security/activity/coverage patterns and practical improvements.",
    monthly:"Explain the last 30 completed calendar days for this office, separating working and non-working patterns and surfacing repeated improvements."
  };
  const prompt=r.isChaiWalaRestaurant?(prompts[r.view]||prompts.daily):r.isOffice?(officePrompts[r.view]||officePrompts.daily):(r.view==="yesterday"?"Explain the last completed business-day report for this site.":"Explain this management report and tell me the priority action.");

  let reportBody=null;
  if(r.isChaiWalaRestaurant){
    if(r.view==="daily"||r.view==="yesterday"){
      const manualBusinessReport=r.view==="yesterday"&&r.snapshot?.payload?.manual_business_report===true;
      reportBody=manualBusinessReport
        ? <ReviewedRestaurantReport snapshot={r.snapshot}/>
        : <>
            <RestaurantOperations data={r.restaurant} periodLabel={r.view==="daily"?"Today":"Yesterday"}/>
            <ManagementReading answer={r.answer} label={r.view==="daily"?"Today's management reading":"Yesterday's management reading"}/>
            {r.view==="yesterday"&&(r.snapshot?<EvidenceReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>No saved business report is available for this service day yet.</div>)}
          </>;
    }else if(r.view==="week"||r.view==="monthly"){
      const days=r.view==="week"?7:30;
      reportBody=<>
        <RestaurantPeriodReport data={r.restaurantPeriod} days={days}/>
        <ManagementReading answer={r.answer} label={days===7?"What the last 7 days suggest":"What the last 30 days suggest"}/>
      </>;
    }
  }else if(r.isOffice){
    if(r.view==="daily"||r.view==="yesterday"){
      reportBody=<>
        <OfficeDayReport data={r.officeDay} periodLabel={r.view==="daily"?"Today":"Last completed working day"}/>
        <ManagementReading answer={r.answer} label={r.view==="daily"?"Today's management reading":"Last completed working-day reading"}/>
        {r.view==="yesterday"&&(r.snapshot?<EvidenceReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>No saved evidence report is available for this working day yet.</div>)}
      </>;
    }else{
      const days=r.view==="week"?7:30;
      reportBody=<><OfficePeriodReport data={r.officePeriod} days={days}/><ManagementReading answer={r.answer} label={days===7?"What the last 7 working days suggest":"What the last 30 days suggest"}/></>;
    }
  }else{
    reportBody=r.view==="yesterday"?(r.snapshot?<EvidenceReport snapshot={r.snapshot}/>:<div className={ui.emptyCard}>No saved evidence report is available for the last completed business day yet.</div>):<section className="daily-report-card"><div className="daily-report-head"><Mark size={26}/><b>{label} report</b></div><div className="daily-report-body">{r.answer?<RichText text={r.answer}/>:<p style={{margin:0}}>No report is available yet.</p>}</div></section>;
  }

  return <div className="shell"><Nav active="Reports" email={r.email} currentSiteId={r.siteId}/><main className="main"><header className="target-page-head"><div><div className="target-eyebrow">Reports</div><h1>{r.isChaiWalaRestaurant?(r.site?.name||"Chai Wala - Chota Bukhari"):r.isOffice?(r.site?.name||"Office site"):"Management report"}</h1><p>{r.isChaiWalaRestaurant?(r.view==="yesterday"&&r.snapshot?.report_date?dateLabel(r.snapshot.report_date)+" · Demand, customers, service flow, security and next actions.":"Demand, customers, service flow, security and next actions."):r.isOffice?"Security attention, working-day activity, after-hours exceptions, monitoring reliability and practical improvements.":"What happened, what needs attention, and whether WatchLog was watching reliably."}</p></div><div className="target-actions"><a className={ui.secondaryLink} href={withSite("/reports/delivery/",r.siteId)}>Delivery</a></div></header>{r.error&&<div className="err">{r.error}</div>}<div className={ui.tabs}>{views.map(([k,l])=><button key={k} className={ui.tab+" "+(r.view===k?ui.tabActive:"")} onClick={()=>r.setView(k)}>{l}</button>)}</div>{r.busy?<div className={ui.emptyCard}>Preparing the report…</div>:reportBody}<div className={ui.sectionHead}><div><h2>Need more detail?</h2><p>Ask WatchLog about any part of this report.</p></div><a className={ui.primaryLink} href={withSite("/ai/?prompt="+encodeURIComponent(prompt),r.siteId)}>Ask WatchLog</a></div></main></div>;
}
