"use client";

import {useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId,withSite} from "../site-context";
import {OwnerPage,SiteSelect,Lead,Section,Row,Metrics,Ledger,RailSection,Stat,Figure,Summary,Bars,HBars,Compare,Delta,Empty,Loading,Notice} from "../owner/ui";

function n(v){return Number(v||0).toLocaleString()}
function label(v){return String(v||"Activity").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase())}
function pct(v){if(v===null||v===undefined||v==="")return null;const x=Number(v);return Number.isFinite(x)?Math.round(Math.max(0,Math.min(1,x))*100):null}
function dateLabel(v){if(!v)return"Latest completed service day";try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(new Date(v+"T00:00:00Z"))}catch{return v}}
function dayShort(v){try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",weekday:"short"}).format(new Date(String(v)+"T00:00:00Z"))}catch{return String(v)}}
function dayMonth(v){try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",day:"numeric",month:"short"}).format(new Date(String(v)+"T00:00:00Z"))}catch{return String(v)}}
function metricValue(metric){if(metric===null||metric===undefined)return null;if(typeof metric==="object"&&metric.value!==undefined)return metric.value===null?null:String(metric.value);return String(metric)}
function numeric(v){if(v===null||v===undefined||v==="")return null;const x=Number(v);return Number.isFinite(x)?x:null}
function compactNumber(v){const x=numeric(v);if(x===null)return"—";return Number.isInteger(x)?String(x):x.toFixed(1)}
function signed(v,unit=""){const x=numeric(v);if(x===null)return null;const a=Math.abs(x);return (x>0?"+":x<0?"−":"")+(Number.isInteger(a)?a:a.toFixed(1))+unit}

// Governed restaurant rolling period: wl_my_report_window → structured_restaurant_metrics.
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
  const rows=[];
  if(numeric(summary.avg_estimated_covers_per_observed_day)!==null&&numeric(previous.avg_estimated_covers_per_observed_day)!==null)rows.push({label:"Estimated covers",note:"per observed service day",current:summary.avg_estimated_covers_per_observed_day,previous:previous.avg_estimated_covers_per_observed_day,delta:covers,deltaLabel:covers===null?"No comparison":signed(covers,"%")});
  if(numeric(summary.median_observed_time_to_food_minutes)!==null&&numeric(previous.median_observed_time_to_food_minutes)!==null)rows.push({label:"Observed time to food",note:"median, minutes",current:summary.median_observed_time_to_food_minutes,previous:previous.median_observed_time_to_food_minutes,delta:service,deltaLabel:signed(service," min")});
  if(numeric(summary.avg_coverage_ratio)!==null&&numeric(previous.avg_coverage_ratio)!==null)rows.push({label:"Monitoring coverage",note:"period average",current:summary.avg_coverage_ratio,previous:previous.avg_coverage_ratio,delta:coverageDelta,deltaLabel:signed(coverageDelta," pts")});
  const daily=(period.daily||[]).map(d=>{const seen=Number(d.camera_observations||0)>0||Number(d.table_observations||0)>0;return{date:d.service_date,seen,covers:seen?Number(d.estimated_covers||0):null,coverage:numeric(d.coverage_ratio)}});
  return{
    expected,observed,previousObserved,ready,changes:changes.slice(0,3),rows,daily,
    hours:(period.hour_profile||[]).filter(h=>numeric(h.avg_peak_visible_diners)!==null),
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
  const peakVisible=hourly.length?hourly.reduce((m,x)=>Math.max(m,Number(x.peak_visible_customers||0)),0):null;
  const peakTables=hourly.length?hourly.reduce((m,x)=>Math.max(m,Number(x.peak_occupied_tables||0)),0):null;
  const businessReady=manual||Number(q.camera_observations||0)>0||Number(q.table_observations||0)>0;
  const coverage=pct(snapshot?.payload?.coverage?.coverage_ratio);
  const manualMetrics=(payload.metrics||[]).slice(0,4);
  const highlights=(payload.highlights||[]).slice(0,4);
  const period=restaurantPeriodView(windowData);

  const liveMetrics=[
    {value:peakVisible,label:"Peak visible diners",note:"Concurrent visible diners, not unique footfall",primary:true},
    {value:peakTables,label:"Peak occupied tables",note:"Highest simultaneous table use"},
    {value:sessions.estimated_covers??null,label:"Estimated covers",note:"Camera-based estimate from supported table activity"},
    {value:sessions.median_observed_time_to_food_minutes==null?null:sessions.median_observed_time_to_food_minutes+" min",label:"Observed time to food",note:"Visible seating to first food seen"}
  ];
  const metrics=manual&&manualMetrics.length?manualMetrics.map((m,i)=>({value:metricValue(m),label:m.label||"Business metric",note:m.note,primary:i===0})):liveMetrics;
  const maxCovers=period?Math.max(0,...period.daily.filter(d=>d.seen).map(d=>d.covers)):0;
  const busiest=period?.daily.find(d=>d.seen&&d.covers===maxCovers&&maxCovers>0);

  return <>
    {period?<Lead tone={period.ready?"info":"unknown"} title={period.ready?period.changes[0]:"Reliable comparison not ready yet"} body={period.observed+" of "+period.expected+" service days observed. Missing periods are not treated as zero activity."}/>
      :<Lead tone={businessReady?"info":"unknown"} title={dateLabel(date)} body={businessReady?"Latest completed service day.":"WatchLog is not presenting unsupported business figures for this service day."}/>}

    {period&&<Section first title="Service-day trend" note="Estimated covers by service day" action={<a href={withSite("/reports/?view=week",siteId)}>7-day report</a>}>
      <Bars question="Which service days were busiest?" series={period.daily.map(d=>({label:dayShort(d.date),value:d.covers||0,gap:!d.seen,peak:Boolean(busiest&&d.date===busiest.date),title:d.date+": "+(d.seen?d.covers+" estimated covers":"not observed")}))}
        legend={<><span>Estimated covers</span>{period.daily.some(d=>!d.seen)&&<span className="gap">Not observed</span>}</>}/>
      {busiest&&<p className="ow-muted" style={{fontSize:12.5,marginTop:8}}>Busiest observed day: <b style={{color:"var(--ow-ink)"}}>{dateLabel(busiest.date)}</b>, {busiest.covers} estimated covers.</p>}
    </Section>}

    {period&&<Section title="7-day pattern" note="Compared with the previous 7 service days">
      {period.ready?<>
        <Compare rows={period.rows}/>
        <div className="ow-legend" style={{marginTop:10}}><span>This period</span><span className="prev">Previous period</span></div>
        {period.changes.length>1&&<ul style={{margin:"12px 0 0",paddingLeft:18,fontSize:13}}>{period.changes.slice(1).map(x=><li key={x}>{x}</li>)}</ul>}
      </>:<Empty title="Reliable comparison not ready yet">Current period: {period.observed} of {period.expected} observed service days. Previous period: {period.previousObserved} of {period.expected}.</Empty>}
    </Section>}

    {period&&period.hours.length>0&&<Section title="When diners are busiest" note="Average peak visible diners by hour, observed days only">
      <Bars question="Which hours carry the most visible demand?" height={110} series={period.hours.map(h=>({label:String(h.local_hour).replace(/:00$/,""),value:Number(h.avg_peak_visible_diners),display:compactNumber(h.avg_peak_visible_diners)}))}/>
    </Section>}

    <Section first={!period} title="Latest completed service day" note={dateLabel(date)} action={<a href={withSite("/reports/?view=yesterday"+(date?"&date="+encodeURIComponent(date):""),siteId)}>Full report</a>}>
      {businessReady?<Metrics items={metrics}/>:<Empty title="Business figures are not available for this service day.">A completed report exists, but WatchLog does not present unsupported figures.</Empty>}
      {highlights.length>0&&<div className="ow-rows" style={{marginTop:14}}>{highlights.map((x,i)=><Row compact key={i} tone="violet" title={typeof x==="string"?x:(x.title||x.body||"Management observation")}/>)}</div>}
    </Section>

    <details className="ow-details" style={{marginTop:18}}>
      <summary>What these cameras can support</summary>
      <div style={{fontSize:13}}>
        <p>Dining-floor views support visible diners, table use, estimated table sessions and covers, and observed time to food when coverage is adequate.</p>
        <ul style={{margin:"8px 0 0",paddingLeft:18}}>
          <li>Visible diners are not unique customer footfall.</li>
          <li>No sales, revenue or transaction count is inferred.</li>
          <li>Unverified time is not zero activity.</li>
        </ul>
        {coverage!==null&&<p style={{marginTop:8}}>Latest completed service day coverage: {coverage}%.</p>}
      </div>
    </details>
  </>;
}

function officePeriodView(period){
  if(!period||period.enabled!==true)return null;
  const s=period.summary||{},p=period.previous_period||{},c=period.comparison||{};
  const days=Math.max(1,Number(s.days||7)),observed=Number(s.observed_days||0),prevObserved=Number(p.observed_days||0);
  const minimum=Math.min(4,days);
  const daily=(period.daily||[]).map(d=>{const seen=Number(d.coverage_ratio||0)>0;return{date:d.date,seen,working:d.working_day!==false,value:seen?Number(d.activity_detections||0):null,after:Number(d.after_hours_count||0)}});
  const rows=[
    {label:"Activity detections",note:"not unique people",current:s.activity_detections,previous:p.activity_detections,delta:c.activity_detections_delta,deltaLabel:signed(c.activity_detections_delta)},
    {label:"After-hours activity",current:s.after_hours_total,previous:p.after_hours_total,delta:c.after_hours_delta,deltaLabel:signed(c.after_hours_delta)},
    {label:"Attention items",current:s.incidents_total,previous:p.incidents_total,delta:c.incidents_delta,deltaLabel:signed(c.incidents_delta)},
  ];
  if(numeric(s.avg_coverage_ratio)!==null&&numeric(p.avg_coverage_ratio)!==null)rows.push({label:"Monitoring coverage",note:"period average",current:s.avg_coverage_ratio,previous:p.avg_coverage_ratio,delta:c.coverage_delta_points,deltaLabel:signed(c.coverage_delta_points," pts")});
  return{s,p,c,days,observed,prevObserved,prevDays:Number(p.days||days),ready:observed>=minimum&&prevObserved>=minimum,daily,rows,coverage:pct(s.avg_coverage_ratio)};
}

function GenericInsights({siteId,data,period,days}){
  const s=data?.summary||{},rules=data?.by_rule||[],after=Number(s.after_hours||0),visitors=Number(s.visitor_in||0),vehicles=Number(s.vehicles_in||0),areas=Number(s.zone_entries||0),total=visitors+vehicles+areas;
  const configured=rules.length>0;
  const measured=after>0||total>0;
  const op=officePeriodView(period);
  if(!configured&&!measured&&!op)return <>
    <Lead tone="unknown" title="Activity insights are not configured yet" body="Choose what WatchLog should measure at this site."/>
    <Empty title="Choose what to measure" action={<a className="ow-btn" href={withSite("/analytics/studio/",siteId)}>Set up activity insights</a>}>Add Activity Rules for the patterns that matter. Until then, WatchLog will not turn missing measurements into zero activity.</Empty>
  </>;
  const range=days===1?"last 24 hours":"last "+days+" days";
  const peak=op?op.daily.filter(d=>d.seen).sort((a,b)=>b.value-a.value)[0]:null;
  let leadTitle=total?n(total)+" rule entries in the "+range:"No rule entries in the "+range;
  if(after)leadTitle+=" · "+after+" after hours";
  let leadBody=op&&op.ready&&numeric(op.c.after_hours_delta)?"After-hours activity "+(op.c.after_hours_delta>0?"rose by ":"fell by ")+Math.abs(op.c.after_hours_delta)+" against the previous "+op.days+" working days.":"Entries are counted by your activity rules; they are not unique people.";
  return <>
    <Lead tone={after?"warn":measured?"info":"unknown"} title={leadTitle} body={leadBody} action={after?<a className="ow-btn quiet" href={withSite("/notifications/",siteId)}>Review after-hours</a>:null}/>

    {op&&<Section first title="Activity trend" note={(op.days===7?"Activity detections by completed working day":"Activity detections by day")+" · not unique people"}>
      <Bars question="When was activity highest?" showValues={op.daily.length<=10}
        series={op.daily.map(d=>({label:op.daily.length<=10?dayShort(d.date):dayMonth(d.date).replace(/ .*/,""),value:d.value||0,gap:!d.seen,prev:!d.working&&d.seen,peak:Boolean(peak&&d.date===peak.date),title:d.date+": "+(d.seen?n(d.value)+" detections":"not observed")}))}
        legend={<><span>Observed</span>{op.daily.some(d=>!d.working)&&<span className="prev">Non-working day</span>}{op.daily.some(d=>!d.seen)&&<span className="gap">Not observed</span>}</>}/>
      {peak&&<p className="ow-muted" style={{fontSize:12.5,marginTop:8}}>Highest: <b style={{color:"var(--ow-ink)"}}>{dateLabel(peak.date)}</b>, {n(peak.value)} detections.</p>}
    </Section>}

    {op&&<Section title="What changed" note={"Last "+op.days+" days vs the previous "+op.prevDays}>
      {op.ready?<><Compare rows={op.rows}/><div className="ow-legend" style={{marginTop:10}}><span>This period</span><span className="prev">Previous period</span></div></>
        :<Empty title="Reliable comparison not ready yet">Current period: {op.observed} of {op.days} observed days. Previous period: {op.prevObserved} of {op.prevDays}.</Empty>}
      <p className="ow-muted" style={{fontSize:12,marginTop:10}}>Missing days are gaps; WatchLog never turns missing measurements into zero activity.</p>
    </Section>}

    <Section first={!op} title="Where activity came from" note={"Activity rule entries, "+range} action={<a href={withSite("/analytics/studio/",siteId)}>Activity Rules</a>}>
      {rules.length?<HBars question="Which rules recorded the most activity?" items={rules.slice(0,8).map(r=>({key:r.rule_id,label:r.name||label(r.analytic_key||r.rule_type),note:(r.camera||"Camera")+" · "+label(r.rule_type||r.analytic_key),value:r.count}))}/>
        :<Empty title="No activity insights yet.">Add an Activity Rule when you want WatchLog to measure a specific pattern.</Empty>}
    </Section>

    <Section title="Exceptions" count={after||null}>
      {after?<div className="ow-rows"><Row tone="warn" title={after+" after-hours activity "+(after===1?"item":"items")} body="Recorded outside the site's configured hours." meta={[range]} action={<a href={withSite("/notifications/",siteId)}>Review</a>}/></div>
        :<Empty title="No after-hours activity was recorded in this period.">Only time with monitoring coverage is counted.</Empty>}
    </Section>
  </>;
}

export default function Analytics(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[days,setDays]=useState(7);
  const[data,setData]=useState(null);
  const[officePeriod,setOfficePeriod]=useState(null);
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
    setBusy(true);setError("");setData(null);setOfficePeriod(null);setRestaurantConfig(null);setRestaurantDay(null);setSnapshot(null);setWindowData(null);setReportDate("");
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
    }else{
      if(overview.error)setError(say(overview.error));
      // Governed office working-day series (enabled:false for other site types).
      const op=await sb.rpc("wl_office_period",{p_site_id:siteId,p_days:days===30?30:7,p_working_only:days!==30});
      if(!live)return;
      if(!op.error&&op.data?.enabled)setOfficePeriod(op.data);
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
  const period=restaurantConfig?restaurantPeriodView(windowData):null;
  const op=restaurantConfig?null:officePeriodView(officePeriod);
  const s=data?.summary||{};
  const coverage=restaurantConfig?(period?.coverage??null):(op?.coverage??null);
  const askPrompts=restaurantConfig
    ?[["What changed this week?","What changed across the last 7 completed service days, and is the comparison reliable?"],["When are we busiest?","When is the restaurant busiest, based on the observed service days?"],["What should management act on?","What should management act on from the latest completed service day, and what is still unverified?"]]
    :[["What changed this period?","What changed in this activity period and what looks unusual?"],["Explain the after-hours activity","Explain the after-hours activity in this period and whether it needs follow-up."],["Which areas are busiest?","Which areas recorded the most activity, and when?"]];

  const rail=busy?null:<>
    <RailSection label="Monitoring coverage" action={<a href={withSite("/site-health/",siteId)}>Health</a>}>
      {coverage===null?<Figure value="—" unit="not verified for this period"/>:<Figure value={coverage+"%"} unit="period average"/>}
      <div style={{marginTop:10}}><Ledger ratio={coverage===null?null:coverage/100}/></div>
      {(period||op)&&<p className="ow-rail-note">{period?period.observed+" of "+period.expected+" service days observed.":op.observed+" of "+op.days+" days observed."}</p>}
    </RailSection>
    {restaurantConfig&&period?<RailSection label="7-day figures">
      <Stat label="Avg estimated covers" note="per observed day" value={period.avgCovers===null?null:compactNumber(period.avgCovers)}/>
      <Stat label="Median time to food" note="camera-observed" value={period.medianFood===null?null:compactNumber(period.medianFood)+" min"}/>
      <Stat label="Busiest observed day" value={period.busiestDay?.service_date?dayMonth(period.busiestDay.service_date):null}/>
    </RailSection>:!restaurantConfig&&<RailSection label={"Totals · "+(days===1?"24 hours":days+" days")}>
      <Stat label="Rule entries" value={n(Number(s.visitor_in||0)+Number(s.vehicles_in||0)+Number(s.zone_entries||0))}/>
      <Stat label="After-hours" value={n(s.after_hours)}/>
      {op&&<Stat label="Activity detections" note="working days, not unique people" value={n(op.s.activity_detections)}/>}
    </RailSection>}
    {op&&op.ready&&<RailSection label="Vs previous period">
      <Stat label="Activity" value={<Delta value={op.c.activity_detections_delta} label={signed(op.c.activity_detections_delta)}/>}/>
      <Stat label="After-hours" value={<Delta value={op.c.after_hours_delta} label={signed(op.c.after_hours_delta)}/>}/>
      <Stat label="Coverage" value={<Delta value={op.c.coverage_delta_points} label={signed(op.c.coverage_delta_points," pts")}/>}/>
    </RailSection>}
    {restaurantConfig&&(windowData?.saved_reports||[]).length>0&&<RailSection label="Completed service days">
      {(windowData.saved_reports||[]).slice(0,4).map((r,i)=>{const d=String(r.service_date||"");return <a className="ow-rail-link" key={r.report_id||d||i} href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}><span>{dateLabel(d)}</span><i>Open</i></a>})}
    </RailSection>}
    <RailSection label="Ask WatchLog">
      <div className="ow-ask">{askPrompts.map(([l,p])=><a key={l} href={withSite("/ai/?prompt="+encodeURIComponent(p),siteId)}>{l}</a>)}</div>
    </RailSection>
  </>;

  return <OwnerPage active="Analytics" email={email} siteId={siteId}
    kicker={["Insights",restaurantConfig?"Completed service days":days===1?"Last 24 hours":"Last "+days+" days"]}
    title={site?.name||"Insights"}
    actions={<><SiteSelect sites={sites} value={siteId} onChange={choose}/>{!restaurantConfig&&<select aria-label="Choose insight period" value={days} onChange={e=>setDays(Number(e.target.value))}><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option></select>}<a className="ow-btn quiet" href={withSite("/analytics/studio/",siteId)}>What to measure</a></>}
    rail={rail}
    summary={busy?null:<Summary items={[
      {value:coverage===null?"Not verified":coverage+"%",label:"Monitoring coverage",muted:coverage===null,ledger:coverage===null?null:coverage/100},
      restaurantConfig?(period?{value:period.observed+"/"+period.expected,label:"Service days observed"}:null):{value:n(s.after_hours),label:"After-hours"},
    ]}/>}>
    {error&&<Notice tone="bad">{error}</Notice>}
    {busy?<Loading label="Preparing management insights"/>:restaurantConfig?<RestaurantInsights siteId={siteId} site={site} date={reportDate} day={restaurantDay} snapshot={snapshot} windowData={windowData}/>:<GenericInsights siteId={siteId} data={data} period={officePeriod} days={days}/>}
  </OwnerPage>;
}
