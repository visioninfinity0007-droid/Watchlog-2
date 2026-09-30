"use client";

import {useMemo,useState} from "react";
import {withSite} from "../site-context";
import styles from "./reports.module.css";

function num(v){return v==null||Number.isNaN(Number(v))?null:Number(v)}
function val(v,suffix=""){return v==null?"—":String(v)+suffix}
function pct(v){const n=num(v);return n==null?"—":Math.round(n*100)+"%"}
function shortDate(v){if(!v)return"—";const d=new Date(v+"T12:00:00");return d.toLocaleDateString([], {month:"short",day:"numeric"})}
function dateLabel(v){if(!v)return"—";const d=new Date(v+"T12:00:00");return d.toLocaleDateString([], {weekday:"long",month:"long",day:"numeric",year:"numeric"})}
function clampLevel(v,max){const n=num(v),m=num(max);if(n==null||!m)return 0;return Math.max(0,Math.min(4,Math.round((n/m)*4)))}
function periodName(view){return view==="daily"?"Today":view==="yesterday"?"Yesterday":view==="week"?"Last 7 days":"Last 30 days"}

function ReportTabs({mode,setMode}){
  return <div className={styles.reportModeTabs} role="tablist" aria-label="Report sections">
    {[["overview","Overview"],["business","Business"],["security","Security"]].map(function(row){const k=row[0],l=row[1];return <button key={k} type="button" role="tab" aria-selected={mode===k} className={mode===k?styles.reportModeActive:""} onClick={function(){setMode(k)}}>{l}</button>})}
  </div>;
}

function ReportStatus({view,model}){
  const limited=model&&model.sufficiency&&model.sufficiency.level==="limited";
  const label=view==="daily"?"In progress":limited?"Limited coverage":"Completed";
  const cls=view==="daily"?styles.reportStatusLive:limited?styles.reportStatusBuilding:styles.reportStatusReady;
  return <span className={styles.reportStatus+" "+cls}><i/>{label}</span>;
}

function ReportHealth({view,model}){
  const period=model&&model.kind==="period";
  const current=period?Number(model.observed||0):null;
  const total=period?Number(model.days||0):null;
  const ratio=period&&total?Math.max(0,Math.min(100,Math.round(current/total*100))):null;
  return <section className={styles.reportHealthCard}>
    <div className={styles.reportHealthHead}><span className={styles.panelEyebrow}>Report confidence</span><b>{period?(current+" of "+total+" days represented"):(model.coverage&&model.coverage.status||"Coverage available")}</b></div>
    {period&&<div className={styles.reportHealthProgress}><span style={{width:String(ratio)+"%"}}/></div>}
    <p>{model.sufficiency&&model.sufficiency.message||model.coverage&&model.coverage.summary||"Figures are limited to the periods that can be supported reliably."}</p>
    {model.coverage&&model.coverage.note&&<small>{model.coverage.note}</small>}
  </section>;
}

function PeriodBuildState({model,windowData,siteId}){
  const rows=windowData&&windowData.saved_reports||[];
  const minimum=model.days===7?4:10;
  const pctReady=Math.max(0,Math.min(100,Math.round((Number(model.observed||0)/minimum)*100)));
  return <section className={styles.periodBuildCard}>
    <div className={styles.periodBuildTop}><div><span className={styles.panelEyebrow}>Period is still building</span><h3>{model.observed} represented day{Number(model.observed)===1?"":"s"} so far</h3><p>WatchLog will unlock the period trend when enough completed service days are represented. Daily reports are still available below.</p></div><strong>{pctReady}%</strong></div>
    <div className={styles.periodBuildTrack}><span style={{width:String(pctReady)+"%"}}/></div>
    <div className={styles.periodBuildDates}>{rows.slice(0,6).map(function(r,i){const d=String(r.service_date||"");return <a key={(r.report_id||d||i)+"-"+i} href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}><b>{shortDate(d)}</b><span>Completed</span></a>})}</div>
  </section>;
}

function ReportIcon({kind}){
  if(kind==="users")return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="9" r="3"/><path d="M3.8 18c.7-3 2.4-4.5 5.2-4.5S13.5 15 14.2 18M15 7.4a2.7 2.7 0 0 1 0 5.2M16.2 13.9c2.1.5 3.4 1.9 4 4.1"/></svg>;
  if(kind==="table")return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 8.5h14M7 8.5v8M17 8.5v8M4 16.5h16"/></svg>;
  if(kind==="clock")return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.7v4.7l3.1 1.8"/></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17.5 9 12l3.2 3.2L20 7.5M15.5 7.5H20V12"/></svg>;
}

function KpiStrip({metrics=[]}){
  const icons=["users","table","trend","clock"];
  if(!metrics.length)return null;
  return <div className={styles.kpiStrip}>{metrics.slice(0,4).map(function(m,i){const delta=m.delta;return <div className={styles.kpiCell} key={(m.label||"metric")+"-"+i}><span className={styles.kpiIcon}><ReportIcon kind={icons[i]}/></span><div><div className={styles.kpiValueRow}><strong>{m.value}</strong>{delta&&<em className={String(delta).startsWith("-")?styles.kpiDeltaDown:styles.kpiDeltaUp}>{delta}</em>}</div><b>{m.label}</b>{m.note&&<small>{m.note}</small>}</div></div>})}</div>;
}

function DemandChart({points=[],title="Demand pattern",subtitle="How visible demand changed during the period."}){
  const rows=(points||[]).filter(function(x){return x&&x.time});
  const[active,setActive]=useState(0);
  if(!rows.length)return <section className={styles.restaurantPanel}><div className={styles.sectionHead}><div><h3>{title}</h3><p>{subtitle}</p></div></div><div className={styles.restaurantEmpty}>There is not enough comparable activity to show this trend yet.</div></section>;
  const W=760,H=244,left=34,right=18,top=22,bottom=42,plotW=W-left-right,plotH=H-top-bottom;
  const x=function(i){return left+(rows.length===1?plotW/2:(plotW*i/(rows.length-1)))};
  const y=function(level){return top+plotH-(Math.max(0,Math.min(4,Number(level||0)))/4)*plotH};
  const pts=rows.map(function(p,i){return[x(i),y(p.level)]});
  const line=pts.map(function(p,i){return(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)}).join(" ");
  const area=line+" L "+x(rows.length-1).toFixed(1)+" "+(top+plotH)+" L "+x(0).toFixed(1)+" "+(top+plotH)+" Z";
  const selected=rows[Math.min(active,rows.length-1)]||rows[0];
  return <div className={styles.demandViz}>
    <div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Business</span><h3>{title}</h3><p>{subtitle}</p></div><span className={styles.dataBoundary}>Relative demand</span></div>
    <div className={styles.chartFrame}>
      <svg className={styles.areaChart} viewBox={"0 0 "+W+" "+H} role="img" aria-label={title}>
        <defs><linearGradient id="chaiDemandFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".22"/><stop offset="100%" stopColor="currentColor" stopOpacity=".02"/></linearGradient></defs>
        {[0,1,2,3,4].map(function(i){const gy=top+plotH-(i/4)*plotH;return <line key={i} x1={left} x2={W-right} y1={gy} y2={gy} className={styles.chartGrid}/>})}
        <path d={area} className={styles.chartArea}/>
        <path d={line} className={styles.chartLine}/>
        {pts.map(function(p,i){return <g key={(rows[i].time||i)+"-"+i} role="button" tabIndex="0" aria-label={rows[i].time+": "+(rows[i].label||"demand point")} onClick={function(){setActive(i)}} onKeyDown={function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();setActive(i)}}} className={i===active?styles.chartPointActive:styles.chartPoint}><circle cx={p[0]} cy={p[1]} r={i===active?8:6} className={styles.chartPointHalo}/><circle cx={p[0]} cy={p[1]} r={i===active?4.5:3.5} className={styles.chartPointDot}/></g>})}
        {rows.map(function(p,i){return <text key={"label-"+(p.time||i)} x={x(i)} y={H-15} textAnchor={i===0?"start":i===rows.length-1?"end":"middle"} className={styles.chartAxisLabel}>{p.time}</text>})}
      </svg>
    </div>
    <div className={styles.chartInspector}><div><span>{selected.time}</span><b>{selected.label||"Demand pattern"}</b><p>{selected.detail||""}</p></div>{selected.status&&<em>{selected.status}</em>}</div>
  </div>;
}

function SecurityPanel({items=[],coveredDays=null}){
  const critical=items.filter(function(x){return x&&x.severity==="critical"}).length;
  const attention=items.filter(function(x){return x&&x.severity==="attention"}).length;
  const title=critical?"Critical attention":attention?"Attention required":items.length?"No critical exception in completed reports":"No security exception recorded in the available coverage";
  const note=!items.length&&coveredDays!=null?"This applies only to the "+coveredDays+" completed day"+(coveredDays===1?"":"s")+" represented here.":"";
  return <section className={styles.securitySnapshot}>
    <div className={styles.securitySnapshotHead}><div><span className={styles.panelEyebrow}>Security</span><h3>{title}</h3></div></div>
    {note&&<p className={styles.securityScopeNote}>{note}</p>}
    <div className={styles.securitySummaryList}>{items.slice(0,5).map(function(x,i){return <div className={styles.securitySummaryRow} key={(x.title||i)+"-"+i}><span className={styles.securityDot+" "+(x.severity==="critical"?styles.dotCritical:x.severity==="attention"?styles.dotAttention:styles.dotGood)}/><div><b>{x.title||"Security note"}</b><p>{x.body||x.summary||""}</p></div>{x.value&&<strong>{x.value}</strong>}</div>})}</div>
  </section>;
}

function ActionList({items=[]}){
  if(!items.length)return null;
  return <section className={styles.actionPanelV3}><div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Next actions</span><h3>What management should follow up</h3><p>Only actions supported by this report are shown.</p></div></div><div className={styles.actionRowsV3}>{items.slice(0,3).map(function(a,i){return <div className={styles.actionRowV3} key={(a.id||a.title||i)+"-"+i}><span>{i+1}</span><div><div className={styles.actionTitleV3}><b>{a.title||"Action"}</b>{a.priority&&<em>{a.priority}</em>}</div><p>{a.body||a.detail||String(a)}</p></div></div>})}</div></section>;
}

function DetailRows({items=[]}){
  if(!items.length)return <div className={styles.restaurantEmpty}>No additional business detail is available for this period.</div>;
  return <div className={styles.operationRows}>{items.map(function(x,i){return <div className={styles.operationRow} key={(x.title||i)+"-"+i}><div className={styles.operationIndex}>{String(i+1).padStart(2,"0")}</div><div className={styles.operationMain}><div className={styles.operationRowHead}><b>{x.title||"Business area"}</b>{x.status&&<span>{x.status}</span>}</div><p>{x.body||""}</p>{x.takeaway&&<small>{x.takeaway}</small>}</div></div>})}</div>;
}

function SavedReports({windowData,siteId,limit=8}){
  const all=windowData&&windowData.saved_reports||[];
  const rows=all.slice(0,limit),extra=all.slice(limit);
  if(!rows.length)return null;
  const renderRow=function(x,i,prefix){const d=String(x.service_date||"");const highlights=x.highlights||[];return <a key={(prefix||"row")+"-"+(x.report_id||d||i)+"-"+i} className={styles.savedReportRow} href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}>
    <span className={styles.savedReportDate}><b>{shortDate(d)}</b><small>{new Date(d+"T12:00:00").toLocaleDateString([], {weekday:"short"})}</small></span>
    <span className={styles.savedReportCopy}><b>{x.title||"Daily management report"}</b><small>{x.summary||highlights[0]||"Completed daily report"}</small></span>
    <span className={styles.savedReportState}><i/>Completed</span>
    <em aria-hidden="true">→</em>
  </a>};
  return <section className={styles.savedReportsCard}>
    <div className={styles.savedReportsHead}><div><span className={styles.panelEyebrow}>Daily reports</span><h3>Completed service days</h3><p>Open a day to see the full management story, actions and security detail.</p></div><span>{all.length} available</span></div>
    <div className={styles.savedReportTable}>{rows.map(function(x,i){return renderRow(x,i,"primary")})}</div>
    {extra.length>0&&<details className={styles.savedReportsMore}><summary>Show {extra.length} more completed report{extra.length===1?"":"s"}</summary><div className={styles.savedReportTable}>{extra.map(function(x,i){return renderRow(x,i,"extra")})}</div></details>}
  </section>;
}

function ConfidenceDetails({coverage,visibility=[],sufficiency}){
  const hasCoverage=coverage&&(coverage.summary||coverage.note||coverage.period||coverage.status);
  if(!hasCoverage&&!visibility.length&&!sufficiency)return null;
  return <div className={styles.footerDetails}>
    {sufficiency&&<details open={sufficiency.level==="limited"}><summary>Report confidence</summary><p>{sufficiency.message}</p></details>}
    {hasCoverage&&<details><summary>Coverage and figure limits</summary><p>{coverage.status&&<b>{coverage.status+". "}</b>}{coverage.summary||""} {coverage.note||""}</p></details>}
    {visibility.length>0&&<details><summary>Visibility improvements</summary>{visibility.map(function(x,i){return <p key={i}><b>{x.title||"Improvement"}:</b> {x.body||x.recommendation||""}</p>})}</details>}
  </div>;
}

function normalizeDaily({view,day,securityDay,snapshot,requestedReportDate}){
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
      security:p.incidents||[],
      operations:p.operations||[],
      actions:p.action_items||p.priority_actions||[],
      coverage:p.coverage||{},
      visibility:p.visibility_notes||[],
      reportId:snapshot.report_id||null,
      sufficiency:{level:"good",message:"This completed daily report is based on the available service-day coverage. Any missing time remains unknown rather than being treated as zero activity."}
    };
  }

  const data=day||{},q=data.data_quality||{},sessions=data.sessions||{};
  const hourly=(data.hourly||[]).filter(function(x){return Number(x.samples||0)>0});
  const peakVisible=hourly.reduce(function(m,x){return Math.max(m,Number(x.peak_visible_customers||0))},0);
  const peakTables=hourly.reduce(function(m,x){return Math.max(m,Number(x.peak_occupied_tables||0))},0);
  const busiest=hourly.reduce(function(best,x){return Number(x.peak_visible_customers||0)>Number(best&&best.peak_visible_customers||-1)?x:best},null);
  const maxPeak=Math.max.apply(null,[1].concat(hourly.map(function(x){return Number(x.peak_visible_customers||0)})));
  const hasBusiness=Number(q.camera_observations||0)>0||Number(q.table_observations||0)>0;
  const timeline=hourly.map(function(x){return {time:x.local_hour||"",level:clampLevel(x.peak_visible_customers,maxPeak),label:x.peak_visible_customers==null?"Activity":"Peak visible diners "+x.peak_visible_customers,detail:x.peak_occupied_tables==null?"":"Peak occupied tables "+x.peak_occupied_tables}});
  const metrics=hasBusiness?[
    {value:val(peakVisible),label:"Peak visible diners",note:"Concurrent visible diners, not unique footfall"},
    {value:val(peakTables),label:"Peak occupied tables",note:"Highest simultaneous table use"},
    {value:val(sessions.estimated_covers),label:"Estimated covers",note:"Estimate based on visible table activity"},
    {value:sessions.median_observed_time_to_food_minutes==null?"—":sessions.median_observed_time_to_food_minutes+" min",label:"Observed time to food",note:"Visible seating to first food seen"}
  ]:[];
  const highlights=[];
  if(busiest&&busiest.local_hour)highlights.push("The strongest visible demand was around "+busiest.local_hour+".");
  if(num(sessions.estimated_covers)!=null)highlights.push("The available coverage supports an estimated "+sessions.estimated_covers+" covers for the represented period.");
  if(num(sessions.median_observed_time_to_food_minutes)!=null)highlights.push("Median observed time to food was about "+sessions.median_observed_time_to_food_minutes+" minutes in supported table sessions.");
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
    summary:summary,highlights:highlights,metrics:metrics,timeline:timeline,
    security:(p.incidents&&p.incidents.length?p.incidents:(securityDay&&securityDay.incidents||[])),operations:operations,actions:p.action_items||p.priority_actions||[],
    coverage:{status:coverageRatio==null?"Coverage not yet rated":pct(coverageRatio)+" service-window coverage",summary:"Figures reflect only the periods with enough visibility to support them.",note:"Missing periods are unknown, not zero activity."},
    visibility:visibility,reportId:snapshot&&snapshot.report_id||null,
    sufficiency:{level:hasBusiness?"good":"limited",message:hasBusiness?"The report shows only figures that can be supported for the represented service period.":"There is not enough comparable business coverage yet for a complete daily management view."}
  };
}

function normalizePeriod({view,period,windowData}){
  const days=view==="week"?7:30,data=period||windowData&&windowData.structured_restaurant_metrics||{},s=data.summary||{},daily=data.daily||[],weeks=data.weekly_trend||[],saved=windowData&&windowData.saved_reports||[];
  const observed=Number(s.observed_service_days||0),previousObserved=Number(data.previous_period&&data.previous_period.observed_service_days||0),minimum=days===7?4:10;
  const trendReady=observed>=minimum,comparisonReady=trendReady&&previousObserved>=minimum,weekdayReady=days===30&&observed>=14;
  const busiestDay=s.busiest_day||null,busiestHour=s.busiest_hour||null;
  const series=days===30?weeks:daily.filter(function(x){return Number(x.camera_observations||0)>0});
  const maxTrend=Math.max.apply(null,[1].concat(series.map(function(x){return Number(x.estimated_covers||0)})));
  const timeline=trendReady?series.map(function(x){const d=x.service_date||x.week_start;return {time:shortDate(d),level:clampLevel(x.estimated_covers,maxTrend),label:x.estimated_covers==null?"Demand":"Estimated covers "+x.estimated_covers,detail:days===30?(x.observed_days+" represented service day"+(Number(x.observed_days)===1?"":"s")+" in this week"):(x.peak_visible_diners==null?"":"Peak visible diners "+x.peak_visible_diners)}}):[];
  const coverDelta=comparisonReady&&num(data.comparison&&data.comparison.estimated_covers_pct)!=null?((Number(data.comparison.estimated_covers_pct)>0?"+":"")+Number(data.comparison.estimated_covers_pct)+"%"):null;
  const metrics=trendReady?[
    {value:val(s.avg_estimated_covers_per_observed_day),label:"Avg estimated covers",note:"Per represented service day",delta:coverDelta},
    {value:busiestDay&&busiestDay.service_date?shortDate(busiestDay.service_date):"—",label:"Busiest represented day",note:busiestDay&&busiestDay.estimated_covers!=null?"Estimated covers "+busiestDay.estimated_covers:""},
    {value:busiestHour&&busiestHour.local_hour||"—",label:"Busiest time",note:"Strongest recurring visible demand"},
    {value:s.median_observed_time_to_food_minutes==null?"—":s.median_observed_time_to_food_minutes+" min",label:"Observed time to food",note:"Across supported table sessions"}
  ]:[];
  const security=[];
  saved.forEach(function(r){(r.incidents||[]).forEach(function(x){security.push(Object.assign({},x,{service_date:r.service_date}))})});
  const operations=[];
  if(trendReady&&busiestHour&&busiestHour.local_hour)operations.push({title:"Demand timing",status:busiestHour.local_hour,body:"The strongest recurring visible demand was around "+busiestHour.local_hour+"."});
  if(trendReady&&busiestDay&&busiestDay.service_date)operations.push({title:"Strongest service day",status:shortDate(busiestDay.service_date),body:"The strongest represented day in this period was "+dateLabel(busiestDay.service_date)+"."});
  if(trendReady&&num(s.median_observed_time_to_food_minutes)!=null)operations.push({title:"Service timing",status:s.median_observed_time_to_food_minutes+" min",body:"This is the median visible seating-to-first-food interval across supported table sessions."});
  if(comparisonReady&&num(data.comparison&&data.comparison.estimated_covers_pct)!=null){const d=Number(data.comparison.estimated_covers_pct);operations.push({title:"Compared with the prior period",status:(d>0?"+":"")+d+"%",body:"Estimated covers changed across two periods with enough represented days to support a comparison."})}
  const actions=[],seen=new Set();
  saved.forEach(function(r){(r.action_items||[]).forEach(function(a){const key=a.id||a.title||a.body;if(key&&!seen.has(key)){seen.add(key);actions.push(Object.assign({},a,{report_id:r.report_id}))}})});
  const summary=trendReady
    ?(days===7?"The last 7 days":"The last 30 days")+" contain enough represented service days for a management trend. "+(busiestHour&&busiestHour.local_hour?"Visible demand was strongest most often around "+busiestHour.local_hour+". ":"")+(comparisonReady?"Prior-period comparison is shown only where both periods have enough represented days.":"The prior period does not yet have enough comparable days for a reliable change statement.")
    :"Only "+observed+" of "+days+" service days currently have enough business coverage for comparable figures. The completed daily reports remain available, but WatchLog is not presenting a "+days+"-day trend as if the full period were represented.";
  const highlights=trendReady?[
    busiestHour&&busiestHour.local_hour?"Recurring demand was strongest around "+busiestHour.local_hour+".":null,
    busiestDay&&busiestDay.service_date?dateLabel(busiestDay.service_date)+" was the strongest represented service day.":null,
    comparisonReady?"The previous period has enough represented days for like-for-like comparison.":"The previous period is not yet complete enough for a reliable like-for-like comparison."
  ].filter(Boolean):[
    observed+" of "+days+" service days currently support comparable business figures.",
    saved.length+" completed daily report"+(saved.length===1?" is":"s are")+" available in this period.",
    "Missing days are treated as unknown, not as zero demand."
  ];
  const coverage={status:observed+" of "+days+" represented days",summary:trendReady?"Enough represented days exist for a period-level demand view.":"The period is still too sparse for a full trend.",note:"Missing or incomplete days remain unknown and are excluded from comparisons."};
  const sufficiency={level:trendReady?"good":"limited",message:trendReady?"This "+days+"-day view uses "+observed+" represented service days. "+(comparisonReady?"The prior period also meets the comparison threshold.":"Prior-period change is withheld where the comparison is not sufficiently represented."):"A "+days+"-day trend requires at least "+minimum+" represented service days. "+observed+" are currently available, so detailed period trends are withheld."};
  return {kind:"period",date:null,period:data.period||windowData&&windowData.period||{},eyebrow:days===7?"Last 7 days":"Last 30 days",headline:days===7?"The week in one view":"The month in one view",summary:summary,highlights:highlights,metrics:metrics,timeline:timeline,security:security,operations:operations,actions:actions,coverage:coverage,visibility:[],reportId:null,sufficiency:sufficiency,trendReady:trendReady,comparisonReady:comparisonReady,weekdayReady:weekdayReady,observed:observed,days:days,minimum:minimum};
}

export default function UnifiedRestaurantReport({view,day,securityDay,period,windowData,snapshot,siteId,requestedReportDate,renderActions}){
  const[mode,setMode]=useState("overview");
  const model=useMemo(function(){return view==="week"||view==="monthly"?normalizePeriod({view:view,period:period,windowData:windowData}):normalizeDaily({view:view,day:day,securityDay:securityDay,snapshot:snapshot,requestedReportDate:requestedReportDate})},[view,day,securityDay,period,windowData,snapshot,requestedReportDate]);
  const periodText=model.date?dateLabel(model.date):model.period&&model.period.start_service_date&&model.period.end_service_date?shortDate(model.period.start_service_date)+" – "+shortDate(model.period.end_service_date):periodName(view);
  const actionBlock=model.actions&&model.actions.length?(renderActions?renderActions(model.actions,model.reportId):<ActionList items={model.actions}/>):null;
  const chartTitle=view==="daily"?"Demand so far today":view==="yesterday"?"Demand through the service day":view==="week"?"Demand across the week":"Demand across the month";
  const showBuild=model.kind==="period"&&!model.trendReady;

  return <div className={styles.saasReport}>
    <div className={styles.reportControlBarV4}>
      <div className={styles.reportIdentity}>
        <ReportStatus view={view} model={model}/>
        <div><b>{periodText}</b><span>{view==="daily"?"Current service day":model.kind==="period"?"Management period":"Completed service day"}</span></div>
      </div>
      <ReportTabs mode={mode} setMode={setMode}/>
    </div>

    {mode==="overview"&&<>
      <section className={styles.reportHeroV4}>
        <div className={styles.reportHeroCopy}><span className={styles.panelEyebrow}>Management overview</span><h2>{model.headline}</h2><p>{model.summary}</p></div>
        {model.highlights&&model.highlights.length>0&&<div className={styles.reportHighlightsV4}>{model.highlights.slice(0,3).map(function(x,i){return <div key={i}><span>{String(i+1).padStart(2,"0")}</span><p>{x}</p></div>})}</div>}
      </section>

      <KpiStrip metrics={model.metrics}/>

      <div className={styles.reportMainGridV4}>
        <div className={styles.reportPrimaryV4}>
          {showBuild?<PeriodBuildState model={model} windowData={windowData} siteId={siteId}/>:<DemandChart points={model.timeline} title={chartTitle} subtitle="Use the shape of the period to see where demand strengthened, softened or repeated."/>}
        </div>
        <aside className={styles.reportRailV4}>
          <SecurityPanel items={model.security||[]} coveredDays={model.kind==="period"?model.observed:null}/>
          <ReportHealth view={view} model={model}/>
        </aside>
      </div>

      {actionBlock}
      {view==="yesterday"&&!snapshot&&<SavedReports windowData={windowData} siteId={siteId} limit={1}/>}
      {model.kind==="period"&&<SavedReports windowData={windowData} siteId={siteId} limit={view==="week"?7:6}/>}
      {model.visibility&&model.visibility.length>0&&<ConfidenceDetails coverage={null} visibility={model.visibility} sufficiency={null}/>}
    </>}

    {mode==="business"&&<>
      <section className={styles.sectionIntroV4}><span className={styles.panelEyebrow}>Business</span><h2>{view==="daily"?"Today’s demand and service flow":view==="yesterday"?"Customers, tables and service flow":view==="week"?"What changed across the week":"What patterns are becoming meaningful"}</h2><p>{view==="daily"?"Current trading activity, table use and service pressure without treating an in-progress day as complete.":view==="yesterday"?"Demand, table use, service channels and closing discipline for the completed service day.":view==="week"?"Repeated demand and service patterns across represented service days.":"Longer-term demand and service patterns only where enough represented days exist."}</p></section>
      <KpiStrip metrics={model.metrics}/>
      <div className={styles.businessGridV4}>
        <div>{showBuild?<PeriodBuildState model={model} windowData={windowData} siteId={siteId}/>:<DemandChart points={model.timeline} title={view==="week"?"Service-day trend":view==="monthly"?"Weekly demand pattern":chartTitle} subtitle="The main period trend stays primary; supporting detail is grouped below."/>}</div>
        <section className={styles.operationsPanelV3}><div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Business signals</span><h3>What management should know</h3><p>Grouped by operating question rather than by camera.</p></div></div><DetailRows items={model.operations||[]}/></section>
      </div>
      {actionBlock}
      {model.kind==="period"&&<SavedReports windowData={windowData} siteId={siteId} limit={view==="week"?7:6}/>}
      <ConfidenceDetails coverage={model.coverage} visibility={model.visibility} sufficiency={model.sufficiency}/>
    </>}

    {mode==="security"&&<>
      <section className={styles.sectionIntroV4}><span className={styles.panelEyebrow}>Security</span><h2>{view==="daily"?"Current security attention":view==="yesterday"?"Security posture for the completed day":view==="week"?"Security exceptions across the week":"Recurring security exceptions"}</h2><p>Only management-relevant exceptions are shown. Routine movement stays routine, and missing coverage is never treated as proof that nothing happened.</p></section>
      <div className={styles.securityGridV4}>
        <SecurityPanel items={model.security||[]} coveredDays={model.kind==="period"?model.observed:null}/>
        <ReportHealth view={view} model={model}/>
      </div>
      {model.security&&model.security.length>0&&<section className={styles.securityEventPanel}><div className={styles.panelHeader}><div><span className={styles.panelEyebrow}>Events & exceptions</span><h3>Security timeline</h3><p>Only items with management value are included.</p></div></div><div className={styles.securityEventList}>{model.security.map(function(x,i){return <div className={styles.securityEventRow} key={(x.title||i)+"-"+i}><span className={styles.securityDot+" "+(x.severity==="critical"?styles.dotCritical:x.severity==="attention"?styles.dotAttention:styles.dotGood)}/><div><b>{x.title||"Security note"}</b><p>{x.body||x.summary||""}</p></div>{x.service_date&&<strong>{shortDate(x.service_date)}</strong>}</div>})}</div></section>}
      {model.kind==="period"&&<SavedReports windowData={windowData} siteId={siteId} limit={view==="week"?7:6}/>}
      {model.visibility&&model.visibility.length>0&&<ConfidenceDetails coverage={null} visibility={model.visibility} sufficiency={null}/>}
    </>}
  </div>;
}
