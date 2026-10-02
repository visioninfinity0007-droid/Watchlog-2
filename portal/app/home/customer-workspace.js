"use client";

import {useCallback,useEffect,useState} from "react";
import {supabase} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId,withSite} from "../site-context";
import {OwnerPage,AskBar,SiteSelect,Lead,Section,Row,Status,Ledger,RailSection,Stat,Figure,Summary,Bars,HBars,Compare,Empty,Loading,Notice,AskLinks,Timeline,ratioPct,num,fmt,tone} from "../owner/ui";

function sev(v){return String(v||"info").toLowerCase()}
function needsAttention(x){return ["critical","warning","attention"].includes(sev(x&&x.severity))}
function when(ts,timeZone){
  if(!ts)return "";
  try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(ts))+" · site time"}
  catch{return String(ts)}
}
function hourLabel(ts,timeZone){
  try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",hour:"numeric",hour12:true}).format(new Date(ts))}
  catch{return ""}
}
function dayLabel(d){
  try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",weekday:"short"}).format(new Date(String(d)+"T00:00:00Z"))}
  catch{return String(d)}
}
function faultLabel(f){
  const key=String((f&&(f.reason||f.reason_code||f.fault_type))||"").toLowerCase();
  const labels={
    agent_unreachable:"WatchLog connection lost",
    nvr_unreachable:"Camera system unreachable",
    nvr_auth_failed:"Camera system sign-in failed",
    storage_fault:"Camera system storage issue",
    storage_degraded:"Camera system storage needs attention",
    disk_full:"Camera system storage is full",
    video_loss:"Camera offline",
    camera_offline:"Camera offline",
    not_recording:"Recording is not confirmed",
    recording_storage_fault:"Recording storage issue"
  };
  return labels[key]||"Monitoring needs attention";
}
function signedPct(v){const n=num(v);if(n===null)return null;return (n>0?"+":n<0?"−":"")+Math.abs(n).toFixed(Math.abs(n)%1?1:0)+"%"}
function signedNum(v,unit=""){const n=num(v);if(n===null)return null;const a=Math.abs(n);return (n>0?"+":n<0?"−":"")+(Number.isInteger(a)?a:a.toFixed(1))+unit}

// Governed comparisons only: the restaurant period (wl_my_report_window → structured_restaurant_metrics)
// or the office working-day period (wl_office_period). Both periods need enough observed days; a zero
// previous denominator never becomes a percentage, and unobserved days stay gaps, never zero.
function restaurantChange(windowData){
  const period=windowData&&windowData.structured_restaurant_metrics;
  if(!period||period.enabled!==true)return null;
  const current=period.summary||{},previous=period.previous_period||{},delta=period.comparison||{};
  const expected=Math.max(1,Number(current.expected_service_days||period.days||7));
  const observed=Math.max(0,Number(current.observed_service_days||0));
  const previousObserved=Math.max(0,Number(previous.observed_service_days||0));
  const minimum=Math.min(4,expected);
  const qualifier=observed+" of "+expected+" service days observed, "+previousObserved+" of "+expected+" previously. Missing periods are not treated as zero activity.";
  const days=(period.daily||[]).map(d=>{
    const seen=Number(d.camera_observations||0)>0||Number(d.table_observations||0)>0;
    return{label:dayLabel(d.service_date),value:seen?Number(d.estimated_covers||0):0,gap:!seen,title:seen?d.service_date+": "+fmt(d.estimated_covers)+" estimated covers":d.service_date+": not observed"};
  });
  if(observed<minimum||previousObserved<minimum)return{ready:false,kind:"restaurant",qualifier,days,rows:[]};
  const rows=[];
  const covers=num(delta.estimated_covers_pct);
  if(num(current.avg_estimated_covers_per_observed_day)!==null&&num(previous.avg_estimated_covers_per_observed_day)!==null){
    rows.push({label:"Estimated covers",note:"per observed service day",current:current.avg_estimated_covers_per_observed_day,previous:previous.avg_estimated_covers_per_observed_day,delta:covers,deltaLabel:covers===null?"No comparison":signedPct(covers)});
  }
  const service=num(delta.median_time_to_food_delta_minutes);
  if(num(current.median_observed_time_to_food_minutes)!==null&&num(previous.median_observed_time_to_food_minutes)!==null){
    rows.push({label:"Observed time to food",note:"median, minutes",current:current.median_observed_time_to_food_minutes,previous:previous.median_observed_time_to_food_minutes,delta:service,deltaLabel:signedNum(service," min")});
  }
  const coverageDelta=num(delta.coverage_delta_points);
  if(num(current.avg_coverage_ratio)!==null&&num(previous.avg_coverage_ratio)!==null){
    rows.push({label:"Monitoring coverage",note:"average of the period",current:current.avg_coverage_ratio,previous:previous.avg_coverage_ratio,delta:coverageDelta,deltaLabel:signedNum(coverageDelta," pts")});
  }
  let headline="No material change stands out in the supported comparison.";
  if(covers!==null&&Math.abs(covers)>=5)headline="Estimated covers were "+Math.abs(covers).toFixed(Math.abs(covers)%1?1:0)+"% "+(covers>0?"higher":"lower")+" than the previous "+expected+" days.";
  else if(service!==null&&Math.abs(service)>=1)headline="Observed time to food was "+Math.abs(service)+" min "+(service<0?"faster":"slower")+" than the previous period.";
  return{ready:true,kind:"restaurant",headline,qualifier,days,rows};
}
function officeChange(period){
  if(!period||period.enabled!==true)return null;
  const s=period.summary||{},p=period.previous_period||{},c=period.comparison||{};
  const days=Math.max(1,Number(s.days||7));
  const observed=Number(s.observed_days||0),prevObserved=Number(p.observed_days||0);
  const minimum=Math.min(4,days);
  const qualifier=observed+" of "+days+" working days observed, "+prevObserved+" of "+Number(p.days||days)+" previously. Missing periods are not treated as zero activity.";
  const series=(period.daily||[]).map(d=>{
    const seen=Number(d.coverage_ratio||0)>0;
    return{label:dayLabel(d.date),value:seen?Number(d.activity_detections||0):0,gap:!seen,title:d.date+": "+(seen?fmt(d.activity_detections)+" activity detections":"not observed")};
  });
  if(observed<minimum||prevObserved<minimum)return{ready:false,kind:"office",qualifier,days:series,rows:[]};
  const rows=[
    {label:"Activity detections",note:"camera detections, not unique people",current:s.activity_detections,previous:p.activity_detections,delta:c.activity_detections_delta,deltaLabel:signedNum(c.activity_detections_delta)},
    {label:"After-hours activity",note:"outside working hours",current:s.after_hours_total,previous:p.after_hours_total,delta:c.after_hours_delta,deltaLabel:signedNum(c.after_hours_delta)},
  ];
  if(num(s.avg_coverage_ratio)!==null&&num(p.avg_coverage_ratio)!==null)rows.push({label:"Monitoring coverage",note:"average of the period",current:s.avg_coverage_ratio,previous:p.avg_coverage_ratio,delta:c.coverage_delta_points,deltaLabel:signedNum(c.coverage_delta_points," pts")});
  const after=num(c.after_hours_delta),act=num(c.activity_detections_delta);
  let headline="No material change stands out in the supported comparison.";
  if(after!==null&&after!==0)headline="After-hours activity "+(after>0?"rose by ":"fell by ")+Math.abs(after)+" against the previous "+days+" working days.";
  else if(act!==null&&num(p.activity_detections)&&Math.abs(act)/Number(p.activity_detections)>=0.1)headline="Activity was "+(act>0?"higher":"lower")+" than the previous "+days+" working days.";
  return{ready:true,kind:"office",headline,qualifier,days:series,rows};
}

export default function CustomerHome(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[ctx,setCtx]=useState(null);
  const[items,setItems]=useState([]);
  const[daily,setDaily]=useState(null);
  const[analytics,setAnalytics]=useState(null);
  const[restaurantConfig,setRestaurantConfig]=useState(null);
  const[latestReport,setLatestReport]=useState(null);
  const[latestReportDate,setLatestReportDate]=useState("");
  const[reportWindow,setReportWindow]=useState(null);
  const[officePeriod,setOfficePeriod]=useState(null);
  const[busy,setBusy]=useState(true);
  const[partial,setPartial]=useState(false);
  const[error,setError]=useState("");

  const loadSite=useCallback(async(id)=>{
    if(!id)return;
    setBusy(true);setPartial(false);setError("");
    const sb=supabase();
    setRestaurantConfig(null);setLatestReport(null);setLatestReportDate("");setReportWindow(null);setOfficePeriod(null);
    const results=await Promise.all([
      sb.rpc("wl_ai_context",{p_site_id:id}),
      sb.rpc("wl_notifications",{p_site_id:id,p_limit:20}),
      sb.rpc("wl_my_daily_intelligence",{p_site_id:id,p_date:null}),
      sb.rpc("wl_analytics_overview",{p_days:1,p_site_id:id}),
      sb.rpc("wl_restaurant_site_config",{p_site_id:id})
    ]);
    const c=results[0],q=results[1],d=results[2],a=results[3],rc=results[4];
    setCtx(c.error?null:(c.data||null));
    setItems(q.error?[]:(q.data||[]));
    setDaily(d.error?null:(d.data||null));
    setAnalytics(a.error?null:(a.data||null));
    const isRestaurant=!rc.error&&rc.data?.enabled===true;
    setRestaurantConfig(isRestaurant?(rc.data||null):null);
    if(c.error||q.error||d.error||a.error||rc.error)setPartial(true);
    setBusy(false);
    // Secondary, slower governed context loads after the first read is on screen.
    const resolved=await sb.rpc("wl_my_last_completed_business_date",{p_site_id:id});
    const reportDate=!resolved.error&&resolved.data?String(resolved.data):"";
    if(reportDate){
      const[snap,windowResult]=await Promise.all([
        sb.rpc("wl_my_report_snapshot",{p_site_id:id,p_date:reportDate}),
        isRestaurant?sb.rpc("wl_my_report_window",{p_site_id:id,p_days:7,p_end_date:reportDate}):Promise.resolve({data:null,error:null})
      ]);
      if(!snap.error&&snap.data){setLatestReport(snap.data);setLatestReportDate(reportDate)}
      if(!windowResult.error)setReportWindow(windowResult.data||null);
    }
    if(!isRestaurant){
      const op=await sb.rpc("wl_office_period",{p_site_id:id,p_days:7,p_working_only:true});
      if(!op.error)setOfficePeriod(op.data||null);
    }
  },[]);

  useEffect(()=>{let live=true;(async()=>{
    const guard=await requireTenant();if(!guard||!live)return;
    setEmail(guard.session.user.email||"");
    const r=await supabase().rpc("wl_sites");
    if(!live)return;
    if(r.error){setError("WatchLog could not load your sites. Refresh and try again.");setBusy(false);return}
    const list=r.data||[];
    setSites(list);
    const requested=new URLSearchParams(location.search).get("site")||selectedSiteId()||(list[0]&&list[0].id)||"";
    const id=list.some(function(x){return String(x.id)===String(requested)})?requested:((list[0]&&list[0].id)||"");
    setSiteId(id);
    if(id){
      const row=list.find(function(x){return String(x.id)===String(id)});
      rememberSite(id,(row&&row.name)||"");
      await loadSite(id);
    }else setBusy(false);
  })();return function(){live=false}},[loadSite]);

  async function choose(id){
    setSiteId(id);
    const row=sites.find(function(x){return String(x.id)===String(id)});
    rememberSite(id,(row&&row.name)||"");
    history.replaceState(null,"",id?"/home/?site="+encodeURIComponent(id):"/home/");
    await loadSite(id);
  }

  const site=sites.find(function(x){return String(x.id)===String(siteId)})||null;
  const tz=site?.timezone;
  const cams=((ctx&&ctx.cameras)||[]).filter(function(x){return x.monitor});
  const healthy=cams.filter(function(x){return String(x.health_state||"").toLowerCase()==="operational"}).length;
  const connected=Boolean(ctx&&ctx.connectivity&&ctx.connectivity.agent_online);
  const seen=Boolean(ctx&&ctx.connectivity&&ctx.connectivity.last_seen);
  const faults=(ctx&&ctx.faults)||[];
  const unread=items.filter(function(x){return !x.read});
  const attention=unread.filter(needsAttention);
  const urgent=attention.filter(function(x){return sev(x.severity)==="critical"});
  const currentCoverage=ratioPct(daily&&daily.coverage&&daily.coverage.coverage_ratio);
  const completedCoverage=ratioPct(latestReport&&latestReport.payload&&latestReport.payload.coverage&&latestReport.payload.coverage.coverage_ratio);
  const coverage=currentCoverage!==null?currentCoverage:completedCoverage;
  const coverageScope=currentCoverage!==null?"current reporting period":completedCoverage!==null?"latest completed service day":"current reporting period";
  const completedMetrics=((latestReport&&latestReport.payload&&latestReport.payload.metrics)||[]).slice(0,4);
  const completedHighlights=((latestReport&&latestReport.payload&&latestReport.payload.highlights)||[]).slice(0,3);
  const change=restaurantConfig?restaurantChange(reportWindow):officeChange(officePeriod);

  const summary=(analytics&&analytics.summary)||{};
  const rules=(analytics&&analytics.by_rule)||[];
  const activity=Number(summary.visitor_in||0)+Number(summary.vehicles_in||0)+Number(summary.zone_entries||0);
  const afterHours=Number(summary.after_hours||0);
  const analyticsReady=rules.length>0||activity>0||afterHours>0;

  // One decisive owner statement, driven only by governed facts.
  const coverageText=coverage===null?"monitoring not verified yet":"monitoring "+coverage+"% verified";
  let title="Nothing needs your attention right now.";
  let body="";
  let leadTone=coverage===null?"unknown":"ok";
  let cta=null;
  if(!seen&&!connected){
    title="WatchLog is getting this site ready.";
    body="Monitoring information appears here after this site connects.";
    leadTone="unknown";
    cta=<a className="ow-btn" href={withSite("/setup/",siteId)}>Continue setup</a>;
  }else if(!connected){
    title="Monitoring needs attention · site connection lost";
    body="The current picture may be incomplete until the site reconnects.";
    leadTone="bad";
    cta=<a className="ow-btn" href={withSite("/site-health/",siteId)}>Check monitoring</a>;
  }else if(urgent.length){
    title=urgent.length+" urgent "+(urgent.length===1?"item needs":"items need")+" review · "+coverageText;
    body=urgent[0].title||"";
    leadTone="bad";
    cta=<a className="ow-btn" href={withSite("/notifications/",siteId)}>Review now</a>;
  }else if(attention.length){
    title=attention.length+" "+(attention.length===1?"item needs":"items need")+" review · "+coverageText;
    body=attention.map(x=>x.title).filter(Boolean).slice(0,2).join(" · ");
    leadTone="warn";
    cta=<a className="ow-btn" href={withSite("/notifications/",siteId)}>Review attention</a>;
  }else if(faults.length){
    title="Monitoring needs attention · "+coverageText;
    body=faults.length+" monitoring "+(faults.length===1?"item needs":"items need")+" checking.";
    leadTone="warn";
    cta=<a className="ow-btn" href={withSite("/site-health/",siteId)}>Check monitoring</a>;
  }else if(coverage!==null&&coverage<100){
    title="Nothing needs review · monitoring "+coverage+"% verified";
    body="Unverified time is not treated as quiet time.";
    leadTone="warn";
  }else if(coverage!==null){
    title="Nothing needs your attention right now.";
    body="Monitoring coverage is "+coverage+"% for the "+coverageScope+".";
  }else{
    body="Monitoring coverage for the current period is not verified yet.";
  }

  const now=new Date();
  const dayAgo=new Date(now.getTime()-24*3600*1000);
  const recent=unread.filter(x=>x.created_at&&new Date(x.created_at)>=dayAgo);
  const ticks=[0,6,12,18,24].map(h=>{const at=new Date(dayAgo.getTime()+h*3600*1000);return{at,label:h===24?"Now":hourLabel(at,tz)}});

  const reportHref=withSite("/reports/?view=yesterday"+(latestReportDate?"&date="+encodeURIComponent(latestReportDate):""),siteId);
  const rail=<>
    <RailSection label="Monitoring coverage" action={<a href={withSite("/site-health/",siteId)}>Health</a>}>
      {coverage===null?<Figure value="—" unit="not verified yet"/>:<Figure value={coverage+"%"} unit={"verified · "+coverageScope}/>}
      <div style={{marginTop:10}}><Ledger ratio={coverage===null?null:coverage/100} classes={currentCoverage!==null?daily?.coverage?.classes:null}/></div>
      <div style={{marginTop:8}}>
        <Stat label="Site connection" value={<Status tone={connected?"ok":seen?"bad":"unknown"}>{connected?"Connected":seen?"Disconnected":"Not connected yet"}</Status>}/>
        <Stat label="Cameras confirmed healthy" value={cams.length?healthy+" of "+cams.length:null}/>
      </div>
    </RailSection>
    <RailSection label="Attention" action={<a href={withSite("/notifications/",siteId)}>Open</a>}>
      <Stat label="Needs review" value={String(attention.length)}/>
      <Stat label="Urgent" value={String(urgent.length)}/>
      <Stat label="Monitoring issues" value={String(faults.length)}/>
    </RailSection>
    <RailSection label="Latest report">
      {latestReportDate?<a className="ow-rail-link" href={reportHref}><span>{restaurantConfig?"Completed service day":"Completed day"}<br/><small>{latestReportDate}</small></span><i>Open</i></a>:<p className="ow-rail-note">No completed report is available yet.</p>}
    </RailSection>
    <RailSection label="Ask WatchLog">
      <AskLinks siteId={siteId} prompts={["What needs my attention right now?","What happened during the latest completed business day?","Was the latest reporting period fully monitored?"]}/>
    </RailSection>
  </>;

  const mobileSummary=busy?null:<Summary items={[
    {value:String(attention.length),label:"Need review"},
    {value:coverage===null?"Not verified":coverage+"%",label:"Monitoring coverage",muted:coverage===null,ledger:coverage===null?null:coverage/100,classes:currentCoverage!==null?daily?.coverage?.classes:null},
    {value:cams.length?healthy+"/"+cams.length:"—",label:"Cameras healthy",muted:!cams.length},
    restaurantConfig&&completedMetrics.length?{value:String(completedMetrics[0].value??"—"),label:completedMetrics[0].label||"Latest business figure"}:analyticsReady?{value:fmt(activity),label:"Activity · 24 hours"}:null,
  ]}/>;

  return <OwnerPage active="Home" email={email} siteId={siteId}
    kicker={["Home",site?.timezone?"Site time "+site.timezone.replace(/^.*\//,"").replace(/_/g," "):null]}
    title={(site&&site.name)||"Your site"}
    actions={<><AskBar siteId={siteId} placeholder={"Ask WatchLog about "+((site&&site.name)||"this site")+"…"}/><SiteSelect sites={sites} value={siteId} onChange={choose}/></>}
    rail={busy?null:rail} summary={mobileSummary}>
    {error&&<Notice tone="bad">{error}</Notice>}
    {partial&&<Notice>Some parts of this overview could not be refreshed. Available information is shown.</Notice>}
    {busy?<Loading label="Loading WatchLog overview"/>:<>
      <Lead tone={leadTone} title={title} body={body} action={cta}/>

      <Section first title="Needs attention" count={attention.length||null} action={<a href={withSite("/notifications/",siteId)}>View all</a>}>
        {recent.length>0&&<Timeline from={dayAgo} to={now} ticks={ticks} items={recent.map(x=>({at:x.created_at,tone:tone(x.severity)==="unknown"?"info":tone(x.severity),label:(x.title||"Item")+" · "+when(x.created_at,tz),href:x.href||withSite("/notifications/",siteId)}))}/>}
        {attention.length?<div className="ow-rows">{attention.slice(0,3).map(function(item){
          const s=sev(item.severity);
          return <Row key={String(item.kind)+":"+String(item.id)} tone={s==="critical"?"bad":"warn"} title={item.title} body={item.body} meta={[s==="critical"?"Urgent":"Attention",when(item.created_at,tz)]}
            action={<a href={item.href||withSite("/notifications/",siteId)}>{item.kind==="health"?"Check monitoring":item.kind==="report"?"Open report":"Review"}</a>}/>;
        })}</div>:faults.length?<div className="ow-rows">{faults.slice(0,3).map(function(f,i){return <Row key={f.id||i} tone="warn" title={f.camera||"Site monitoring"} body={faultLabel(f)} action={<a href={withSite("/site-health/",siteId)}>Check monitoring</a>}/>})}</div>
        :<Empty title="Nothing needs your attention right now.">Anything WatchLog cannot verify stays marked as not verified.</Empty>}
      </Section>

      {change&&<Section title="What changed" note={(change.kind==="restaurant"?"Last 7 service days vs the previous 7":"Last 7 working days vs the previous 7")} action={<a href={withSite("/analytics/",siteId)}>Insights</a>}>
        {change.ready?<>
          <p style={{fontSize:15,fontWeight:600,color:"var(--ow-ink)",marginBottom:14}}>{change.headline}</p>
          <div className="ow-grid2">
            {change.days.length>0&&<Bars height={96} question={change.kind==="restaurant"?"Estimated covers by service day":"Activity detections by working day"} series={change.days} showValues={false}
              legend={<><span>Observed</span>{change.days.some(d=>d.gap)&&<span className="gap">Not observed</span>}</>}/>}
            <div><Compare rows={change.rows}/><div className="ow-legend" style={{marginTop:10}}><span>This period</span><span className="prev">Previous period</span></div></div>
          </div>
        </>:<Empty title="A reliable comparison is not ready yet.">WatchLog compares periods once enough days are observed in both.</Empty>}
        <p className="ow-muted" style={{fontSize:12,marginTop:10}}>{change.qualifier}</p>
      </Section>}

      <Section title={restaurantConfig&&completedMetrics.length?"Latest completed service day":"Activity · last 24 hours"} action={<a href={withSite("/analytics/",siteId)}>Open insights</a>}>
        {restaurantConfig&&completedMetrics.length?<>
          {completedHighlights.length>0&&<p style={{fontSize:14,color:"var(--ow-ink)",marginBottom:12}}>{typeof completedHighlights[0]==="string"?completedHighlights[0]:(completedHighlights[0].title||"")}</p>}
          <div className="ow-metrics">{completedMetrics.map(function(m,i){return <div className={"ow-metric"+(i===0?" primary":"")} key={(m.label||i)+"-"+i}><b className={m.value==null?"unknown":""}>{m.value==null?"Not available":String(m.value)}</b><span>{m.label||"Business metric"}</span></div>})}</div>
        </>:analyticsReady?<>
          <p style={{fontSize:14,color:"var(--ow-ink)",marginBottom:12}}><b>{fmt(activity)}</b> entries recorded by your activity rules{afterHours?<> · <b style={{color:"var(--ow-warn)"}}>{afterHours} after hours</b></>:null}.</p>
          {rules.length>0?<HBars question="Where activity came from" items={rules.slice(0,4).map(r=>({key:r.rule_id,label:r.name||"Activity rule",note:r.camera,value:r.count}))}/>
            :<div className="ow-metrics">{[["Visitor entries",summary.visitor_in],["Vehicle entries",summary.vehicles_in],["Area entries",summary.zone_entries],["After-hours activity",afterHours]].map(([l,v])=><div className="ow-metric" key={l}><b>{fmt(v)}</b><span>{l}</span></div>)}</div>}
          <p className="ow-muted" style={{fontSize:12,marginTop:10}}>Entries are rule crossings, not unique people.</p>
        </>:<Empty title="Business activity insights are not ready yet." action={<a className="ow-btn quiet small" href={restaurantConfig?reportHref:withSite("/analytics/studio/",siteId)}>{restaurantConfig?"Open latest report":"Choose what to measure"}</a>}>Measured patterns appear here once this site has supported activity insights.</Empty>}
      </Section>

      <Section title="Ask WatchLog" className="ow-narrow-only">
        <AskLinks siteId={siteId} prompts={["What needs my attention right now?","Was the latest reporting period fully monitored?"]}/>
      </Section>
    </>}
  </OwnerPage>;
}
