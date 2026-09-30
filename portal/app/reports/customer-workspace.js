"use client";
import {useEffect,useState} from "react";
import RichText from "../rich-text";
import {Nav} from "../shell";
import {withSite} from "../site-context";
import {supabase,say} from "../../lib/supabase";
import Mark from "../mark";
import ui from "../portal.module.css";
import styles from "./reports.module.css";
import useReport from "./use-report";
import UnifiedRestaurantReport from "./unified-restaurant-report";

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
      <div className={styles.sectionHead}><div><h3>Report reliability & improvement recommendations</h3><p>Camera geometry and image quality determine how trustworthy customer/table analytics can be.</p></div></div>
      <div className={styles.qualityWaiting}><b>Camera-quality insights are not available for this period yet.</b><span>WatchLog only shows reliability guidance when the available coverage supports it. An accuracy percentage is shown only when it can be supported reliably.</span></div>
    </section>;
  }
  return <section className={styles.qualityShell}>
    <div className={styles.sectionHead}><div><h3>Report reliability & improvement recommendations</h3><p>Repeated camera-view problems are surfaced before WatchLog recommends operational changes.</p></div><span className={styles.qualityScored}>Quality reviewed</span></div>
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
    </article>):<div className={styles.qualityClear}>No repeated camera-quality problem has crossed the recommendation threshold during this period.</div>}</div>
    <div className={styles.accuracyNote}><b>Customer-count accuracy:</b> {validation.note||"An accuracy percentage is shown only when the available evidence can support it reliably."}</div>
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
    <div className={styles.restaurantHead}><div><div className={styles.kicker}>Restaurant operations · {periodLabel||"service day"}</div><h2>{data.service_date?"Service day · "+dateLabel(data.service_date):"Restaurant service day"}</h2><p>Chai Wala operating view for the 4 PM–4 AM service window. Values marked estimated or observed are based on visible activity, not POS data.</p></div><span className={styles.readyPill}>{coverage==null?"Coverage available":coverageLabel(coverage)+" coverage"}</span></div>

    <div className={styles.restaurantMetrics}>
      <div className={styles.restaurantMetric}><strong>{val(peakVisible)}</strong><span>Peak visible diners</span><small>Complete dining-floor composite only</small></div>
      <div className={styles.restaurantMetric}><strong>{val(peakOccupied)}</strong><span>Peak occupied tables</span><small>Observed simultaneously occupied tables</small></div>
      <div className={styles.restaurantMetric}><strong>{val(sessions.estimated_covers)}</strong><span>Estimated covers</span><small>Estimate based on visible table activity</small></div>
      <div className={styles.restaurantMetric}><strong>{val(sessions.served_sessions)}</strong><span>Served table sessions</span><small>Sessions where food became visibly present</small></div>
      <div className={styles.restaurantMetric}><strong>{sessions.median_observed_time_to_food_minutes==null?"—":String(sessions.median_observed_time_to_food_minutes)+" min"}</strong><span>Median observed time to food</span><small>Seated to first food visible, not POS timing</small></div>
      <div className={styles.restaurantMetric}><strong>{coverageLabel(coverage)}</strong><span>Analytics coverage</span><small>Share of the service window with enough coverage for these figures</small></div>
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
      <div className={styles.sectionHead}><div><h3>Table utilization</h3><p>Share of the observed period where each tracked table was occupied.</p></div></div>
      <div className={styles.tableColumns}>{activeTables.length?activeTables.map(t=><div className={styles.utilRow} key={t.table_key}>
        <div className={styles.utilTop}><span>{t.label||t.table_key}</span><b>{t.occupancy_pct==null?"—":String(t.occupancy_pct)+"%"}</b></div>
        <div className={styles.utilTrack}><div className={styles.utilFill} style={{width:String(Math.max(0,Math.min(100,Number(t.occupancy_pct||0))))+"%"}}/></div>
        <small>Peak visible party {val(t.peak_party)}</small>
      </div>):<div className={styles.restaurantEmpty}>Table-use figures are not available for this period yet.</div>}</div>
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
      <div><div className={styles.kicker}>Chai Wala · restaurant performance</div><h2>{title}</h2><p>{shortDate(data.period?.start_service_date)} – {shortDate(data.period?.end_service_date)} · service days run 4 PM–4 AM</p></div>
      <div className={styles.periodHeroFacts}>
        <span><b>{observed}</b>/{days} observed days</span>
        <span><b>{coverageLabel(coverage)}</b> avg coverage</span>
      </div>
    </div>

    {observed===0?<div className={styles.restaurantEmpty}>No reliable restaurant business analytics are available for this period yet. WatchLog will not fabricate demand, cover or service-time figures.</div>:<>
      <div className={styles.periodMetrics}>
        <div className={styles.restaurantMetric}><strong>{val(summary.total_estimated_covers)}</strong><span>Estimated covers</span><small>Total estimate based on visible table activity</small></div>
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
        <div className={styles.sectionHead}><div><h3>Weekday pattern</h3><p>Average visible demand and service timing by day of week.</p></div></div>
        <div className={styles.weekdayGrid}>{weekdays.map(w=><div className={styles.weekdayCard} key={w.iso_day}><b>{w.weekday}</b><strong>{val(w.avg_estimated_covers)}</strong><span>avg est. covers</span><small>{val(w.avg_peak_visible_diners)} avg peak diners · {w.avg_daily_median_time_to_food==null?"—":w.avg_daily_median_time_to_food+" min"} time to food</small></div>)}</div>
      </div>}

      <div className={styles.periodGrid}>
        <div className={styles.restaurantPanel}>
          <div className={styles.sectionHead}><div><h3>Floor comparison</h3><p>Customer-area demand by dining floor over this period.</p></div></div>
          <div className={styles.floorCards}>{floors.length?floors.map(f=><div className={styles.floorCard} key={f.camera_id||f.floor}><div className={styles.floorName}>{f.floor}</div><div className={styles.floorStats}><span><b>{val(f.peak_visible_diners)}</b> peak diners</span><span><b>{val(f.avg_visible_diners)}</b> avg visible</span><span><b>{val(f.peak_occupied_tables)}</b> peak occupied tables</span></div><small>{f.observed_days} observed service days</small></div>):<div className={styles.restaurantEmpty}>No floor comparison is available yet.</div>}</div>
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
          <div className={styles.utilList}>{topTables.length?topTables.map(t=><div className={styles.utilRow} key={t.table_key}><div className={styles.utilTop}><span>{t.label||t.table_key}</span><b>{val(t.occupancy_pct,"%")}</b></div><div className={styles.utilTrack}><div className={styles.utilFill} style={{width:String(Math.max(0,Math.min(100,Number(t.occupancy_pct||0))))+"%"}}/></div><small>{t.observed_days} observed days · peak visible party {val(t.peak_party)}</small></div>):<div className={styles.restaurantEmpty}>No table-use figures are available yet.</div>}</div>
        </div>
        <div className={styles.restaurantPanel}>
          <div className={styles.sectionHead}><div><h3>Lower-utilization tables</h3><p>Useful for layout review only when coverage is adequate and table anchors stayed visible.</p></div></div>
          <div className={styles.utilList}>{lowTables.length?lowTables.map(t=><div className={styles.utilRow} key={t.table_key}><div className={styles.utilTop}><span>{t.label||t.table_key}</span><b>{val(t.occupancy_pct,"%")}</b></div><div className={styles.utilTrack}><div className={styles.utilFill} style={{width:String(Math.max(0,Math.min(100,Number(t.occupancy_pct||0))))+"%"}}/></div><small>{t.observed_days} observed days</small></div>):<div className={styles.restaurantEmpty}>No comparable table utilization is available yet.</div>}</div>
        </div>
      </div>
    </>}

    <div className={styles.truthNote}><b>Management boundary:</b> these figures describe visible activity during covered periods. Visible diners are not unique footfall; covers and sessions are estimates; service timing is visually observed, not POS order-to-serve. Period-to-period changes should only be acted on when coverage is sufficiently comparable.</div>
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


function SavedReportHistory({windowData,siteId,mode="period",excludeDate=""}){
  const rows=(windowData?.saved_reports||[]).filter(x=>x?.service_date&&String(x.service_date)!==String(excludeDate||""));
  if(!rows.length)return null;
  const shown=mode==="latest"?rows.slice(0,1):rows;
  return <section className={styles.section}>
    <div className={styles.sectionHead}><div>
      <h3>{mode==="latest"?"Most recent completed report":"Completed daily reports in this period"}</h3>
      <p>{mode==="latest"?"There is no saved report for the selected day. The most recent completed report is shown below.":"Open any completed daily report without losing the 7-day or 30-day management view."}</p>
    </div></div>
    <div className={styles.findings}>{shown.map((x,i)=>{
      const d=String(x.service_date||"");
      const highlights=(x.highlights||[]).slice(0,3);
      return <article className={styles.finding} key={(x.report_id||d||i)+"-"+i}>
        <div className={styles.findingTop}><span className={styles.dot+" "+styles.dotGood}/><b>{dateLabel(d)}</b></div>
        <p>{x.summary||"A completed management report is available for this service day."}</p>
        {highlights.length>0&&<div>{highlights.map((h,j)=><p key={j}>• {typeof h==="string"?h:(h?.body||h?.title||"")}</p>)}</div>}
        <a className={ui.secondaryLink} href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}>Open report</a>
      </article>;
    })}</div>
  </section>;
}

function ReportIcon({kind}){
  if(kind==="shield")return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5.5 5.7v5.2c0 4.4 2.5 7.9 6.5 10.1 4-2.2 6.5-5.7 6.5-10.1V5.7L12 3Z" fill="none" stroke="currentColor" strokeWidth="1.7"/><path d="m9.1 12 1.8 1.8 4-4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/></svg>;
  if(kind==="clock")return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" strokeWidth="1.7"/><path d="M12 7.7v4.7l3.1 1.8" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>;
  if(kind==="table")return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 8.5h14M7 8.5v8M17 8.5v8M4 16.5h16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>;
  if(kind==="users")return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="9" r="3" fill="none" stroke="currentColor" strokeWidth="1.7"/><path d="M3.8 18c.7-3 2.4-4.5 5.2-4.5S13.5 15 14.2 18M15 7.3a2.7 2.7 0 0 1 0 5.3M16.2 13.9c2.1.5 3.4 1.9 4 4.1" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>;
  if(kind==="trend")return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17.5 9 12l3.2 3.2L20 7.5M15.5 7.5H20V12" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" strokeWidth="1.7"/><path d="M12 8.2v4.2M12 16.2h.01" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"/></svg>;
}

function DemandAreaChart({points=[]}){
  const rows=(points||[]).filter(x=>x&&x.time);
  const[active,setActive]=useState(0);
  if(!rows.length)return null;
  const W=760,H=244,left=34,right=18,top=22,bottom=42,plotW=W-left-right,plotH=H-top-bottom;
  const x=i=>left+(rows.length===1?plotW/2:(plotW*i/(rows.length-1)));
  const y=level=>top+plotH-(Math.max(0,Math.min(4,Number(level||0)))/4)*plotH;
  const pts=rows.map((p,i)=>[x(i),y(p.level)]);
  const line=pts.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
  const area=line+" L "+x(rows.length-1).toFixed(1)+" "+(top+plotH)+" L "+x(0).toFixed(1)+" "+(top+plotH)+" Z";
  const selected=rows[Math.min(active,rows.length-1)]||rows[0];
  return <div className={styles.demandViz}>
    <div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Business analytics</span><h3>Demand through the night</h3><p>Relative dining demand across the observed evening.</p></div><span className={styles.dataBoundary}>Relative demand</span></div>
    <div className={styles.chartFrame}>
      <svg className={styles.areaChart} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Relative restaurant demand over the observed evening">
        <defs><linearGradient id="chaiDemandFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".22"/><stop offset="100%" stopColor="currentColor" stopOpacity=".02"/></linearGradient></defs>
        {[0,1,2,3,4].map(i=>{const gy=top+plotH-(i/4)*plotH;return <line key={i} x1={left} x2={W-right} y1={gy} y2={gy} className={styles.chartGrid}/>})}
        <path d={area} className={styles.chartArea}/>
        <path d={line} className={styles.chartLine}/>
        {pts.map((p,i)=><g key={rows[i].time} role="button" tabIndex="0" aria-label={rows[i].time+": "+(rows[i].label||"demand point")} onClick={()=>setActive(i)} onKeyDown={e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();setActive(i)}}} className={i===active?styles.chartPointActive:styles.chartPoint}>
          <circle cx={p[0]} cy={p[1]} r={i===active?8:6} className={styles.chartPointHalo}/>
          <circle cx={p[0]} cy={p[1]} r={i===active?4.5:3.5} className={styles.chartPointDot}/>
        </g>)}
        {rows.map((p,i)=><text key={"label-"+p.time} x={x(i)} y={H-15} textAnchor={i===0?"start":i===rows.length-1?"end":"middle"} className={styles.chartAxisLabel}>{p.time}</text>)}
      </svg>
      <div className={styles.chartLegend}><span><i className={styles.legendHigh}/>Higher demand</span><span><i className={styles.legendLow}/>Lower demand</span></div>
    </div>
    <div className={styles.chartInspector}><div><span>{selected.time}</span><b>{selected.label||"Observed pattern"}</b><p>{selected.detail||""}</p></div>{selected.status&&<em>{selected.status}</em>}</div>
  </div>;
}

function KpiStrip({metrics=[]}){
  const icons=["users","table","trend","clock"];
  return <div className={styles.kpiStrip}>{metrics.slice(0,4).map((m,i)=><div className={styles.kpiCell} key={(m.label||"metric")+"-"+i}><span className={styles.kpiIcon}><ReportIcon kind={icons[i]}/></span><div><strong>{m.value}</strong><b>{m.label}</b>{m.note&&<small>{m.note}</small>}</div></div>)}</div>;
}

function SecuritySnapshot({items=[]}){
  const critical=items.filter(x=>x.severity==="critical").length;
  const attention=items.filter(x=>x.severity==="attention").length;
  const status=critical?"Critical attention":attention?"Attention item":"No critical incident observed";
  return <section className={styles.securitySnapshot}>
    <div className={styles.securitySnapshotHead}><span className={styles.securityIcon}><ReportIcon kind="shield"/></span><div><span className={styles.panelEyebrow}>Security analytics</span><h3>{status}</h3></div></div>
    <div className={styles.securitySummaryList}>{items.slice(0,3).map((x,i)=><div className={styles.securitySummaryRow} key={(x.title||i)+"-"+i}><span className={styles.securityDot+" "+severityClass(x.severity)}/><div><b>{x.title}</b><p>{x.body}</p></div>{x.value&&<strong>{x.value}</strong>}</div>)}</div>
  </section>;
}

function OperationRows({items=[]}){
  return <div className={styles.operationRows}>{items.map((x,i)=><div className={styles.operationRow} key={(x.title||i)+"-"+i}><div className={styles.operationIndex}>{String(i+1).padStart(2,"0")}</div><div className={styles.operationMain}><div className={styles.operationRowHead}><b>{x.title}</b><span>{x.status}</span></div><p>{x.body}</p>{x.takeaway&&<small>{x.takeaway}</small>}</div></div>)}</div>;
}

const RECOMMENDATION_RESPONSES=[
  ["accepted","We'll do this"],
  ["need_help","Need help"],
  ["not_now","Not now"],
  ["not_relevant","Not relevant"],
];
const RECOMMENDATION_TEAM_STATUS={
  new:"Sent to WatchLog",
  in_progress:"WatchLog is following up",
  resolved:"Follow-up resolved",
  closed:"Closed",
};

function RecommendationFeedback({action,existing,siteId,reportId,onSaved}){
  const[choice,setChoice]=useState(existing?.response_code||"");
  const[note,setNote]=useState(existing?.client_note||"");
  const[showNote,setShowNote]=useState(Boolean(existing?.client_note));
  const[busy,setBusy]=useState(false);
  const[message,setMessage]=useState("");

  useEffect(()=>{
    setChoice(existing?.response_code||"");
    setNote(existing?.client_note||"");
    setShowNote(Boolean(existing?.client_note));
  },[existing?.response_code,existing?.client_note]);

  if(!action?.id||!siteId||!reportId)return null;

  const discussPrompt=`I want to discuss this recommendation from my WatchLog report: "${action.title}". Recommendation: ${action.body}. Explain why it matters, what practical options I have, and help me decide what to do next.`;

  async function save(){
    if(!choice){setMessage("Choose a response first.");return;}
    setBusy(true);setMessage("");
    const{data,error}=await supabase().rpc("wl_save_recommendation_feedback",{
      p_site_id:siteId,
      p_report_id:reportId,
      p_recommendation_id:action.id,
      p_response_code:choice,
      p_client_note:note.trim()||null,
    });
    setBusy(false);
    if(error){setMessage(say(error)||"Could not save your response.");return;}
    onSaved?.(data);
    setMessage("Response saved. WatchLog's team can now follow it up.");
  }

  return <div className={styles.recommendationFeedback}>
    <div className={styles.feedbackPrompt}>What do you think about this recommendation?</div>
    <div className={styles.feedbackChoices}>{RECOMMENDATION_RESPONSES.map(([code,label])=><button type="button" key={code} className={choice===code?styles.feedbackChoiceActive:""} onClick={()=>{setChoice(code);setMessage("");if(code==="need_help")setShowNote(true);}}>{label}</button>)}</div>
    <div className={styles.feedbackTools}>
      <button type="button" className={styles.feedbackTextButton} onClick={()=>setShowNote(v=>!v)}>{showNote?"Hide comment":"Add comment"}</button>
      <a className={styles.feedbackDiscuss} href={withSite("/ai/?prompt="+encodeURIComponent(discussPrompt),siteId)}>Discuss with WatchLog</a>
      {existing?.response_code&&<span className={styles.feedbackSaved}>Saved · {RECOMMENDATION_RESPONSES.find(x=>x[0]===existing.response_code)?.[1]||existing.response_code}{existing.team_status?" · "+(RECOMMENDATION_TEAM_STATUS[existing.team_status]||existing.team_status):""}</span>}
    </div>
    {showNote&&<textarea className={styles.feedbackNote} maxLength={2000} value={note} onChange={e=>setNote(e.target.value)} placeholder="Add context for the WatchLog team — what you agree with, what should change, or where you need help."/>}
    <div className={styles.feedbackFooter}><button type="button" className={styles.feedbackSave} disabled={busy||!choice} onClick={save}>{busy?"Saving…":existing?.response_code?"Update response":"Send feedback"}</button>{message&&<span className={styles.feedbackMessage}>{message}</span>}</div>
  </div>;
}

function RecommendationRow({action,index,existing,siteId,reportId,onSaved,compact=false}){
  return <div className={compact?styles.actionRowCompact:styles.actionRowV3}>
    <span>{index+1}</span>
    <div>
      <div className={styles.actionTitleV3}><b>{action.title||"Action"}</b>{action.priority&&<em>{action.priority}</em>}</div>
      <p>{action.body||action.detail||action}</p>
      <RecommendationFeedback action={action} existing={existing} siteId={siteId} reportId={reportId} onSaved={onSaved}/>
    </div>
  </div>;
}

function PriorityActions({items=[],fallback=[],siteId,reportId}){
  const rows=items.length?items:fallback.map((x,i)=>({id:null,title:"Action "+(i+1),body:x,priority:i===0?"Priority":"Next"}));
  const[feedback,setFeedback]=useState({});
  const[loadError,setLoadError]=useState("");

  useEffect(()=>{
    let live=true;
    if(!siteId||!reportId)return()=>{live=false};
    (async()=>{
      const{data,error}=await supabase().rpc("wl_my_recommendation_feedback",{p_site_id:siteId,p_report_id:reportId});
      if(!live)return;
      if(error){setLoadError(say(error)||"Could not load saved responses.");return;}
      const map={};
      for(const item of data?.items||[])map[item.recommendation_id]=item;
      setFeedback(map);setLoadError("");
    })();
    return()=>{live=false};
  },[siteId,reportId]);

  function saved(item){if(!item?.recommendation_id)return;setFeedback(v=>({...v,[item.recommendation_id]:item}));}

  if(!rows.length)return null;
  return <section className={styles.actionPanelV3}><div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Next actions</span><h3>What management should follow up</h3><p>Respond to any recommendation so the WatchLog team can incorporate your feedback and help with implementation.</p></div></div>
    {loadError&&<div className={styles.feedbackLoadError}>{loadError}</div>}
    <div className={styles.actionRowsV3}>{rows.slice(0,3).map((a,i)=><RecommendationRow key={(a.id||a.title||i)+"-"+i} action={a} index={i} existing={feedback[a.id]} siteId={siteId} reportId={a.report_id||reportId} onSaved={saved}/>)}</div>
    {rows.length>3&&<details className={styles.moreActions}><summary>Show {rows.length-3} more improvements</summary><div className={styles.moreActionRows}>{rows.slice(3).map((a,i)=><RecommendationRow compact key={(a.id||a.title||i)+"-"+i} action={a} index={i+3} existing={feedback[a.id]} siteId={siteId} reportId={a.report_id||reportId} onSaved={saved}/>)}</div></details>}
  </section>;
}

function ReviewedRestaurantReport({snapshot,siteId}){
  const p=snapshot?.payload||{};
  const[mode,setMode]=useState("overview");
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
  return <div className={styles.saasReport}>
    <div className={styles.reportControlBar}>
      <div className={styles.reportDateLine}><span>{dateLabel(p.report_date||snapshot?.report_date)}</span><i/> <span>Yesterday</span></div>
      <div className={styles.reportModeTabs} role="tablist" aria-label="Report sections">
        {[["overview","Overview"],["business","Business"],["security","Security"]].map(([k,l])=><button key={k} type="button" role="tab" aria-selected={mode===k} className={mode===k?styles.reportModeActive:""} onClick={()=>setMode(k)}>{l}</button>)}
      </div>
    </div>

    {mode==="overview"&&<>
      <section className={styles.executiveSummary}>
        <div className={styles.executiveCopy}><span className={styles.panelEyebrow}>Executive summary</span><h2>Yesterday in one view</h2>{summary&&<p>{summary}</p>}</div>
        {highlights.length>0&&<div className={styles.executivePointers}>{highlights.slice(0,3).map((x,i)=><div key={i}><span>{i+1}</span><p>{x}</p></div>)}</div>}
      </section>
      <KpiStrip metrics={metrics}/>
      <div className={styles.analyticsSplit}><DemandAreaChart points={timeline}/><SecuritySnapshot items={security}/></div>
      <PriorityActions items={actionItems} fallback={actions} siteId={siteId} reportId={snapshot?.report_id}/>
      <div className={styles.footerDetails}>
        <details><summary>How to read these figures</summary><p><b>{coverage.status||"Observed period"}.</b> {coverage.summary||""} {coverage.note||""}</p></details>
        {visibility.length>0&&<details><summary>Visibility improvements</summary>{visibility.map((x,i)=><p key={i}><b>{x.title}:</b> {x.body}</p>)}</details>}
      </div>
    </>}

    {mode==="business"&&<>
      <section className={styles.sectionIntroV3}><span className={styles.panelEyebrow}>Business analytics</span><h2>Customers, tables and operating flow</h2><p>What the observed evening says about demand, service channels and closing discipline.</p></section>
      <KpiStrip metrics={metrics}/>
      <DemandAreaChart points={timeline}/>
      <section className={styles.operationsPanelV3}><div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Operations</span><h3>How each part of the restaurant performed</h3><p>Grouped by business function instead of by camera.</p></div></div><OperationRows items={operations}/></section>
      <PriorityActions items={actionItems} fallback={actions} siteId={siteId} reportId={snapshot?.report_id}/>
    </>}

    {mode==="security"&&<>
      <section className={styles.sectionIntroV3}><span className={styles.panelEyebrow}>Security analytics</span><h2>Security posture and access control</h2><p>Exception-based security: what was normal, what was clear, and what needs attention.</p></section>
      <div className={styles.securityFocusGrid}>
        <SecuritySnapshot items={security}/>
        <section className={styles.securityContextPanel}><div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Management context</span><h3>What the security picture means</h3></div></div><p>Office areas remained visibly undisturbed and rear access was mostly routine. The main security-related improvement is operational: keep the rear route clear during close-down so service access does not become constrained.</p></section>
      </div>
      <section className={styles.securityEventPanel}><div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Events & exceptions</span><h3>Security timeline</h3><p>Only events with management value are shown.</p></div></div><div className={styles.securityEventList}>{security.map((x,i)=><div className={styles.securityEventRow} key={(x.title||i)+"-"+i}><span className={styles.securityDot+" "+severityClass(x.severity)}/><div><b>{x.title}</b><p>{x.body}</p></div>{x.value&&<strong>{x.value}</strong>}</div>)}</div></section>
      {visibility.length>0&&<section className={styles.visibilityPanel}><div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Coverage quality</span><h3>Improvements that strengthen future analytics</h3></div></div><div className={styles.visibilityRows}>{visibility.map((x,i)=><div key={i}><b>{x.title}</b><p>{x.body}</p></div>)}</div></section>}
    </>}
  </div>;
}

function EvidenceReport({snapshot,siteId}){
  const p=snapshot?.payload||{},metrics=p.metrics||[],incidents=p.incidents||[],coverage=p.coverage||{},cameras=p.camera_coverage||[],insights=p.site_insights||[],actionItems=p.action_items||[],actions=p.priority_actions||[];
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

    {(actionItems.length>0||actions.length>0)&&<PriorityActions items={actionItems} fallback={actions} siteId={siteId} reportId={snapshot?.report_id}/>}
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
    reportBody=<UnifiedRestaurantReport
      view={r.view}
      day={r.restaurant}
      securityDay={r.restaurantSecurity}
      period={r.restaurantPeriod}
      windowData={r.reportWindow}
      snapshot={r.snapshot}
      siteId={r.siteId}
      requestedReportDate={r.requestedReportDate}
      renderActions={(items,reportId)=><PriorityActions items={items} siteId={r.siteId} reportId={reportId}/>}
    />;
  }else if(r.isOffice){
    if(r.view==="daily"||r.view==="yesterday"){
      reportBody=<>
        <OfficeDayReport data={r.officeDay} periodLabel={r.view==="daily"?"Today":"Last completed working day"}/>
        <ManagementReading answer={r.answer} label={r.view==="daily"?"Today's management reading":"Last completed working-day reading"}/>
        {r.view==="yesterday"&&(r.snapshot?<EvidenceReport snapshot={r.snapshot} siteId={r.siteId}/>:<>
          <div className={ui.emptyCard}>No saved management report is available for this working day yet.</div>
          <SavedReportHistory windowData={r.reportWindow} siteId={r.siteId} mode="latest"/>
        </>)}
      </>;
    }else{
      const days=r.view==="week"?7:30;
      reportBody=<><OfficePeriodReport data={r.officePeriod} days={days}/><ManagementReading answer={r.answer} label={days===7?"What the last 7 working days suggest":"What the last 30 days suggest"}/><SavedReportHistory windowData={r.reportWindow} siteId={r.siteId}/></>;
    }
  }else{
    reportBody=r.view==="yesterday"?(r.snapshot?<EvidenceReport snapshot={r.snapshot} siteId={r.siteId}/>:<div className={ui.emptyCard}>No saved evidence report is available for the last completed business day yet.</div>):<section className="daily-report-card"><div className="daily-report-head"><Mark size={26}/><b>{label} report</b></div><div className="daily-report-body">{r.answer?<RichText text={r.answer}/>:<p style={{margin:0}}>No report is available yet.</p>}</div></section>;
  }

  return <div className="shell"><Nav active="Reports" email={r.email} currentSiteId={r.siteId}/><main className="main"><header className="target-page-head"><div><div className="target-eyebrow">Reports</div><h1>{r.isChaiWalaRestaurant?(r.site?.name||"Chai Wala - Chota Bukhari"):r.isOffice?(r.site?.name||"Office site"):"Management report"}</h1><p>{r.isChaiWalaRestaurant?(r.view==="daily"?"Live management view for the current service day.":r.view==="yesterday"?(r.snapshot?.report_date?dateLabel(r.snapshot.report_date)+" · Completed business and security report.":"Completed business and security report."):(r.view==="week"?"Weekly business and security review.":"Monthly business and security review.")):r.isOffice?"Security attention, working-day activity, after-hours exceptions, monitoring reliability and practical improvements.":"What happened, what needs attention, and whether WatchLog was watching reliably."}</p></div><div className="target-actions"><a className={ui.secondaryLink} href={withSite("/reports/delivery/",r.siteId)}>Delivery</a></div></header>{r.error&&<div className="err">{r.error}</div>}<div className={ui.tabs}>{views.map(([k,l])=><button key={k} className={ui.tab+" "+(r.view===k?ui.tabActive:"")} onClick={()=>r.setView(k)}>{l}</button>)}</div>{r.busy?<div className={ui.emptyCard}>Preparing the report…</div>:reportBody}<div className={ui.sectionHead}><div><h2>Need more detail?</h2><p>Ask WatchLog about any part of this report.</p></div><a className={ui.primaryLink} href={withSite("/ai/?prompt="+encodeURIComponent(prompt),r.siteId)}>Ask WatchLog</a></div></main></div>;
}
