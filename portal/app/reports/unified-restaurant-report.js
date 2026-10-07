"use client";

import {useMemo,useState} from "react";
import {withSite} from "../site-context";
import {Lead,Section,Row,Metrics,Status,Ledger,RailSection,Stat,Summary,Bars,HBars,Findings,Empty} from "../owner/ui";

function num(v){return v==null||Number.isNaN(Number(v))?null:Number(v)}
function val(v,suffix=""){return v==null?"—":String(v)+suffix}
function pct(v){const n=num(v);return n==null?"—":Math.round(n*100)+"%"}
function shortDate(v){if(!v)return"—";try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",day:"numeric",month:"short"}).format(new Date(v+"T00:00:00Z"))}catch{return String(v)}}
function dateLabel(v){if(!v)return"—";try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(new Date(v+"T00:00:00Z"))}catch{return String(v)}}
function weekdayLabel(v){if(!v)return"—";try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",weekday:"short"}).format(new Date(v+"T00:00:00Z"))}catch{return String(v)}}
function clampLevel(v,max){const n=num(v),m=num(max);if(n==null||!m)return 0;return Math.max(0,Math.min(4,Math.round((n/m)*4)))}
function periodName(view){return view==="daily"?"Today":view==="yesterday"?"Yesterday":view==="week"?"Last 7 days":"Last 30 days"}
function metricValue(v,suffix=""){return num(v)==null?null:String(v)+suffix}
function businessReady(data){return Boolean(data&&data.enabled!==false&&data.reconciliation&&data.reconciliation.ready===true)}
function sampleNote(n,label="qualifying sessions"){return num(n)==null?"":String(n)+" "+label}

// Restaurant reports share one decision-first shell for Today / Yesterday / 7 days / 30 days:
// status + period, one conclusion, the figures and the governed chart, then security, actions and detail.
// Dashboard primitives (kept stable for the report contract): reportControlBarV4, reportHeroV4,
// reportMainGridV4, ReportHealth, PeriodBuildState, SavedReports, ReportStatus.

function ReportTabs({mode,setMode}){
  return <div className="ow-tabs" role="tablist" aria-label="Report sections">
    {[["overview","Overview"],["business","Business"],["security","Security"]].map(function(row){const k=row[0],l=row[1];return <button key={k} type="button" role="tab" aria-selected={mode===k} className="ow-tab" onClick={function(){setMode(k)}}>{l}</button>})}
  </div>;
}

function ReportStatus({view,model}){
  const limited=model&&model.sufficiency&&model.sufficiency.level==="limited";
  const label=view==="daily"?"In progress":limited?"Limited coverage":"Completed";
  return <Status tone={view==="daily"?"info":limited?"warn":"verified"}>{label}</Status>;
}

function ReportHealth({view,model}){
  const period=model&&model.kind==="period";
  const current=period?Number(model.observed||0):null;
  const total=period?Number(model.days||0):null;
  return <RailSection label="Report confidence">
    <p style={{fontSize:14,fontWeight:600,color:"var(--ow-ink)"}}>{period?(current+" of "+total+" days represented"):(model.coverage&&model.coverage.status||"Coverage available")}</p>
    {period&&total?<div style={{marginTop:8}}><Ledger ratio={current/total} label={false}/></div>:null}
    <p className="ow-rail-note">{model.sufficiency&&model.sufficiency.message||model.coverage&&model.coverage.summary||"Figures are limited to the periods that can be supported reliably."}</p>
    {model.coverage&&model.coverage.note&&<p className="ow-rail-note">{model.coverage.note}</p>}
  </RailSection>;
}

function PeriodBuildState({model,windowData,siteId}){
  const rows=windowData&&windowData.saved_reports||[];
  const minimum=model.days===7?4:10;
  return <Section first title="Period is still building" note={model.observed+" of "+minimum+" represented days needed before a "+model.days+"-day trend is shown"} className="period-build">
    <Ledger ratio={Math.min(1,Number(model.observed||0)/minimum)} label={false}/>
    <p className="ow-muted" style={{fontSize:12.5,marginTop:8}}>Daily reports stay available below; missing days are unknown, not zero demand.</p>
    {rows.length>0&&<div className="ow-rows" style={{marginTop:10}}>{rows.slice(0,6).map(function(r,i){const d=String(r.service_date||"");return <Row compact key={(r.report_id||d||i)+"-"+i} tone="verified" title={dateLabel(d)} action={<a href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}>Open</a>}/>})}</div>}
  </Section>;
}

function DemandBars({model,title}){
  const series=model.bars||[];
  if(!series.length||!series.some(function(x){return !x.gap}))return <Empty title="There is not enough comparable activity to show this trend yet.">Missing periods are unknown, not zero activity.</Empty>;
  const hasGap=series.some(function(x){return x.gap});
  return <Bars question={title} series={series} showValues={!model.barsRelative&&series.length<=14}
    legend={<><span>{model.barsRelative?"Relative demand":model.kind==="period"?"Estimated covers":"Peak visible diners"}</span>{hasGap&&<span className="gap">Not observed</span>}</>}/>;
}

function SecurityPanel({items=[],coveredDays=null}){
  const critical=items.filter(function(x){return x&&x.severity==="critical"}).length;
  const attention=items.filter(function(x){return x&&x.severity==="attention"}).length;
  const title=critical?"Critical attention":attention?"Attention required":items.length?"No critical exception in completed reports":"No security exception recorded in the available coverage";
  const note=!items.length&&coveredDays!=null?"Applies only to the "+coveredDays+" completed day"+(coveredDays===1?"":"s")+" represented here.":"";
  return <Section title="Security" count={critical+attention||null}>
    {items.length?<div className="ow-rows">{items.slice(0,5).map(function(x,i){return <Row key={(x.title||i)+"-"+i} tone={x.severity==="critical"?"bad":x.severity==="attention"?"warn":"ok"} title={x.title||"Security note"} body={x.body||x.summary||""} meta={[x.service_date?shortDate(x.service_date):null,x.value||null]}/>})}</div>
      :<Empty title={title}>{note||null}</Empty>}
  </Section>;
}

function ActionList({items=[]}){
  if(!items.length)return null;
  return <Section title="Recommended action" count={items.length>1?items.length:null}><div className="ow-actions">{items.slice(0,3).map(function(a,i){return <div className="ow-action" key={(a.id||a.title||i)+"-"+i}><span className="ow-action-n">{i+1}</span><div><h3>{a.title||"Action"}</h3><p>{a.body||a.detail||String(a)}</p></div></div>})}</div></Section>;
}

function DetailRows({items=[]}){
  if(!items.length)return <Empty title="No additional business detail is available for this period."/>;
  return <div className="ow-rows">{items.map(function(x,i){return <Row key={(x.title||i)+"-"+i} tone="violet" title={x.title||"Business area"} body={x.body||""} meta={[x.status||null,x.takeaway||null]}/>})}</div>;
}

// Governed business detail for the Business view: floors, tables, hours, weekdays and observed service time.
// Every figure is read from wl_restaurant_day / wl_restaurant_period; missing rows stay absent, never zero.
function BusinessDetail({view,day,period,businessDay,businessPeriod,model}){
  const isPeriod=model.kind==="period";
  const data=isPeriod?(period||{}):(day||{});
  const floors=isPeriod?(data.floor_profile||[]):(data.floors||[]).filter(function(f){return Number(f.samples||0)>0});
  const tables=isPeriod?(data.table_profile||[]):(data.tables||[]).filter(function(t){return Number(t.samples||0)>0});
  const ranked=tables.filter(function(t){return num(t.occupancy_pct)!=null}).slice().sort(function(a,b){return Number(b.occupancy_pct)-Number(a.occupancy_pct)});
  const hours=isPeriod?(data.hour_profile||[]).filter(function(h){return num(h.avg_peak_visible_diners)!=null}):[];
  const weekdays=view==="monthly"?(data.weekday_profile||[]):[];
  const dist=isPeriod?(data.service_time_distribution||{}):{};
  const distRows=[["Under 15 min",dist.under_15_minutes],["15–30 min",dist["15_to_30_minutes"]],["30–45 min",dist["30_to_45_minutes"]],["45+ min",dist["45_plus_minutes"]]].filter(function(r){return num(r[1])!=null});
  const business=isPeriod?(businessPeriod||{}):(businessDay||{});
  const ready=businessReady(business);
  const bm=business.metrics||business.summary||{};
  const party=bm.party_size_mix||{};
  const partyRows=[
    {label:"1 person",value:num(party.one_person)},
    {label:"2 people",value:num(party.two_people)},
    {label:"3–4 people",value:num(party.three_to_four)},
    {label:"5+ people",value:num(party.five_plus)}
  ].filter(function(x){return x.value!=null});
  const starts=(bm.session_starts_by_period||business.session_starts_by_period||[]).filter(function(x){return num(x.sessions)!=null});
  const turnover=(bm.table_turnover||business.table_turnover||[]).filter(function(x){return num(x.sessions)!=null});
  if(isPeriod&&!model.trendReady)return null;
  return <>
    {isPeriod&&<Section title={model.days===7?"7-day operations review":"30-day management review"} note={model.observed+" of "+model.days+" service days represented; business figures come from those days only"}/>}
    {!isPeriod&&businessDay&&<Section title="Dining covers & party mix" note={ready?(business.cover_scope==="full_service_day"?"Estimated across the supported full service day":"Estimated for the represented service period"):"Site-wide session totals stay unavailable until the dining-table views are safely reconciled"}>
      {ready?<><Metrics items={[
        {value:metricValue(bm.estimated_covers),label:business.cover_scope==="full_service_day"?"Estimated dining covers":"Estimated covers in represented period",note:sampleNote(bm.cover_sample_sessions)},
        {value:metricValue(bm.estimated_table_sessions),label:"Estimated table sessions",note:sampleNote(bm.session_sample_size)},
        {value:metricValue(bm.average_party_size),label:"Average party size",note:sampleNote(bm.party_size_sample_sessions)},
        {value:metricValue(bm.largest_visible_party),label:"Largest visible party",note:"joined-table parties reconciled where configured"}
      ]}/>
      {partyRows.length>0&&<div style={{marginTop:16}}><Bars height={100} question="What party sizes made up the represented dining demand?" series={partyRows.map(function(x){return {label:x.label,value:x.value}})}/></div>}
      </>:<Empty title="A reliable site-wide cover estimate is not available yet.">{business.reconciliation?.reason||"The dining views need a confirmed physical-table mapping before WatchLog can combine table sessions without double-counting."}</Empty>}
    </Section>}
    {!isPeriod&&ready&&starts.length>0&&<Section title="New table sessions" note="When defensible new dining sessions began; this is not entrance footfall">
      <Bars height={105} series={starts.map(function(x){return {label:x.period||x.local_hour||"Period",value:Number(x.sessions)}})}/>
    </Section>}
    {!isPeriod&&ready&&<Section title="Service responsiveness" note="Observed table-session timings; every figure shows the qualifying sample size">
      <Metrics items={[
        {value:metricValue(bm.median_time_to_first_service_minutes," min"),label:"Median time to first visible service",note:sampleNote(bm.first_service_sample_sessions)},
        {value:metricValue(bm.median_time_to_first_served_items_minutes," min"),label:"Median time to visible served items",note:sampleNote(bm.served_items_time_sample_sessions)},
        {value:metricValue(bm.median_minimum_observed_dwell_minutes," min"),label:"Median minimum dwell",note:sampleNote(bm.dwell_sample_sessions)},
        {value:num(bm.served_session_rate_pct)==null?null:bm.served_session_rate_pct+"%",label:"Served-session rate",note:sampleNote(bm.served_rate_sample_sessions)}
      ]}/>
      {bm.service_slowdown_vs_demand&&<p className="ow-muted" style={{fontSize:12,marginTop:8}}>{bm.service_slowdown_vs_demand}</p>}
    </Section>}
    {!isPeriod&&ready&&turnover.length>0&&<Section title="Table turnover" note="Defensible table sessions by calibrated physical table/zone">
      <HBars items={turnover.slice(0,12).map(function(x,i){return {key:x.physical_table_key||x.table_key||String(i),label:x.label||x.physical_table_key||x.table_key||"Table",note:x.utilization_pct==null?"":x.utilization_pct+"% occupied in valid observations",value:Number(x.sessions)}})}/>
    </Section>}
    {floors.length>0&&<Section title="Floor comparison" note="Peak visible diners by dining floor; keep floors separate before reading site totals">
      <HBars items={floors.map(function(f,i){return {key:(f.camera_id||f.floor||i)+"",label:f.floor||"Dining floor",note:"Peak occupied tables "+val(f.peak_occupied_tables)+" · avg visible "+val(isPeriod?f.avg_visible_diners:f.avg_visible_customers),value:isPeriod?f.peak_visible_diners:f.peak_visible_customers}})}/>
    </Section>}
    {ranked.length>0&&<Section title="Table utilization" note="Share of valid observations in which each calibrated table was occupied">
      <div className="ow-grid2">
        <div><div className="ow-label" style={{marginBottom:8}}>Most-used calibrated tables</div><HBars items={ranked.slice(0,8).map(function(t){return {key:t.table_key,label:t.label||t.table_key,note:"Peak visible party "+val(t.peak_party),value:Number(t.occupancy_pct)}})}/></div>
        {ranked.length>3&&<div><div className="ow-label" style={{marginBottom:8}}>Lower-utilization tables</div><HBars items={ranked.slice(-5).reverse().map(function(t){return {key:t.table_key+"-low",label:t.label||t.table_key,note:isPeriod?val(t.observed_days)+" observed days":"",value:Number(t.occupancy_pct)}})}/></div>}
      </div>
      <p className="ow-muted" style={{fontSize:12,marginTop:8}}>Percent of valid observations; useful for layout review only when coverage was adequate and table anchors stayed visible.</p>
    </Section>}
    {hours.length>0&&<Section title="Demand by hour" note="Average peak visible diners across represented service days">
      <Bars height={110} series={hours.map(function(h){return {label:String(h.local_hour).replace(/:00$/,""),value:Number(h.avg_peak_visible_diners),display:val(h.avg_peak_visible_diners)}})}/>
    </Section>}
    {model.weekdayReady&&weekdays.length>0&&<Section title="Weekday pattern" note="Average estimated covers by day of week, represented days only">
      <Bars height={110} series={weekdays.map(function(w){return {label:w.weekday,value:Number(w.avg_estimated_covers||0),gap:!Number(w.observed_days||0),display:val(w.avg_estimated_covers)}})} legend={<><span>Avg estimated covers</span><span className="gap">No represented day</span></>}/>
    </Section>}
    {distRows.length>0&&<Section title="Observed service-time distribution" note="Seated/occupied to first food visible; not POS ticket time">
      <Bars height={100} series={distRows.map(function(r){return {label:r[0],value:Number(r[1]||0)}})}/>
      <p className="ow-muted" style={{fontSize:12,marginTop:8}}>{val(dist.sample_sessions)} table sessions had a defensible observed time-to-food measurement.</p>
    </Section>}
  </>;
}

function SavedReports({windowData,siteId,limit=8,rail}){
  const all=windowData&&windowData.saved_reports||[];
  const rows=all.slice(0,limit),extra=all.slice(limit);
  if(!rows.length)return null;
  if(rail)return <RailSection label="Completed service days">{rows.slice(0,5).map(function(x,i){const d=String(x.service_date||"");return <a className="ow-rail-link" key={(x.report_id||d||i)+"-"+i} href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}><span>{shortDate(d)} <small>{weekdayLabel(d)}</small></span><i>Open</i></a>})}</RailSection>;
  const renderRow=function(x,i,prefix){const d=String(x.service_date||"");const highlights=x.highlights||[];return <Row compact key={(prefix||"row")+"-"+(x.report_id||d||i)+"-"+i} tone="verified" title={shortDate(d)+" · "+weekdayLabel(d)} body={x.summary||highlights[0]||"Completed daily report"} action={<a href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}>Open</a>}/>};
  return <Section title="Completed service days" count={all.length}>
    <div className="ow-rows">{rows.map(function(x,i){return renderRow(x,i,"primary")})}</div>
    {extra.length>0&&<details className="ow-details savedReportsMore"><summary>Show {extra.length} more completed report{extra.length===1?"":"s"}</summary><div className="ow-rows">{extra.map(function(x,i){return renderRow(x,i,"extra")})}</div></details>}
  </Section>;
}

function ConfidenceDetails({coverage,visibility=[],sufficiency}){
  const hasCoverage=coverage&&(coverage.summary||coverage.note||coverage.period||coverage.status);
  if(!hasCoverage&&!visibility.length&&!sufficiency)return null;
  return <div style={{marginTop:18}}>
    {sufficiency&&<details className="ow-details" open={sufficiency.level==="limited"}><summary>Report confidence</summary><div style={{fontSize:13}}>{sufficiency.message}</div></details>}
    {hasCoverage&&<details className="ow-details"><summary>Coverage and figure limits</summary><div style={{fontSize:13}}>{coverage.status&&<b>{coverage.status+". "}</b>}{coverage.summary||""} {coverage.note||""}<p style={{marginTop:6}}>Visible diners are concurrent visible people, not unique footfall; estimated covers and sessions are estimates; observed time to food is not POS order-to-serve time and these figures are not POS data. Missing observation periods are missing coverage, not zero activity. Period-to-period changes should only be acted on when coverage is sufficiently comparable.</p></div></details>}
    {visibility.length>0&&<details className="ow-details"><summary>Visibility improvements</summary><div style={{fontSize:13}}>{visibility.map(function(x,i){return <p key={i} style={{marginBottom:6}}><b>{x.title||"Improvement"}:</b> {x.body||x.recommendation||""}</p>})}</div></details>}
  </div>;
}

function normalizeDaily({view,day,businessDay,securityDay,snapshot,requestedReportDate}){
  const p=snapshot&&snapshot.payload||{};
  if(view==="yesterday"&&p.manual_business_report===true){
    return {
      kind:"saved",
      date:p.report_date||snapshot.report_date,
      eyebrow:requestedReportDate?"Completed daily report":"Yesterday",
      headline:requestedReportDate?"Completed service day":"Yesterday in one view",
      summary:p.narrative_summary||p.ai_summary||p.executive_summary||"",
      highlights:p.highlights||[],
      metrics:(p.metrics||[]).slice(0,4),
      timeline:p.demand_timeline||[],
      bars:(p.demand_timeline||[]).filter(function(x){return x&&x.time}).map(function(x){return {label:x.time,value:Number(x.level||0),title:x.time+": "+(x.label||"relative demand")}}),
      barsRelative:true,
      security:p.incidents||[],
      operations:p.operations||[],
      actions:p.action_items||p.priority_actions||[],
      coverage:p.coverage||{},
      visibility:p.visibility_notes||[],
      reportId:snapshot.report_id||null,
      sufficiency:{level:"good",message:"This completed daily report is based on the available service-day coverage. Any missing time remains unknown rather than being treated as zero activity."}
    };
  }

  const data=day||{},q=data.data_quality||{},sessions=data.sessions||{},business=businessDay||{},businessMetrics=business.metrics||{},sessionReady=businessReady(business);
  const hourly=(data.hourly||[]).filter(function(x){return Number(x.samples||0)>0});
  const peakVisible=hourly.reduce(function(m,x){return Math.max(m,Number(x.peak_visible_customers||0))},0);
  const peakTables=hourly.reduce(function(m,x){return Math.max(m,Number(x.peak_occupied_tables||0))},0);
  const busiest=hourly.reduce(function(best,x){return Number(x.peak_visible_customers||0)>Number(best&&best.peak_visible_customers||-1)?x:best},null);
  const maxPeak=Math.max.apply(null,[1].concat(hourly.map(function(x){return Number(x.peak_visible_customers||0)})));
  const hasBusiness=Number(q.camera_observations||0)>0||Number(q.table_observations||0)>0;
  const timeline=hourly.map(function(x){return {time:x.local_hour||"",level:clampLevel(x.peak_visible_customers,maxPeak),label:x.peak_visible_customers==null?"Activity":"Peak visible diners "+x.peak_visible_customers,detail:x.peak_occupied_tables==null?"":"Peak occupied tables "+x.peak_occupied_tables}});
  const bars=(data.hourly||[]).map(function(x){const seen=Number(x.samples||0)>0;return {label:String(x.local_hour||"").replace(/:00$/,""),value:seen?Number(x.peak_visible_customers||0):0,gap:!seen,title:(x.local_hour||"")+": "+(seen?"peak visible diners "+val(x.peak_visible_customers):"not observed")}});
  const metrics=hasBusiness?[
    sessionReady?{value:val(businessMetrics.estimated_covers),label:business.cover_scope==="full_service_day"?"Estimated dining covers":"Estimated covers in represented period",note:sampleNote(businessMetrics.cover_sample_sessions)}:{value:null,label:"Estimated covers",note:"Unavailable until dining sessions are physically reconciled"},
    sessionReady?{value:val(businessMetrics.estimated_table_sessions),label:"Estimated table sessions",note:sampleNote(businessMetrics.session_sample_size)}:{value:null,label:"Table sessions",note:"Unavailable until dining sessions are physically reconciled"},
    sessionReady?{value:val(businessMetrics.average_party_size),label:"Average party size",note:sampleNote(businessMetrics.party_size_sample_sessions)}:{value:val(peakTables),label:"Peak occupied tables",note:"Highest simultaneous table use"},
    {value:val(peakVisible),label:"Peak visible diners",note:"Concurrent visible diners, not unique footfall"}
  ]:[];
  const highlights=[];
  if(busiest&&busiest.local_hour)highlights.push("The strongest visible demand was around "+busiest.local_hour+".");
  if(sessionReady&&num(businessMetrics.estimated_covers)!=null)highlights.push("The available evidence supports an estimated "+businessMetrics.estimated_covers+" dining covers "+(business.cover_scope==="full_service_day"?"for the supported service day.":"in the represented service period.") );
  if(sessionReady&&num(businessMetrics.median_time_to_first_service_minutes)!=null)highlights.push("Median time to the first visible table-service interaction was about "+businessMetrics.median_time_to_first_service_minutes+" minutes across "+businessMetrics.first_service_sample_sessions+" qualifying sessions.");
  const operations=[];
  if(hasBusiness)operations.push({title:"Dining",status:busiest&&busiest.local_hour||"Observed",body:busiest&&busiest.local_hour?"Dining demand was strongest around "+busiest.local_hour+".":"Dining activity was visible during the covered period."});
  const peakKitchen=Math.max.apply(null,[0].concat(hourly.map(function(x){return Number(x.avg_kitchen_load||0)})));
  const peakHandoff=Math.max.apply(null,[0].concat(hourly.map(function(x){return Number(x.avg_handoff_load||0)})));
  if(peakHandoff>0)operations.push({title:"Service handoff",status:"Observed",body:"Handoff pressure reached its strongest visible level at about "+Math.round(peakHandoff*100)+"% of the period scale."});
  if(peakKitchen>0)operations.push({title:"Kitchen",status:"Observed",body:"Kitchen pressure reached its strongest visible level at about "+Math.round(peakKitchen*100)+"% of the period scale."});
  const coverageRatio=num(q.business_analytics_coverage_ratio);
  const summary=hasBusiness
    ?(view==="daily"?"Today so far":"This service day")+" shows "+(busiest&&busiest.local_hour?"the strongest visible demand around "+busiest.local_hour:"measurable dining activity")+(peakVisible?", reaching about "+peakVisible+" visible diners at the busiest point":"")+". Figures remain limited to the periods with usable coverage."
    :view==="daily"
      ?"Today’s service is still in progress, but there is not enough reliable coverage yet for a management-level demand or table-use conclusion."
      :"A completed management report is not available for this service day yet, so WatchLog is not presenting unsupported business figures.";
  const visibility=(data.analytics_quality&&data.analytics_quality.recommendations||[]).map(function(x){return {title:x.camera?x.camera+": "+(x.issue||"Visibility"):(x.issue||"Visibility"),body:x.recommendation||x.evidence||""}});
  return {
    kind:"live",
    date:data.service_date||requestedReportDate||snapshot&&snapshot.report_date||null,
    eyebrow:view==="daily"?"Today · in progress":requestedReportDate?"Selected service day":"Yesterday",
    headline:view==="daily"?"Today in one view":"Service day in one view",
    summary:summary,highlights:highlights,metrics:metrics,timeline:timeline,bars:bars,
    security:(p.incidents&&p.incidents.length?p.incidents:(securityDay&&securityDay.incidents||[])),operations:operations,actions:p.action_items||p.priority_actions||[],
    coverage:{status:coverageRatio==null?"Coverage not yet rated":pct(coverageRatio)+" service-window coverage",summary:"Figures reflect only the periods with enough visibility to support them.",note:"Missing periods are unknown, not zero activity."},
    visibility:visibility,reportId:snapshot&&snapshot.report_id||null,business:business,
    sufficiency:{level:hasBusiness?"good":"limited",message:hasBusiness?"The report shows only figures that can be supported for the represented service period.":"There is not enough comparable business coverage yet for a complete daily management view."}
  };
}

function normalizePeriod({view,period,businessPeriod,windowData}){
  const days=view==="week"?7:30,data=period||windowData&&windowData.structured_restaurant_metrics||{},business=businessPeriod||{},businessSummary=business.summary||{},businessDaily=business.daily||[],s=data.summary||{},daily=data.daily||[],weeks=data.weekly_trend||[],saved=windowData&&windowData.saved_reports||[];
  const observed=Number(s.observed_service_days||0),previousObserved=Number(data.previous_period&&data.previous_period.observed_service_days||0),minimum=days===7?4:10;
  const businessObserved=Number(businessSummary.reconciled_service_days||0);
  const businessTrendReady=business.enabled===true&&businessObserved>=minimum;
  // Site-wide cover/session trends come only from the reconciled physical-table contract.
  // The older restaurant-period estimated_covers can double-count overlapping dining views,
  // so it remains available only for legacy drill-down data and is never charted as a site total here.
  const trendReady=businessTrendReady,comparisonReady=false,weekdayReady=false;
  const busiestDay=s.busiest_day||null,busiestHour=s.busiest_hour||null;
  const safeDays=businessDaily.filter(function(x){return x&&x.reconciliation_ready===true&&num(x.estimated_covers)!=null});
  const maxTrend=Math.max.apply(null,[1].concat(safeDays.map(function(x){return Number(x.estimated_covers||0)})));
  const timeline=trendReady?safeDays.map(function(x){return {time:shortDate(x.service_date),level:clampLevel(x.estimated_covers,maxTrend),label:"Estimated covers "+x.estimated_covers,detail:val(x.table_sessions)+" qualifying table sessions"}}):[];
  const bars=business.enabled===true?businessDaily.map(function(x){const seen=x&&x.reconciliation_ready===true&&num(x.estimated_covers)!=null;return {label:weekdayLabel(x.service_date),value:seen?Number(x.estimated_covers):0,gap:!seen,title:shortDate(x.service_date)+": "+(seen?val(x.estimated_covers)+" estimated covers":"session totals unavailable")}}):[];
  const coverDelta=null;
  const metrics=businessTrendReady?[
    {value:val(businessSummary.avg_estimated_covers_per_reconciled_day),label:"Avg estimated covers",note:"Per reconciled represented service day"},
    {value:val(businessSummary.avg_table_sessions_per_reconciled_day),label:"Avg table sessions",note:businessObserved+" reconciled service days"},
    {value:metricValue(businessSummary.average_party_size),label:"Average party size",note:sampleNote(businessSummary.party_size_sample_sessions)},
    {value:metricValue(businessSummary.median_time_to_first_service_minutes," min"),label:"Median time to first visible service",note:sampleNote(businessSummary.first_service_sample_sessions)}
  ]:trendReady?[
    {value:busiestHour&&busiestHour.local_hour||"—",label:"Busiest time",note:"Strongest recurring visible demand"},
    {value:val(observed),label:"Represented service days",note:"Session totals withheld until physical-table reconciliation is available"}
  ]:[];
  const security=[];
  saved.forEach(function(r){(r.incidents||[]).forEach(function(x){security.push(Object.assign({},x,{service_date:r.service_date}))})});
  const operations=[];
  if(trendReady&&safeDays.length){
    const top=safeDays.slice().sort(function(a,b){return Number(b.estimated_covers||0)-Number(a.estimated_covers||0)})[0];
    if(top)operations.push({title:"Strongest reconciled service day",status:shortDate(top.service_date),body:"This was the highest estimated-cover day among the physically reconciled service days in this period."});
  }
  if(businessTrendReady&&num(businessSummary.median_time_to_first_service_minutes)!=null)operations.push({title:"Service responsiveness",status:businessSummary.median_time_to_first_service_minutes+" min",body:"Median seating-to-first-visible-service timing across "+businessSummary.first_service_sample_sessions+" qualifying sessions."});
  const actions=[],seen=new Set();
  saved.forEach(function(r){(r.action_items||[]).forEach(function(a){const key=a.id||a.title||a.body;if(key&&!seen.has(key)){seen.add(key);actions.push(Object.assign({},a,{report_id:r.report_id}))}})});
  const summary=trendReady
    ?(days===7?"The last 7 days":"The last 30 days")+" contain "+businessObserved+" physically reconciled service days, enough to show a covers/session trend without adding overlapping dining views."
    :business.enabled===true
      ?"Only "+businessObserved+" of "+days+" service days currently have reconciled table-session figures. Completed daily reports remain available; missing or unreconciled days are unknown, not zero demand."
      :"Restaurant session reconciliation is not available yet for this reporting period, so WatchLog is withholding site-wide cover/session trends rather than using potentially duplicated camera totals.";
  const highlights=trendReady?[
    businessSummary.avg_estimated_covers_per_reconciled_day!=null?"Average estimated covers were "+businessSummary.avg_estimated_covers_per_reconciled_day+" per reconciled service day.":null,
    businessSummary.average_party_size!=null?"Average visible party size was "+businessSummary.average_party_size+" across "+businessSummary.party_size_sample_sessions+" qualifying sessions.":null,
    businessSummary.median_time_to_first_service_minutes!=null?"Median time to first visible table-service interaction was "+businessSummary.median_time_to_first_service_minutes+" minutes across "+businessSummary.first_service_sample_sessions+" qualifying sessions.":null
  ].filter(Boolean):[
    (business.enabled===true?businessObserved:0)+" of "+days+" service days currently support reconciled cover/session figures.",
    saved.length+" completed daily report"+(saved.length===1?" is":"s are")+" available in this period.",
    "Missing or unreconciled days are treated as unknown, not as zero demand."
  ];
  const coverage={status:(business.enabled===true?businessObserved:0)+" of "+days+" reconciled days",summary:trendReady?"Enough reconciled service days exist for a period-level covers/session view.":"The period is still too sparse or unreconciled for a site-wide covers/session trend.",note:"Missing or unreconciled days remain unknown and are excluded from business totals."};
  const sufficiency={level:trendReady?"good":"limited",message:trendReady?"This "+days+"-day view uses "+businessObserved+" physically reconciled service days.":"A "+days+"-day covers/session trend requires at least "+minimum+" reconciled service days. "+(business.enabled===true?businessObserved:0)+" are currently available, so site-wide session totals are withheld."};
  return {kind:"period",date:null,period:business.period||data.period||windowData&&windowData.period||{},eyebrow:days===7?"Last 7 days":"Last 30 days",headline:days===7?"The week in one view":"The month in one view",summary:summary,highlights:highlights,metrics:metrics,timeline:timeline,bars:bars,security:security,operations:operations,actions:actions,coverage:coverage,visibility:[],reportId:null,sufficiency:sufficiency,trendReady:trendReady,comparisonReady:comparisonReady,weekdayReady:weekdayReady,observed:business.enabled===true?businessObserved:0,days:days,minimum:minimum,business:business};
}

function firstSentences(text,n){const t=String(text||"").replace(/\s+/g," ").trim();const parts=t.match(/[^.!?]+[.!?]+(\s|$)/g);return parts?parts.slice(0,n).join("").trim():t}

export default function UnifiedRestaurantReport({view,day,businessDay,securityDay,period,businessPeriod,windowData,snapshot,siteId,requestedReportDate,renderActions,askPrompt}){
  const[mode,setMode]=useState("overview");
  const model=useMemo(function(){return view==="week"||view==="monthly"?normalizePeriod({view:view,period:period,businessPeriod:businessPeriod,windowData:windowData}):normalizeDaily({view:view,day:day,businessDay:businessDay,securityDay:securityDay,snapshot:snapshot,requestedReportDate:requestedReportDate})},[view,day,businessDay,securityDay,period,businessPeriod,windowData,snapshot,requestedReportDate]);
  const periodText=model.date?dateLabel(model.date):model.period&&model.period.start_service_date&&model.period.end_service_date?shortDate(model.period.start_service_date)+" – "+shortDate(model.period.end_service_date):periodName(view);
  const actionBlock=model.actions&&model.actions.length?(renderActions?renderActions(model.actions,model.reportId):<ActionList items={model.actions}/>):null;
  const chartTitle=view==="daily"?"When were diners most visible today?":view==="yesterday"?"When was demand strongest through the service day?":view==="week"?"Which service days were busiest?":"How did weekly demand move across the month?";
  const showBuild=model.kind==="period"&&!model.trendReady;
  const security=model.security||[];
  const critical=security.filter(function(x){return x&&x.severity==="critical"}).length;
  const attention=security.filter(function(x){return x&&x.severity==="attention"}).length;
  const limited=model.sufficiency&&model.sufficiency.level==="limited";
  const leadTitle=(critical?critical+" critical security item"+(critical===1?"":"s"):attention?attention+" security item"+(attention===1?" needs":"s need")+" review":"No security exception recorded")+" · "+(limited?"limited coverage":model.kind==="period"?model.observed+" of "+model.days+" days represented":(model.coverage&&model.coverage.status)||"coverage available");
  const leadTone=critical?"bad":attention?"warn":limited?"unknown":"ok";
  const lead=firstSentences(model.summary,2);
  const asks=[
    ["Explain this report",askPrompt||"Explain this restaurant report and the priority action."],
    ["What should management act on?","From this restaurant report, what should management act on first, and why?"],
    ["What could not be verified?","For this restaurant reporting period, what time could WatchLog not verify, and does it change the conclusion?"],
  ];

  const rail=<>
    <RailSection label="Report status">
      <div className="ow-stat"><span>Status</span><b><ReportStatus view={view} model={model}/></b></div>
      <div className="ow-stat"><span>Period</span><b>{periodText}</b></div>
    </RailSection>
    <ReportHealth view={view} model={model}/>
    {model.kind!=="period"&&day&&day.data_quality&&<RailSection label="Business figures"><Stat label="Analytics coverage" note="share of the service window able to support diner and table figures" value={day.data_quality.business_analytics_coverage_ratio==null?null:pct(day.data_quality.business_analytics_coverage_ratio)}/></RailSection>}
    {model.kind==="period"&&<SavedReports windowData={windowData} siteId={siteId} rail/>}
    <RailSection label="Ask WatchLog about this report">
      <div className="ow-ask">{asks.map(function(a){return <a key={a[0]} href={withSite("/ai/?prompt="+encodeURIComponent(a[1]),siteId)}>{a[0]}</a>})}</div>
    </RailSection>
  </>;

  return <div className="ow-body has-rail" data-part="reportMainGridV4">
    <div className="ow-work">
      <div data-part="reportControlBarV4" style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:16,flexWrap:"wrap"}}>
        <ReportTabs mode={mode} setMode={setMode}/>
      </div>
      <Summary items={[{value:periodText,label:view==="daily"?"Current service day":model.kind==="period"?"Management period":"Completed service day"},{value:limited?"Limited":"Supported",label:"Report confidence",muted:limited}]}/>

      <div data-part="reportHeroV4"><Lead tone={leadTone} title={leadTitle} body={lead}/></div>

      {mode==="overview"&&<>
        {model.metrics&&model.metrics.length>0&&<Metrics items={model.metrics.map(function(m,i){return {value:m.value==="—"?null:m.value,label:m.label,note:(m.delta?m.delta+" vs prior · ":"")+(m.note||""),primary:i===0,unknown:"Not available"}})}/>}
        <div style={{marginTop:20}}>{showBuild?<PeriodBuildState model={model} windowData={windowData} siteId={siteId}/>:<Section first title={view==="week"?"Service-day trend":view==="monthly"?"Weekly demand":"Demand pattern"}><DemandBars model={model} title={chartTitle}/></Section>}</div>
        {model.highlights&&model.highlights.length>0&&<Section title="What management should know"><Findings items={model.highlights.slice(0,3).map(function(x){return {title:x}})}/></Section>}
        <SecurityPanel items={security} coveredDays={model.kind==="period"?model.observed:null}/>
        {actionBlock}
        {view==="yesterday"&&!snapshot&&<SavedReports windowData={windowData} siteId={siteId} limit={1}/>}
        {model.visibility&&model.visibility.length>0&&<ConfidenceDetails coverage={null} visibility={model.visibility} sufficiency={null}/>}
      </>}

      {mode==="business"&&<>
        {model.metrics&&model.metrics.length>0&&<Metrics items={model.metrics.map(function(m,i){return {value:m.value==="—"?null:m.value,label:m.label,note:m.note,primary:i===0,unknown:"Not available"}})}/>}
        <div style={{marginTop:20}}>{showBuild?<PeriodBuildState model={model} windowData={windowData} siteId={siteId}/>:<Section first title={view==="week"?"Service-day trend":view==="monthly"?"Weekly demand pattern":"Demand pattern"}><DemandBars model={model} title={chartTitle}/></Section>}</div>
        <Section title="Business signals"><DetailRows items={model.operations||[]}/></Section>
        <BusinessDetail view={view} day={day} period={period||(windowData&&windowData.structured_restaurant_metrics)} businessDay={businessDay} businessPeriod={businessPeriod} model={model}/>
        {actionBlock}
        {model.kind==="period"&&<SavedReports windowData={windowData} siteId={siteId} limit={view==="week"?7:6}/>}
        <ConfidenceDetails coverage={model.coverage} visibility={model.visibility} sufficiency={model.sufficiency}/>
      </>}

      {mode==="security"&&<>
        <SecurityPanel items={security} coveredDays={model.kind==="period"?model.observed:null}/>
        <p className="ow-muted" style={{fontSize:12.5,marginTop:10}}>Routine movement stays routine. Missing coverage is never treated as proof that nothing happened.</p>
        {model.kind==="period"&&<SavedReports windowData={windowData} siteId={siteId} limit={view==="week"?7:6}/>}
        {model.visibility&&model.visibility.length>0&&<ConfidenceDetails coverage={null} visibility={model.visibility} sufficiency={null}/>}
      </>}
    </div>
    <aside className="ow-rail" aria-label="Report intelligence">{rail}</aside>
  </div>;
}
