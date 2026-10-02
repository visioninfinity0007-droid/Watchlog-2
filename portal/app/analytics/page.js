"use client";

import {useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {Nav,requireTenant} from "../shell";
import {rememberSite,selectedSiteId,withSite} from "../site-context";
import ui from "../portal.module.css";
import styles from "./customer-overview.module.css";

function n(v){return Number(v||0).toLocaleString()}
function label(v){return String(v||"Activity").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase())}
function pct(v){if(v===null||v===undefined||v==="")return null;const x=Number(v);return Number.isFinite(x)?Math.round(Math.max(0,Math.min(1,x))*100):null}
function dateLabel(v){if(!v)return"Latest completed service day";try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(new Date(v+"T00:00:00Z"))}catch{return v}}
function metricValue(metric){if(metric===null||metric===undefined)return"—";if(typeof metric==="object"&&metric.value!==undefined)return String(metric.value);return String(metric)}
function numeric(v){if(v===null||v===undefined||v==="")return null;const x=Number(v);return Number.isFinite(x)?x:null}
function compactNumber(v){const x=numeric(v);if(x===null)return"—";return Number.isInteger(x)?String(x):x.toFixed(1)}
function restaurantPeriodView(windowData){
  const period=windowData?.structured_restaurant_metrics;
  if(!period||period.enabled!==true)return null;
  const summary=period.summary||{},previous=period.previous_period||{},comparison=period.comparison||{};
  const expected=Math.max(1,Number(summary.expected_service_days||period.days||7));
  const observed=Math.max(0,Number(summary.observed_service_days||0));
  const previousObserved=Math.max(0,Number(previous.observed_service_days||0));
  const minimum=Math.min(4,expected);
  const ready=observed>=minimum&&previousObserved>=minimum;
  const changes=[];
  const covers=numeric(comparison.estimated_covers_pct);
  if(covers!==null&&Math.abs(covers)>=5)changes.push("Estimated covers were "+compactNumber(Math.abs(covers))+"% "+(covers>0?"higher":"lower")+" than the previous "+expected+"-day period.");
  const service=numeric(comparison.median_time_to_food_delta_minutes);
  if(service!==null&&Math.abs(service)>=1)changes.push("Median observed time to food was "+compactNumber(Math.abs(service))+" minute"+(Math.abs(service)===1?"":"s")+" "+(service<0?"faster":"slower")+" than the previous period.");
  const coverageDelta=numeric(comparison.coverage_delta_points);
  if(coverageDelta!==null&&Math.abs(coverageDelta)>=5)changes.push("Monitoring coverage was "+compactNumber(Math.abs(coverageDelta))+" percentage points "+(coverageDelta>0?"higher":"lower")+" than the previous period.");
  if(ready&&!changes.length)changes.push("No material change stands out in the supported 7-day comparison.");
  return{
    expected,observed,previousObserved,ready,changes:changes.slice(0,3),
    avgCovers:numeric(summary.avg_estimated_covers_per_observed_day),
    medianFood:numeric(summary.median_observed_time_to_food_minutes),
    coverage:pct(summary.avg_coverage_ratio),
    busiestDay:summary.busiest_day||null
  };
}

function RestaurantInsights({siteId,site,date,day,snapshot,windowData}){
  const payload=snapshot?.payload||{};
  const manual=payload?.manual_business_report===true;
  const q=day?.data_quality||{};
  const sessions=day?.sessions||{};
  const hourly=(day?.hourly||[]).filter(x=>Number(x.samples||0)>0);
  const peakVisible=hourly.reduce((m,x)=>Math.max(m,Number(x.peak_visible_customers||0)),0);
  const peakTables=hourly.reduce((m,x)=>Math.max(m,Number(x.peak_occupied_tables||0)),0);
  const businessReady=manual||Number(q.camera_observations||0)>0||Number(q.table_observations||0)>0;
  const coverage=pct(snapshot?.payload?.coverage?.coverage_ratio);
  const saved=(windowData?.saved_reports||[]).slice(0,4);
  const manualMetrics=(payload.metrics||[]).slice(0,4);
  const highlights=(payload.highlights||[]).slice(0,4);
  const period=restaurantPeriodView(windowData);

  const liveMetrics=[
    {value:peakVisible||"—",label:"Peak visible diners",note:"Concurrent visible diners, not unique footfall"},
    {value:peakTables||"—",label:"Peak occupied tables",note:"Highest simultaneous table use"},
    {value:sessions.estimated_covers??"—",label:"Estimated covers",note:"Camera-based estimate from supported table activity"},
    {value:sessions.median_observed_time_to_food_minutes==null?"—":sessions.median_observed_time_to_food_minutes+" min",label:"Observed time to food",note:"Visible seating to first food seen"}
  ];
  const metrics=manual&&manualMetrics.length?manualMetrics:liveMetrics;

  return <>
    <section className={styles.restaurantHero}>
      <div>
        <span className={styles.restaurantEyebrow}>{site?.name||"Restaurant"} · completed service day</span>
        <h2>{dateLabel(date)}</h2>
        <p>{businessReady?"A management view of visible dining demand, table use, service timing and supported operating observations.":"A completed report exists, but WatchLog is not presenting unsupported business figures for this service day."}</p>
      </div>
      <div className={styles.coverageBlock}>
        <span>Monitoring coverage</span>
        <strong>{coverage===null?"Not verified":coverage+"%"}</strong>
        <small>{coverage===null?"No verified coverage figure is available for this completed service day.":coverage===100?"The configured service window is fully represented.":"Some of the configured service window remains unverified."}</small>
      </div>
    </section>

    {businessReady&&<section className={styles.restaurantMetrics}>
      {metrics.map((m,i)=><div key={(m.label||i)+"-"+i}><strong>{metricValue(m)}</strong><span>{m.label||"Business metric"}</span>{m.note&&<small>{m.note}</small>}</div>)}
    </section>}

    <section className={styles.restaurantGrid}>
      <div className={styles.restaurantPanel}>
        <div className={styles.sectionHead}><div><h2>What management should know</h2><p>Only conclusions supported by the completed service day are shown.</p></div><a href={withSite("/reports/?view=yesterday"+(date?"&date="+encodeURIComponent(date):""),siteId)}>Full report</a></div>
        {highlights.length?<div className={styles.insightList}>{highlights.map((x,i)=><div key={i}><span>{String(i+1).padStart(2,"0")}</span><p>{typeof x==="string"?x:(x.title||x.body||"Management observation")}</p></div>)}</div>:<div className={styles.empty}>No completed management highlights are available for this service day yet. Open the report for the supported coverage and evidence.</div>}
      </div>

      <aside className={styles.boundaryPanel}>
        <span>Measurement boundary</span>
        <h3>What these cameras can support</h3>
        <p>Dining-floor views can support visible diners, table use, estimated table sessions/covers and observed time to food when coverage is adequate.</p>
        <ul>
          <li>Do not read visible diners as unique customer footfall.</li>
          <li>Do not infer sales, revenue or transaction count.</li>
          <li>Unverified time is not zero activity.</li>
        </ul>
      </aside>
    </section>

    {period&&<section className={styles.periodPanel}>
      <div className={styles.sectionHead}><div><span className={styles.periodEyebrow}>7-day pattern</span><h2>What changed across recent service days</h2><p>{period.ready?"Compared with the previous 7 service days using only supported completed-period data.":"WatchLog is building a comparison from completed service days; missing periods are not treated as zero activity."}</p></div><a href={withSite("/reports/?view=week",siteId)}>Open 7-day report</a></div>
      <div className={styles.periodMetrics}>
        <div><strong>{period.avgCovers===null?"—":compactNumber(period.avgCovers)}</strong><span>Avg estimated covers</span><small>{period.observed+" of "+period.expected+" service days observed"}</small></div>
        <div><strong>{period.medianFood===null?"—":compactNumber(period.medianFood)+" min"}</strong><span>Median observed time to food</span><small>Across supported table sessions</small></div>
        <div><strong>{period.coverage===null?"—":period.coverage+"%"}</strong><span>Avg monitoring coverage</span><small>Missing time is not zero activity</small></div>
        <div><strong>{period.busiestDay?.service_date?dateLabel(String(period.busiestDay.service_date)):"—"}</strong><span>Busiest observed service day</span><small>{period.busiestDay?.estimated_covers!=null?compactNumber(period.busiestDay.estimated_covers)+" estimated covers":"Not enough supported data"}</small></div>
      </div>
      {period.ready?<div className={styles.periodChanges}>{period.changes.map((item,i)=><div key={item}><span>{String(i+1).padStart(2,"0")}</span><p>{item}</p></div>)}</div>:<div className={styles.periodPending}><h3>Reliable comparison not ready yet</h3><p>Current period: {period.observed} of {period.expected} observed service days. Previous period: {period.previousObserved} of {period.expected}. WatchLog will show directional changes after both periods have enough observed days.</p></div>}
    </section>}

    <section className={styles.section}>
      <div className={styles.sectionHead}><div><h2>Completed service days</h2><p>Use completed daily reports before treating a multi-day pattern as a trend.</p></div><a href={withSite("/reports/?view=week",siteId)}>Open 7-day view</a></div>
      <div className={styles.savedDays}>{saved.length?saved.map((r,i)=>{const d=String(r.service_date||"");return <a key={r.report_id||d||i} href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}><div><b>{dateLabel(d)}</b><small>{r.summary||((r.highlights||[])[0])||"Completed management report"}</small></div><span>Open →</span></a>}):<div className={styles.empty}>No completed daily reports are available in this period yet.</div>}</div>
    </section>

    <div className={styles.explain}><div><h3>Need a specific answer?</h3><p>Ask WatchLog about the completed service day, supported business activity or monitoring coverage.</p></div><a className={ui.primaryLink} href={withSite("/ai/?prompt="+encodeURIComponent("What should management know from the latest completed service day, and what is still unverified?"),siteId)}>Ask WatchLog</a></div>
  </>;
}

function GenericInsights({siteId,data}){
  const s=data?.summary||{},rules=data?.by_rule||[],after=Number(s.after_hours||0),visitors=Number(s.visitor_in||0),vehicles=Number(s.vehicles_in||0),areas=Number(s.zone_entries||0),total=visitors+vehicles+areas;
  const configured=rules.length>0;
  const measured=after>0||total>0;
  if(!configured&&!measured)return <>
    <section className={styles.summary}><div className={styles.summaryTop}><span className={styles.summaryBadge}>Setup needed</span></div><h2>Activity insights are not configured yet</h2><p>Choose what WatchLog should measure before this page can summarize visitor, vehicle, area or after-hours activity.</p></section>
    <section className={styles.configure}><div><span>Insights setup</span><h2>Choose what to measure</h2><p>Add Activity Rules for the patterns that matter at this site. Until then, WatchLog will not turn missing measurements into zero activity.</p></div><a className={styles.configureAction} href={withSite("/analytics/studio/",siteId)}>Set up activity insights</a></section>
  </>;
  const summaryTitle=after?"After-hours activity needs review":total?"Activity measured in this period":"No measured activity in this period";
  const summaryText=after?String(after)+" activity "+(after===1?"item was":"items were")+" recorded outside normal hours.":total?n(visitors)+" visitor entries, "+n(vehicles)+" vehicle entries and "+n(areas)+" area entries were measured.":"Configured activity rules recorded no visitor, vehicle, area or after-hours activity in this time range.";
  return <>
    <section className={styles.summary}><div className={styles.summaryTop}><span className={styles.summaryBadge+" "+(after?styles.warn:total?styles.ok:"")}>{after?"Needs review":total?"Measured":"Summary"}</span></div><h2>{summaryTitle}</h2><p>{summaryText}</p></section>
    <section className={styles.metrics}><div className={styles.metric}><strong>{n(visitors)}</strong><span>Visitor entries</span><small>People crossing configured entry rules</small></div><div className={styles.metric}><strong>{n(vehicles)}</strong><span>Vehicle entries</span><small>Vehicles crossing configured entry rules</small></div><div className={styles.metric}><strong>{n(areas)}</strong><span>Area entries</span><small>Configured monitored-zone entries</small></div><div className={styles.metric}><strong>{n(after)}</strong><span>After-hours activity</span><small>Activity outside configured hours</small></div></section>
    <section className={styles.section}><div className={styles.sectionHead}><div><h2>Most active rules</h2><p>The monitoring rules that recorded the most activity in this period.</p></div></div><div className={styles.panel}>{rules.length?<div className={styles.ruleList}>{rules.slice(0,8).map(r=><div className={styles.rule} key={r.rule_id}><span className={styles.count}>{n(r.count)}</span><div><b>{r.name||label(r.analytic_key||r.rule_type)}</b><small>{r.camera||"Camera"}</small></div><span className={styles.ruleType}>{label(r.rule_type||r.analytic_key)}</span></div>)}</div>:<div className={styles.empty}>No activity insights yet. Add an Activity Rule when you want WatchLog to measure a specific pattern.</div>}</div></section>
    <div className={styles.explain}><div><h3>Need an explanation?</h3><p>Ask WatchLog what changed or what looks unusual in this period.</p></div><a className={ui.primaryLink} href={withSite("/ai/?prompt="+encodeURIComponent("What changed in this activity period and what looks unusual?"),siteId)}>Ask WatchLog</a></div>
  </>;
}

export default function Analytics(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[days,setDays]=useState(7);
  const[data,setData]=useState(null);
  const[restaurantConfig,setRestaurantConfig]=useState(null);
  const[restaurantDay,setRestaurantDay]=useState(null);
  const[snapshot,setSnapshot]=useState(null);
  const[windowData,setWindowData]=useState(null);
  const[reportDate,setReportDate]=useState("");
  const[busy,setBusy]=useState(true);
  const[error,setError]=useState("");

  useEffect(()=>{let live=true;(async()=>{
    const g=await requireTenant();if(!g||!live)return;
    setEmail(g.session.user.email||"");
    const r=await supabase().rpc("wl_sites");
    if(!live)return;
    if(r.error){setError(say(r.error));setBusy(false);return}
    const list=r.data||[];
    setSites(list);
    const requested=new URLSearchParams(location.search).get("site")||selectedSiteId()||list[0]?.id||"";
    const id=list.some(x=>String(x.id)===String(requested))?requested:(list[0]?.id||"");
    setSiteId(id);
    if(id)rememberSite(id,list.find(x=>String(x.id)===String(id))?.name||"");
  })();return()=>{live=false}},[]);

  useEffect(()=>{if(!siteId)return;let live=true;(async()=>{
    setBusy(true);setError("");setData(null);setRestaurantConfig(null);setRestaurantDay(null);setSnapshot(null);setWindowData(null);setReportDate("");
    const sb=supabase();
    const [cfg,overview]=await Promise.all([
      sb.rpc("wl_restaurant_site_config",{p_site_id:siteId}),
      sb.rpc("wl_analytics_overview",{p_days:days,p_site_id:siteId})
    ]);
    if(!live)return;
    if(!overview.error)setData(overview.data||{summary:{},by_rule:[]});
    const isRestaurant=!cfg.error&&cfg.data?.enabled===true;
    setRestaurantConfig(isRestaurant?cfg.data:null);

    if(isRestaurant){
      const resolved=await sb.rpc("wl_my_last_completed_business_date",{p_site_id:siteId});
      if(!live)return;
      const date=!resolved.error&&resolved.data?String(resolved.data):"";
      setReportDate(date);
      const [day,snap,win]=await Promise.all([
        date?sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:date}):Promise.resolve({data:null,error:null}),
        date?sb.rpc("wl_my_report_snapshot",{p_site_id:siteId,p_date:date}):Promise.resolve({data:null,error:null}),
        sb.rpc("wl_my_report_window",{p_site_id:siteId,p_days:7,p_end_date:date||null})
      ]);
      if(!live)return;
      if(!day.error)setRestaurantDay(day.data||null);
      if(!snap.error)setSnapshot(snap.data||null);
      if(!win.error)setWindowData(win.data||null);
      if(day.error&&snap.error)setError("WatchLog could not prepare the latest restaurant insights.");
    }else if(overview.error){
      setError(say(overview.error));
    }
    setBusy(false);
  })();return()=>{live=false}},[siteId,days]);

  async function choose(id){
    setSiteId(id);
    const row=sites.find(x=>String(x.id)===String(id));
    rememberSite(id,row?.name||"");
    history.replaceState(null,"",id?"/analytics/?site="+encodeURIComponent(id):"/analytics/");
  }

  const site=useMemo(()=>sites.find(x=>String(x.id)===String(siteId))||null,[sites,siteId]);

  return <div className="shell"><Nav active="Analytics" email={email} currentSiteId={siteId}/><main className={"main "+styles.page}>
    <header className="target-page-head"><div><div className="target-eyebrow">Insights</div><h1>{restaurantConfig?"Understand the service day at "+(site?.name||"this site"):"What is happening at "+(site?.name||"this site")+"?"}</h1><p>{restaurantConfig?"Business activity first, with monitoring limits shown before conclusions.":"Understand useful activity patterns without digging through camera events."}</p></div><div className="target-actions">{sites.length>1&&<select aria-label="Choose site" value={siteId} onChange={e=>choose(e.target.value)}>{sites.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select>}{!restaurantConfig&&<select aria-label="Choose insight period" value={days} onChange={e=>setDays(Number(e.target.value))}><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option></select>}<a className={ui.secondaryLink} href={withSite("/analytics/studio/",siteId)}>What to measure</a></div></header>
    {error&&<div className="err">{error}</div>}
    {busy?<div className={styles.empty}>Preparing management insights…</div>:restaurantConfig?<RestaurantInsights siteId={siteId} site={site} date={reportDate} day={restaurantDay} snapshot={snapshot} windowData={windowData}/>:<GenericInsights siteId={siteId} data={data}/>}
  </main></div>;
}
