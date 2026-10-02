"use client";
import {useEffect,useState} from "react";
import RichText from "../rich-text";
import {withSite} from "../site-context";
import {supabase,say} from "../../lib/supabase";
import useReport from "./use-report";
import UnifiedRestaurantReport from "./unified-restaurant-report";
import {deriveSiteDay,eligible,askQuestions,acceptPeriod,periodMeasure,periodAreas} from "../owner/site-profiles";
import {SiteOperations,SiteRailFacts,operationalLead} from "../owner/site-modules";
import {coverageTruth,OwnerPage,Lead,Section,Row,Metrics,Status,Ledger,RailSection,Stat,Figure,Summary,Bars,HBars,Compare,Delta,Empty,Loading,Notice,fmt as fmtN} from "../owner/ui";

const VIEWS=[["daily","Today"],["yesterday","Yesterday"],["monthly","30 days"],["executive","Executive"]];
const RESTAURANT_VIEWS=[["daily","Today"],["yesterday","Yesterday"],["week","Last 7 days"],["monthly","Last 30 days"]];
const OFFICE_VIEWS=[["daily","Today"],["yesterday","Yesterday"],["week","Last 7 days"],["monthly","Last 30 days"]];
function dateLabel(v){if(!v)return"—";try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",weekday:"long",day:"numeric",month:"long",year:"numeric"}).format(new Date(v+"T00:00:00Z"))}catch{return String(v)}}
function shortDate(v){if(!v)return"—";try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",day:"numeric",month:"short"}).format(new Date(v+"T00:00:00Z"))}catch{return String(v)}}
function val(v,suffix=""){return v==null?"—":String(v)+suffix}
function num(v){return v==null||Number.isNaN(Number(v))?null:Number(v)}
function coverageLabel(v){const n=num(v);return n==null?"—":Math.round(n*100)+"%"}
function signed(v,suffix=""){const n=num(v);if(n==null)return"—";return(n>0?"+":"")+String(n)+suffix}

function dayShort(v){if(!v)return"—";try{return new Intl.DateTimeFormat("en-PK",{timeZone:"UTC",weekday:"short"}).format(new Date(v+"T00:00:00Z"))}catch{return String(v)}}

// ---------------------------------------------------------------------------------------------
// Owner Reports (Signal Ledger). Decision first: period + one-sentence conclusion + required action,
// then Security → Operations → What changed → Recommended action, with method and detail one step away.
// Code counts and decides; the saved report's own summary (AI-written) is shown, never re-derived.
// ---------------------------------------------------------------------------------------------

function firstSentences(text,n=2){
  const t=String(text||"").replace(/\s+/g," ").trim();
  if(!t)return"";
  const parts=t.match(/[^.!?]+[.!?]+(\s|$)/g);
  return parts?parts.slice(0,n).join("").trim():t;
}
function plural(n,one,many){return n+" "+(n===1?one:many)}
function sevTone(v){const s=String(v||"").toLowerCase();return s==="critical"?"bad":s==="attention"||s==="warning"?"warn":s==="none"||s==="clear"?"ok":"unknown"}

// Day facts. Offices read the office brief (office semantics); every other business profile reads only the
// site-neutral governed keys (access windows, day boundaries, after-hours), never data.office.
function siteNeutralDay(data,siteDay){
  const eps=(data.access_windows||[]).filter(e=>e.object_class!=="vehicle");
  const byCamera={};for(const e of eps){const k=e.camera||"Camera";byCamera[k]=(byCamera[k]||0)+1}
  const peak=siteDay&&siteDay.peakHour!=null?siteDay.timeline.find(t=>t.hour===siteDay.peakHour):null;
  return{activity:eps.length,activityLabel:"Activity episodes",activityNote:"camera observations, not unique people",
    peak:peak?{hour:peak.hour,count:peak.episodes,unit:"episodes"}:null,
    byArea:Object.entries(byCamera).sort((a,b)=>b[1]-a[1]).map(([camera,events])=>({camera,events}))};
}
function OfficeDayReport({data,periodLabel,title="Operations",dayNoun="working day",showAreas=true,profile,siteDay}){
  if(!data)return <Empty title={"No structured activity intelligence is available for this "+dayNoun+" yet."}>The report fills in once monitoring for the day is complete.</Empty>;
  const office=data.office||{},attn=data.attention||{},bounds=data.day_boundaries||{};
  const neutral=profile&&profile.key!=="office"?siteNeutralDay(data,siteDay):null;
  const byArea=neutral?neutral.byArea:(office.by_area||[]),restricted=data.restricted||[];
  const activity=neutral?neutral.activity:Number(office?.coverage?.person_events||0);
  const afterHours=Number(data?.after_hours?.count??(neutral?0:office.after_hours_total)??0);
  const restrictedEpisodes=restricted.reduce((n,x)=>n+Number(x.episodes||0),0);
  const peak=neutral?neutral.peak:(office?.peak_hour?.hour==null?null:{hour:office.peak_hour.hour,count:office.peak_hour.count,unit:"detections"});
  const verifiedStart=Boolean(siteDay?.opening?.verified);
  return <Section title={title} note={periodLabel+" · "+(neutral?neutral.activityNote:"activity detections, not unique people")}>
    <Metrics items={[
      {value:fmtN(activity),label:neutral?neutral.activityLabel:"Activity detections",note:neutral?neutral.activityNote:"camera detections, not unique people",primary:true},
      {value:fmtN(afterHours),label:"After-hours observations",note:"outside configured hours"},
      {value:peak?String(peak.hour).padStart(2,"0")+":00":null,label:"Peak activity hour",note:peak&&peak.count!=null?fmtN(peak.count)+" "+peak.unit:undefined,unknown:"Not observed"},
      {value:bounds.opening_at||null,label:verifiedStart?"Opening activity":"First observed activity",note:verifiedStart?"earlier part of the day verified":"not proof of the operating start",unknown:"Not observed"},
      {value:bounds.closing_at||null,label:"Last observed activity",unknown:"Not observed"},
    ]}/>
    {showAreas&&byArea.length>0&&<div style={{marginTop:18}}><HBars question="Activity by monitored camera" items={byArea.slice(0,6).map((a,i)=>({key:(a.camera||"area")+i,label:a.camera||"Area",value:a.events}))}/></div>}
    {restrictedEpisodes>0&&<p className="ow-muted" style={{fontSize:12.5,marginTop:10}}>{plural(restrictedEpisodes,"restricted-area episode","restricted-area episodes")} recorded{Number(attn.critical||0)?", "+plural(Number(attn.critical),"critical item","critical items"):""}.</p>}
    <p className="ow-muted" style={{fontSize:12,marginTop:10}}>Role-specific conclusions need confirmed camera mapping. Missing monitoring is missing evidence, not zero activity.</p>
  </Section>;
}

function OfficePeriodReport({data,days,profile}){
  const dayNoun=profile?.dayNoun||"working day";
  // Only the profile's own governed period schema is read (office-period-v1 or site-period-v1).
  if(!acceptPeriod(profile,data))return <Empty title={"A period comparison is not available for this site yet."}>{profile&&profile.key!=="office"?"Each completed "+dayNoun+" report remains available under Yesterday. The "+days+"-day comparison for this site type has not been enabled.":"The period view fills in as completed working days are reported."}</Empty>;
  const s=data.summary||{},p=data.previous_period||{},c=data.comparison||{},daily=data.daily||[];
  const m=periodMeasure(profile),areas=periodAreas(profile,data);
  const observed=Number(s.observed_days||0),prevObserved=Number(p.observed_days||0),minimum=Math.min(4,Number(s.days||days));
  const comparable=observed>=minimum&&prevObserved>=minimum;
  const series=daily.map(d=>{const seen=Number(d.coverage_ratio||0)>0;return{label:days===7?dayShort(d.date):shortDate(d.date).replace(/ .*/,""),value:seen?Number(d[m.key]||0):0,gap:!seen,prev:!d.working_day&&seen,title:d.date+": "+(seen?fmtN(d[m.key])+" · "+m.note:"not observed")}});
  const working=daily.filter(x=>x.working_day),nonWorking=daily.filter(x=>!x.working_day);
  const sum=(rows,key)=>rows.reduce((n,x)=>n+Number(x[key]||0),0);
  return <>
    <Section title="What changed" note={(days===7?"Last 7 completed "+dayNoun+"s":"Last 30 completed days")+" vs the previous period"}>
      <div className="ow-grid2">
        <Bars height={120} question={(profile?.comparisonLabel||"Activity detections")+(days===7?" by "+dayNoun:" by day")} series={series} showValues={days===7}
          legend={<><span>{days===7?"Observed":"Working day"}</span>{days===30&&<span className="prev">Non-working day</span>}{series.some(x=>x.gap)&&<span className="gap">Not observed</span>}</>}/>
        <div>{comparable?<Compare rows={[
          {label:m.label,note:m.note,current:s[m.key],previous:p[m.key],delta:c[m.delta],deltaLabel:signed(c[m.delta])},
          {label:"Attention items",current:s.incidents_total,previous:p.incidents_total,delta:c.incidents_delta,deltaLabel:signed(c.incidents_delta)},
          {label:"After-hours activity",current:s.after_hours_total,previous:p.after_hours_total,delta:c.after_hours_delta,deltaLabel:signed(c.after_hours_delta)},
          ...(num(s.avg_coverage_ratio)!=null&&num(p.avg_coverage_ratio)!=null?[{label:"Monitoring coverage",note:"period average",current:s.avg_coverage_ratio,previous:p.avg_coverage_ratio,delta:c.coverage_delta_points,deltaLabel:signed(c.coverage_delta_points," pts")}]:[])
        ]}/>:<Empty title="A reliable comparison is not ready yet.">{observed} of {val(s.days)} days observed now, {prevObserved} of {val(p.days)} before.</Empty>}
        {comparable&&<div className="ow-legend" style={{marginTop:10}}><span>This period</span><span className="prev">Previous period</span></div>}</div>
      </div>
      <p className="ow-muted" style={{fontSize:12,marginTop:10}}>{observed} of {val(s.days)} days observed. Figures are {m.note}; missing monitoring is missing evidence, not zero activity.</p>
    </Section>
    {comparable&&areas.length>0&&<Section title="Area activity vs the previous period" note="Activity episodes at configured camera purposes · observations, not output or sales">
      <Compare rows={areas.map(a=>({label:a.label,note:"episodes",current:a.current,previous:a.previous,delta:a.delta,deltaLabel:signed(a.delta)}))}/>
    </Section>}
    <Section title="Period totals">
      <Metrics items={[
        {value:fmtN(s.incidents_total),label:"Attention items",note:fmtN(s.critical_total)+" critical"},
        {value:fmtN(s.after_hours_total),label:"After-hours observations"},
        {value:fmtN(s[m.key]),label:m.label,note:m.note},
        days===30?{value:fmtN(sum(working,m.key)),label:"On "+dayNoun+"s",note:working.length+" "+dayNoun+"s"}:{value:coverageLabel(s.avg_coverage_ratio),label:"Average coverage"},
        ...(days===30?[{value:fmtN(sum(nonWorking,m.key)),label:"On other days",note:nonWorking.length+" days"}]:[]),
      ]}/>
    </Section>
  </>;
}

function ManagementReading({answer,label}){
  if(!answer)return null;
  return <details className="ow-details"><summary>{label||"WatchLog's reading of the period"}</summary><div style={{fontSize:13.5,lineHeight:1.6,color:"var(--ow-text)"}}><RichText text={answer}/></div></details>;
}

function InsightCards({items=[]}){
  if(!items.length)return null;
  return <div className="ow-rows">{items.map((f,i)=><Row key={`${f.title}-${i}`} tone={sevTone(f.severity)} title={f.title} body={f.body} meta={f.value?[f.value]:[]}/>)}</div>;
}

function SavedReportHistory({windowData,siteId,mode="period",excludeDate=""}){
  const rows=(windowData?.saved_reports||[]).filter(x=>x?.service_date&&String(x.service_date)!==String(excludeDate||""));
  if(!rows.length)return null;
  const shown=mode==="latest"?rows.slice(0,1):rows;
  return <Section title={mode==="latest"?"Most recent completed report":"Completed daily reports in this period"} count={mode==="latest"?null:rows.length}
    note={mode==="latest"?"No report was saved for the selected day.":undefined}>
    <div className="ow-rows">{shown.map((x,i)=>{
      const d=String(x.service_date||"");
      const h=(x.highlights||[])[0];
      return <Row compact key={(x.report_id||d||i)+"-"+i} tone="verified" title={dateLabel(d)} body={x.summary&&x.summary!=="Completed management report"?x.summary:(typeof h==="string"?h:(h?.body||h?.title||"Completed management report"))}
        action={<a href={withSite("/reports/?view=yesterday&date="+encodeURIComponent(d),siteId)}>Open report</a>}/>;
    })}</div>
  </Section>;
}

const RECOMMENDATION_RESPONSES=[
  ["accepted","Accept"],
  ["need_help","Need help"],
  ["not_now","Later"],
  ["not_relevant","Not relevant"],
];
const RECOMMENDATION_TEAM_STATUS={
  new:"Sent to WatchLog",
  in_progress:"WatchLog is following up",
  resolved:"Follow-up resolved",
  closed:"Closed",
};

function RecommendationFeedback({action,existing,siteId,reportId,onSaved}){
  const[open,setOpen]=useState(false);
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
  const savedLabel=existing?.response_code?(RECOMMENDATION_RESPONSES.find(x=>x[0]===existing.response_code)?.[1]||existing.response_code)+(existing.team_status?" · "+(RECOMMENDATION_TEAM_STATUS[existing.team_status]||existing.team_status):""):"";

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

  return <div className="ow-feedback">
    <div className="ow-feedback-bar">
      <button type="button" className="ow-linkbtn" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>{existing?.response_code?"Update response":"Respond"}</button>
      <a href={withSite("/ai/?prompt="+encodeURIComponent(discussPrompt),siteId)}>Discuss with WatchLog</a>
      {savedLabel&&<span className="ow-pill verified">Saved · {savedLabel}</span>}
    </div>
    {open&&<div className="ow-feedback-form">
      <div className="ow-choices" role="group" aria-label="Your response">{RECOMMENDATION_RESPONSES.map(([code,label])=><button type="button" aria-pressed={choice===code} key={code} onClick={()=>{setChoice(code);setMessage("");if(code==="need_help")setShowNote(true);}}>{label}</button>)}</div>
      <button type="button" className="ow-linkbtn" onClick={()=>setShowNote(v=>!v)}>{showNote?"Hide comment":"Add comment"}</button>
      {showNote&&<textarea maxLength={2000} rows={3} value={note} onChange={e=>setNote(e.target.value)} placeholder="Context for the WatchLog team: what you agree with, what should change, or where you need help."/>}
      <div className="ow-feedback-bar"><button type="button" className="ow-btn small" disabled={busy||!choice} onClick={save}>{busy?"Saving…":existing?.response_code?"Update response":"Send feedback"}</button>{message&&<span className="ow-muted" role="status">{message}</span>}</div>
    </div>}
  </div>;
}

function RecommendationRow({action,index,existing,siteId,reportId,onSaved}){
  return <div className="ow-action">
    <span className="ow-action-n">{index+1}</span>
    <div>
      <h3>{action.title||"Action"}{action.priority&&<span className="ow-pill" style={{marginLeft:8}}>{action.priority}</span>}</h3>
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
  return <Section title="Recommended action" count={rows.length>1?rows.length:null} id="recommended-action">
    {loadError&&<Notice tone="warn">{loadError}</Notice>}
    <div className="ow-actions">{rows.slice(0,3).map((a,i)=><RecommendationRow key={(a.id||a.title||i)+"-"+i} action={a} index={i} existing={feedback[a.id]} siteId={siteId} reportId={a.report_id||reportId} onSaved={saved}/>)}</div>
    {rows.length>3&&<details className="ow-details"><summary>Show {rows.length-3} more improvements</summary><div className="ow-actions">{rows.slice(3).map((a,i)=><RecommendationRow key={(a.id||a.title||i)+"-"+i} action={a} index={i+3} existing={feedback[a.id]} siteId={siteId} reportId={a.report_id||reportId} onSaved={saved}/>)}</div></details>}
  </Section>;
}

function MonitoringDetail({coverage,cameras,restaurant}){
  if(!coverage?.status&&!coverage?.summary&&!cameras?.length)return null;
  return <details className="ow-details">
    <summary>{restaurant?"Observation window and key areas":"Monitoring detail and key areas"}</summary>
    <div>
      {(coverage?.summary||coverage?.note)&&<p style={{fontSize:13,marginBottom:12}}><b>{coverage.status||"Coverage"}{coverage.period?" · "+coverage.period:""}.</b> {coverage.summary||""} {coverage.note||""}</p>}
      {cameras?.length>0&&<table className="ow-table"><thead><tr><th>Area</th><th>Status</th><th>When</th><th>What it means</th></tr></thead><tbody>{cameras.map((c,i)=><tr key={`${c.camera}-${i}`}>
        <td><b>{c.camera}</b><small>{c.assessment}</small></td>
        <td><Status tone={/attention|partial|not/i.test(c.status||"")?"warn":/cover/i.test(c.status||"")?"verified":"unknown"}>{c.status||"Covered"}</Status></td>
        <td>{c.period||"—"}</td><td>{c.management_view||"Routine"}</td></tr>)}</tbody></table>}
    </div>
  </details>;
}

// The saved daily management brief for a completed day.
function EvidenceReport({snapshot,siteId,activity}){
  const p=snapshot?.payload||{},incidents=p.incidents||[],coverage=p.coverage||{},cameras=p.camera_coverage||[],insights=p.site_insights||[],actionItems=p.action_items||[],actions=p.priority_actions||[];
  const restaurant=p.site_type==="restaurant"||p.report_profile==="restaurant_business_owner_v1";
  const exceptions=incidents.filter(x=>sevTone(x.severity)==="warn"||sevTone(x.severity)==="bad");
  return <>
    <Section title="Security" count={exceptions.length||null} first>
      {incidents.length?<InsightCards items={incidents}/>:<Empty title="No security exception recorded in the available coverage."/>}
    </Section>
    {/* With structured day activity below, an empty saved-observations box would read as "nothing happened". */}
    {(insights.length>0||!activity)&&<Section title={restaurant?"Business":"Operations"}>
      {insights.length?<InsightCards items={insights}/>:<Empty title="No operating observations were saved for this day."/>}
    </Section>}
    {activity||null}
    {(actionItems.length>0||actions.length>0)&&<PriorityActions items={actionItems} fallback={actions} siteId={siteId} reportId={snapshot?.report_id}/>}
    <MonitoringDetail coverage={coverage} cameras={cameras} restaurant={restaurant}/>
  </>;
}

function reportLead(r){
  const snap=r.snapshot?.payload||{};
  const savedIncidents=(snap.incidents||[]).filter(x=>["warn","bad"].includes(sevTone(x.severity)));
  const covRatio=num(snap?.coverage?.coverage_ratio)??num(r.officeDay?.coverage?.coverage_ratio);
  const cov=covRatio==null?null:Math.round(Math.max(0,Math.min(1,covRatio))*100);
  const covText=cov==null?(snap?.coverage?.period?"monitoring "+snap.coverage.period:"monitoring not verified"):"monitoring "+cov+"% verified";
  if(r.isOffice&&(r.view==="week"||r.view==="monthly")){
    const s=acceptPeriod(r.profile,r.officePeriod)?r.officePeriod.summary:null;if(!s)return null;
    const items=Number(s.incidents_total||0),crit=Number(s.critical_total||0),after=Number(s.after_hours_total||0);
    const pc=num(s.avg_coverage_ratio);
    return{tone:crit?"bad":items||after?"warn":"ok",
      title:(items?plural(items,"attention item","attention items"):"No attention items")+" · "+plural(after,"after-hours observation","after-hours observations"),
      body:(pc==null?"Coverage not verified":"Average coverage "+Math.round(pc*100)+"%")+" across "+plural(Number(s.observed_days||0),"observed day","observed days")+"."};
  }
  if(r.view==="yesterday"||(r.isOffice&&r.view==="daily")){
    const attn=r.officeDay?.attention||{};
    const n=savedIncidents.length||Number(attn.critical||0)+Number(attn.warning||0);
    const critical=savedIncidents.filter(x=>sevTone(x.severity)==="bad").length||Number(attn.critical||0);
    if(!r.snapshot&&!r.officeDay)return null;
    const summary=firstSentences(snap.narrative_summary||snap.ai_summary||snap.executive_summary||"",2);
    const op=r.isOffice&&r.siteDay?operationalLead(r.profile,r.siteDay):null;
    return{tone:critical?"bad":n?"warn":cov==null?"unknown":"ok",
      title:(n?plural(n,"item needs","items need")+" review":"No security exception recorded")+" · "+covText,
      body:summary||(op?op+".":"")||(n?"":"Nothing needed management attention in the available coverage.")};
  }
  return null;
}

function ReportRail({r,lead}){
  const snap=r.snapshot?.payload||{};
  const covRatio=num(snap?.coverage?.coverage_ratio)??num(r.officeDay?.coverage?.coverage_ratio)??(r.view==="week"||r.view==="monthly"?num(r.officePeriod?.summary?.avg_coverage_ratio):null);
  const cov=covRatio==null?null:Math.round(Math.max(0,Math.min(1,covRatio))*100);
  const metrics=(snap.metrics||[]).filter(m=>!/coverage/i.test(m.label||"")).slice(0,4);
  const c=acceptPeriod(r.profile,r.officePeriod)?r.officePeriod.comparison:null;
  const reportDate=r.snapshot?.report_date||snap.report_date||r.requestedReportDate;
  const typeAsks=r.isOffice&&r.siteDay?askQuestions(r.profile,eligible(r.profile,r.siteDay,r.officePeriod)).slice(0,2).map(q=>[q,q]):[];
  const asks=[
    ["Explain this report",r.prompt],
    ["What should I do first?","From this report, what is the single most important action and why?"],
    ...typeAsks,
    ["What could WatchLog not verify?","For this reporting period, what time or areas could WatchLog not verify, and does it change the conclusion?"],
  ];
  return <>
    <RailSection label="Monitoring confidence">
      {cov!=null?<Figure value={cov+"%"} unit="verified"/>:<Figure value={snap?.coverage?.period?"—":"—"} unit={snap?.coverage?.period||"not verified yet"}/>}
      <div style={{marginTop:10}}><Ledger ratio={covRatio} classes={r.view==="week"||r.view==="monthly"?null:(snap?.coverage?.classes||r.officeDay?.coverage?.classes)}/></div>
      {snap?.coverage?.note&&<p className="ow-rail-note">{snap.coverage.note}</p>}
    </RailSection>
    <RailSection label="Report">
      <Stat label="Period" value={r.periodLabel}/>
      {(r.view==="yesterday")&&<Stat label="Saved report" value={<Status tone={r.snapshot?"verified":"unknown"}>{r.snapshot?"Completed":"Not saved yet"}</Status>}/>}
      {reportDate&&r.view==="yesterday"&&<Stat label={r.isOffice?r.profile.dayNoun.charAt(0).toUpperCase()+r.profile.dayNoun.slice(1):"Service day"} value={shortDate(reportDate)}/>}
      <a className="ow-rail-link" href={withSite("/reports/delivery/",r.siteId)}><span>Delivery & recipients</span><i>Manage</i></a>
    </RailSection>
    {r.isOffice&&r.siteDay&&(r.view==="daily"||r.view==="yesterday")&&<SiteRailFacts profile={r.profile} label={r.profile.label+" · "+(r.view==="daily"?"today":r.profile.dayNoun)} day={r.siteDay}/>}
    {metrics.length>0&&<RailSection label="Key figures">{metrics.map((m,i)=><Stat key={(m.label||"")+i} label={m.label} note={m.note} value={m.value}/>)}</RailSection>}
    {c&&(r.view==="week"||r.view==="monthly")&&<RailSection label="Vs previous period">
      <Stat label="Attention items" value={<Delta value={c.incidents_delta} label={signed(c.incidents_delta)}/>}/>
      <Stat label="After-hours" value={<Delta value={c.after_hours_delta} label={signed(c.after_hours_delta)}/>}/>
      <Stat label="Coverage" value={<Delta value={c.coverage_delta_points} label={signed(c.coverage_delta_points," pts")}/>}/>
    </RailSection>}
    <RailSection label="Ask WatchLog about this report">
      <div className="ow-ask">{asks.map(([label,p])=><a key={label} href={withSite("/ai/?prompt="+encodeURIComponent(p),r.siteId)}>{label}</a>)}</div>
    </RailSection>
  </>;
}

export default function CustomerReports(){
  const r=useReport();
  // One governed day dataset -> the site type's operating picture (same derivation as Home and Insights).
  const siteDay=r.isOffice&&r.officeDay&&(r.view==="daily"||r.view==="yesterday")?deriveSiteDay({profile:r.profile,daily:r.officeDay,cameras:r.siteAi?.cameras||[],hours:r.siteAi?.business_context,coverage:coverageTruth(r.officeDay.coverage)}):null;
  const views=r.isChaiWalaRestaurant?RESTAURANT_VIEWS:r.isOffice?OFFICE_VIEWS:VIEWS;
  const label=views.find(([k])=>k===r.view)?.[1]||"Report";
  const restaurantName=r.site?.name||"this restaurant";
  const prompts={
    daily:`Explain today's restaurant operations at ${restaurantName}. Focus on demand, tables, observed service timing, service pressure, coverage and any security attention.`,
    yesterday:`Explain yesterday's restaurant report for ${restaurantName}. Focus on demand, tables, observed service timing, service pressure, coverage and any security attention.`,
    week:`Explain the last 7 service days for ${restaurantName}. Identify repeated demand, table-utilization and observed service-time patterns, and practical improvements supported by the evidence.`,
    monthly:`Explain the last 30 service days for ${restaurantName}. Identify weekly, weekday and hourly patterns, floor/table utilization, observed service-time trends and practical improvements supported by the evidence.`
  };
  const officePrompts={
    daily:"Explain today's office report in natural management language. Lead with what needs attention, monitoring confidence and practical improvements. Do not call activity detections unique people.",
    yesterday:"Explain the last completed working-day office report. Lead with security attention, office activity, after-hours exceptions, coverage and practical improvements.",
    week:"Explain the last 7 completed working days for this office. Identify repeated security/activity/coverage patterns and practical improvements.",
    monthly:"Explain the last 30 completed calendar days for this office, separating working and non-working patterns and surfacing repeated improvements."
  };
  const place=r.siteType==="retail"?"store":r.siteType;
  const typePrompts=r.isOffice&&r.siteType!=="office"?{
    daily:`Explain today's ${place} report so far. Lead with what needs attention, then ${r.profile.activityNoun||"activity"} and monitoring confidence. Suggest what to check; do not assert causes.`,
    yesterday:`Explain the last completed ${r.profile.dayNoun} report for this ${place}. Lead with security attention, notable quiet periods and monitoring confidence. A quiet period is an observation, not downtime or delay. Suggest what to check; do not assert causes.`,
    week:`Explain the last 7 completed ${r.profile.dayNoun}s for this ${place}. Identify repeated activity, gap and coverage patterns, and what to check next.`,
    monthly:`Explain the last 30 days for this ${place}, separating working and non-working patterns and what to check next.`
  }:officePrompts;
  const prompt=r.isChaiWalaRestaurant?(prompts[r.view]||prompts.daily):r.isOffice?(typePrompts[r.view]||typePrompts.daily):(r.view==="yesterday"?"Explain the last completed business-day report for this site.":"Explain this management report and tell me the priority action.");
  const periodHint=r.view==="daily"?"Today, in progress":r.view==="yesterday"?(r.snapshot?.report_date?dateLabel(r.snapshot.report_date):"Last completed day"):r.view==="week"?"Last 7 days":r.view==="monthly"?"Last 30 days":"Executive summary";

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
      askPrompt={prompt}
    />;
  }else if(r.isOffice){
    if(r.view==="daily"||r.view==="yesterday"){
      const dayNoun=r.profile.dayNoun;
      const dayLabel=r.view==="daily"?"Today":"Last completed "+dayNoun;
      const capitalDay=dayNoun.charAt(0).toUpperCase()+dayNoun.slice(1);
      // Site-type operations (timeline, areas, gaps, vehicles, checkout) replace the per-camera bars when the
      // site's cameras have configured purposes; otherwise the camera-level view stays.
      const zoned=Boolean(siteDay?.configuredZones?.length);
      const activity=<>
        <OfficeDayReport title={capitalDay+" activity"} data={r.officeDay} periodLabel={dayLabel} dayNoun={dayNoun} showAreas={!zoned} profile={r.profile} siteDay={siteDay}/>
        <SiteOperations profile={r.profile} day={siteDay} siteId={r.siteId} include={["timeline","zones","gaps","logistics","vehicles","checkout"]}/>
      </>;
      reportBody=<>
        {r.view==="yesterday"&&r.snapshot?<EvidenceReport snapshot={r.snapshot} siteId={r.siteId} activity={activity}/>
          :activity}
        {r.view==="yesterday"&&!r.snapshot&&<>
          <Empty title={"No saved management report is available for this "+r.profile.dayNoun+" yet."}/>
          <SavedReportHistory windowData={r.reportWindow} siteId={r.siteId} mode="latest"/>
        </>}
        <ManagementReading answer={r.answer} label={r.view==="daily"?"Today's management reading":"WatchLog's reading of the "+r.profile.dayNoun}/>
      </>;
    }else{
      const days=r.view==="week"?7:30;
      reportBody=<><OfficePeriodReport data={r.officePeriod} days={days} profile={r.profile}/><SavedReportHistory windowData={r.reportWindow} siteId={r.siteId}/><ManagementReading answer={r.answer} label={days===7?"What the last 7 "+r.profile.dayNoun+"s suggest":"What the last 30 days suggest"}/></>;
    }
  }else{
    reportBody=r.view==="yesterday"?(r.snapshot?<EvidenceReport snapshot={r.snapshot} siteId={r.siteId}/>:<Empty title="No saved management brief is available for the last completed business day yet.">The daily report appears here once the business day is complete.</Empty>)
      :<Section first title={label+" report"}>{r.answer?<div style={{fontSize:14,lineHeight:1.65}}><RichText text={r.answer}/></div>:<Empty title="No report is available yet."/>}</Section>;
  }

  const lead=r.isChaiWalaRestaurant?null:reportLead({...r,siteDay});
  const rr={...r,siteDay,prompt,periodLabel:label};
  const summaryCov=num(r.snapshot?.payload?.coverage?.coverage_ratio)??num(r.officeDay?.coverage?.coverage_ratio);
  return <OwnerPage active="Reports" email={r.email} siteId={r.siteId}
    kicker={["Reports",periodHint]}
    title={r.site?.name||"Management report"}
    actions={<a className="ow-btn quiet" href={withSite("/reports/delivery/",r.siteId)}>Delivery & recipients</a>}
    rail={r.isChaiWalaRestaurant||r.busy?null:<ReportRail r={rr} lead={lead}/>}
    summary={r.isChaiWalaRestaurant||r.busy?null:<Summary items={[
      {value:r.periodLabel||label,label:"Period"},
      {value:summaryCov==null?"Not verified":Math.round(summaryCov*100)+"%",label:"Monitoring coverage",muted:summaryCov==null,ledger:summaryCov},
    ]}/>}>
    {r.error&&<Notice tone="bad">{r.error}</Notice>}
    <div className="ow-tabs" role="tablist" aria-label="Reporting period">{views.map(([k,l])=><button type="button" role="tab" aria-selected={r.view===k} key={k} className="ow-tab" onClick={()=>r.setView(k)}>{l}</button>)}</div>
    {r.busy?<Loading label="Preparing your report"/>:<>
      {lead&&<Lead tone={lead.tone} title={lead.title} body={lead.body} action={(r.snapshot?.payload?.action_items?.length||r.snapshot?.payload?.priority_actions?.length)?<a className="ow-btn quiet" href="#recommended-action">Recommended action</a>:null}/>}
      {reportBody}
    </>}
  </OwnerPage>;
}
