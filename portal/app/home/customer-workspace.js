"use client";

import {useCallback,useEffect,useState} from "react";
import {supabase} from "../../lib/supabase";
import {Nav,requireTenant} from "../shell";
import {rememberSite,selectedSiteId,withSite} from "../site-context";
import styles from "./home.module.css";

function pct(v){
  if(v===null||v===undefined||v==="")return null;
  const n=Number(v);
  return Number.isFinite(n)?Math.round(Math.max(0,Math.min(1,n))*100):null;
}
function sev(v){return String(v||"info").toLowerCase()}
function needsAttention(x){return ["critical","warning","attention"].includes(sev(x&&x.severity))}
function n(v){return Number(v||0).toLocaleString()}
function when(ts,timeZone){
  if(!ts)return "";
  try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",year:"numeric",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(ts))+" · site time"}
  catch{return String(ts)}
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

function metric(v){
  if(v===null||v===undefined||v==="")return null;
  const x=Number(v);
  return Number.isFinite(x)?x:null;
}
function rounded(v){
  const x=Math.abs(Number(v));
  return Number.isInteger(x)?String(x):x.toFixed(1);
}
function comparisonView(windowData){
  const period=windowData&&windowData.structured_restaurant_metrics;
  if(!period||period.enabled!==true)return null;
  const current=period.summary||{},previous=period.previous_period||{},delta=period.comparison||{};
  const expected=Math.max(1,Number(current.expected_service_days||period.days||7));
  const observed=Math.max(0,Number(current.observed_service_days||0));
  const previousObserved=Math.max(0,Number(previous.observed_service_days||0));
  const minimum=Math.min(4,expected);
  const qualifier="Based on "+observed+" of "+expected+" observed service days versus "+previousObserved+" of "+expected+" previously. Missing periods are not treated as zero activity.";
  if(observed<minimum||previousObserved<minimum)return{ready:false,items:[],qualifier};

  const items=[];
  const covers=metric(delta.estimated_covers_pct);
  if(covers!==null&&Math.abs(covers)>=5){
    items.push("Estimated covers were "+rounded(covers)+"% "+(covers>0?"higher":"lower")+" than the previous "+expected+"-day period.");
  }
  const service=metric(delta.median_time_to_food_delta_minutes);
  if(service!==null&&Math.abs(service)>=1){
    items.push("Median observed time to food was "+rounded(service)+" minute"+(Math.abs(service)===1?"":"s")+" "+(service<0?"faster":"slower")+" than the previous period.");
  }
  const coverageDelta=metric(delta.coverage_delta_points);
  if(coverageDelta!==null&&Math.abs(coverageDelta)>=5){
    items.push("Monitoring coverage was "+rounded(coverageDelta)+" percentage points "+(coverageDelta>0?"higher":"lower")+" than the previous period.");
  }
  if(!items.length)items.push("No material change stands out in the supported comparison.");
  return{ready:true,items:items.slice(0,3),qualifier};
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
  const[busy,setBusy]=useState(true);
  const[partial,setPartial]=useState(false);
  const[error,setError]=useState("");

  const loadSite=useCallback(async(id)=>{
    if(!id)return;
    setBusy(true);setPartial(false);setError("");
    const sb=supabase();
    setRestaurantConfig(null);setLatestReport(null);setLatestReportDate("");setReportWindow(null);
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
    if(isRestaurant){
      const resolved=await sb.rpc("wl_my_last_completed_business_date",{p_site_id:id});
      const reportDate=!resolved.error&&resolved.data?String(resolved.data):"";
      if(reportDate){
        const[snap,windowResult]=await Promise.all([
          sb.rpc("wl_my_report_snapshot",{p_site_id:id,p_date:reportDate}),
          sb.rpc("wl_my_report_window",{p_site_id:id,p_days:7,p_end_date:reportDate})
        ]);
        if(!snap.error){setLatestReport(snap.data||null);setLatestReportDate(reportDate)}
        else setPartial(true);
        if(!windowResult.error)setReportWindow(windowResult.data||null);
      }
    }
    if(c.error||q.error||d.error||a.error||rc.error)setPartial(true);
    setBusy(false);
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
  const cams=((ctx&&ctx.cameras)||[]).filter(function(x){return x.monitor});
  const healthy=cams.filter(function(x){return String(x.health_state||"").toLowerCase()==="operational"}).length;
  const connected=Boolean(ctx&&ctx.connectivity&&ctx.connectivity.agent_online);
  const seen=Boolean(ctx&&ctx.connectivity&&ctx.connectivity.last_seen);
  const faults=(ctx&&ctx.faults)||[];
  const unread=items.filter(function(x){return !x.read});
  const attention=unread.filter(needsAttention);
  const urgent=attention.filter(function(x){return sev(x.severity)==="critical"});
  const currentCoverage=pct(daily&&daily.coverage&&daily.coverage.coverage_ratio);
  const completedCoverage=pct(latestReport&&latestReport.payload&&latestReport.payload.coverage&&latestReport.payload.coverage.coverage_ratio);
  const coverage=currentCoverage!==null?currentCoverage:completedCoverage;
  const coverageScope=currentCoverage!==null?"current reporting period":completedCoverage!==null?"latest completed service day":"current reporting period";
  const completedMetrics=((latestReport&&latestReport.payload&&latestReport.payload.metrics)||[]).slice(0,4);
  const completedHighlights=((latestReport&&latestReport.payload&&latestReport.payload.highlights)||[]).slice(0,3);
  const change=comparisonView(reportWindow);

  const summary=(analytics&&analytics.summary)||{};
  const ruleCount=((analytics&&analytics.by_rule)||[]).length;
  const activity=Number(summary.visitor_in||0)+Number(summary.vehicles_in||0)+Number(summary.zone_entries||0);
  const afterHours=Number(summary.after_hours||0);
  const analyticsReady=ruleCount>0||activity>0||afterHours>0;

  let title="No current issue is reported.";
  let copy="Monitoring coverage for the current period is not verified yet. Anything WatchLog cannot confirm remains clearly marked.";
  let tone="neutral";
  if(!seen&&!connected){
    title="WatchLog is getting this site ready.";
    copy="Monitoring information will appear here after this site connects.";
  }else if(!connected){
    title="Monitoring needs attention.";
    copy="WatchLog is not currently connected to this site, so the current picture may be incomplete.";
    tone="bad";
  }else if(urgent.length){
    title=String(urgent.length)+" urgent "+(urgent.length===1?"item needs":"items need")+" review.";
    copy="Review the priority items below first.";
    tone="bad";
  }else if(attention.length){
    title=String(attention.length)+" "+(attention.length===1?"item needs":"items need")+" your attention.";
    copy="WatchLog has highlighted the items worth reviewing first.";
    tone="warn";
  }else if(faults.length){
    title="Monitoring needs attention.";
    copy=String(faults.length)+" monitoring "+(faults.length===1?"item needs":"items need")+" checking.";
    tone="warn";
  }else if(coverage!==null&&coverage<100){
    title="Monitoring coverage is incomplete.";
    copy="Monitoring coverage is "+coverage+"% for the "+coverageScope+". Unverified time is not treated as quiet time.";
    tone="warn";
  }else if(coverage!==null){
    title="Nothing needs your attention right now.";
    copy="Monitoring coverage is "+coverage+"% for the "+coverageScope+", with no current issue reported.";
    tone="good";
  }

  return <div className="shell">
    <Nav active="Home" email={email} currentSiteId={siteId}/>
    <main className={"main "+styles.page}>
      <header className={styles.top}>
        <div><div className={styles.eyebrow}>WatchLog overview</div><h1>{(site&&site.name)||"Your site"}</h1><p>What matters now, what needs attention, and what WatchLog can verify.</p></div>
        <div className={styles.actions}>
          {sites.length>1&&<select aria-label="Choose site" value={siteId} onChange={function(e){choose(e.target.value)}}>{sites.map(function(x){return <option key={x.id} value={x.id}>{x.name}</option>})}</select>}
          <a className={styles.secondary} href={withSite("/reports/?view=yesterday",siteId)}>Latest report</a>
          <a className={styles.primary} href={withSite("/ai/",siteId)}>Ask WatchLog</a>
        </div>
      </header>

      {error&&<div className={styles.error} role="alert">{error}</div>}
      {partial&&<div className={styles.partial} role="status">Some parts of this overview could not be refreshed. Available information is shown below.</div>}

      {busy?<div className={styles.loading} role="status" aria-label="Loading WatchLog overview"><span/><span/><span/></div>:<>
        <section className={styles.hero+" "+(styles[tone]||"")} aria-live="polite">
          <i/>
          <div><span>Right now</span><h2>{title}</h2><p>{copy}</p></div>
          <a href={withSite(attention.length?"/notifications/":"/site-health/",siteId)}>{attention.length?"Review attention":"Check monitoring"}</a>
        </section>

        <section className={styles.metrics}>
          <div><span>Needs attention</span><strong>{attention.length}</strong><small>{attention.length?"new item"+(attention.length===1?"":"s"):"nothing new"}</small></div>
          <div><span>Monitoring coverage</span><strong>{coverage===null?"—":String(coverage)+"%"}</strong><small>{coverage===null?"not verified yet":coverageScope}</small></div>
          <div><span>Cameras</span><strong>{cams.length?String(healthy)+"/"+String(cams.length):"—"}</strong><small>{cams.length?"confirmed healthy":"not configured"}</small></div>
          <div><span>{restaurantConfig&&completedMetrics.length?(completedMetrics[0].label||"Latest business insight"):"Measured activity"}</span><strong>{restaurantConfig&&completedMetrics.length?String(completedMetrics[0].value??"—"):analyticsReady?n(activity):"—"}</strong><small>{restaurantConfig&&completedMetrics.length?"latest completed service day":analyticsReady?"last 24 hours":"not configured yet"}</small></div>
        </section>

        <section className={styles.section}>
          <div className={styles.sectionHead}><div><span>Attention</span><h2>What needs a look</h2></div><a href={withSite("/notifications/",siteId)}>View all</a></div>
          {attention.length?<div className={styles.list}>{attention.slice(0,4).map(function(item){
            const s=sev(item.severity);
            return <article key={String(item.kind)+":"+String(item.id)}><em className={styles[s]||styles.warning}>{s==="critical"?"Urgent":"Attention"}</em><div><h3>{item.title}</h3><p>{item.body}</p><small>{when(item.created_at,site?.timezone)}</small></div><a href={item.href||withSite("/notifications/",siteId)}>{item.kind==="health"?"Check monitoring":item.kind==="report"?"Open report":"Review"}</a></article>
          })}</div>:faults.length?<div className={styles.list}>{faults.slice(0,3).map(function(f,i){return <article key={f.id||i}><em className={styles.warning}>Attention</em><div><h3>{f.camera||"Site monitoring"}</h3><p>{faultLabel(f)}</p></div><a href={withSite("/site-health/",siteId)}>Check monitoring</a></article>})}</div>:<div className={styles.clear}><b>✓</b><div><h3>Nothing needs your attention right now.</h3><p>No current issue is reported. Anything WatchLog cannot verify remains marked clearly.</p></div></div>}
        </section>

        <section className={styles.split}>
          <div className={styles.panel}>
            <div className={styles.sectionHead}><div><span>Business activity</span><h2>{restaurantConfig&&completedMetrics.length?"Latest completed service day":"Recent activity"}</h2></div><a href={withSite("/analytics/",siteId)}>Open insights</a></div>
            {restaurantConfig&&completedMetrics.length?<><div className={styles.facts}>
              {completedMetrics.map(function(m,i){return <div key={(m.label||i)+"-"+i}><strong>{m.value==null?"—":String(m.value)}</strong><span>{m.label||"Business metric"}</span></div>})}
            </div>{completedHighlights.length?<p className={styles.businessSummary}>{completedHighlights[0]}</p>:null}<a className={styles.inlineLink} href={withSite("/reports/?view=yesterday"+(latestReportDate?"&date="+encodeURIComponent(latestReportDate):""),siteId)}>Open completed service-day report →</a></>:analyticsReady?<div className={styles.facts}>
              <div><strong>{n(summary.visitor_in)}</strong><span>Visitor entries</span></div>
              <div><strong>{n(summary.vehicles_in)}</strong><span>Vehicle entries</span></div>
              <div><strong>{n(summary.zone_entries)}</strong><span>Area entries</span></div>
              <div><strong>{n(afterHours)}</strong><span>After-hours activity</span></div>
            </div>:<div className={styles.empty}><h3>Business activity insights are not ready yet.</h3><p>WatchLog will show measured patterns here when this site has supported activity insights.</p><a href={restaurantConfig?withSite("/reports/?view=yesterday",siteId):withSite("/analytics/studio/",siteId)}>{restaurantConfig?"Open latest report":"Choose what to measure"}</a></div>}
          </div>

          <aside className={styles.coverage}>
            <div className={styles.sectionHead}><div><span>Monitoring confidence</span><h2>Can I trust this picture?</h2></div><a href={withSite("/site-health/",siteId)}>Details</a></div>
            <strong>{coverage===null?"Not verified":String(coverage)+"%"}</strong>
            <p>{coverage===null?"WatchLog does not have a verified coverage figure for this view yet.":coverage===100?"The "+coverageScope+" is fully represented.":"WatchLog verified "+coverage+"% of the "+coverageScope+". Unverified time is not treated as quiet time."}</p>
            <div className={styles.health}><i className={connected?styles.dotGood:seen?styles.dotBad:styles.dotUnknown}/><div><b>{connected?"Site connected":seen?"Site connection unavailable":"Site not connected yet"}</b><small>{cams.length?String(healthy)+" of "+String(cams.length)+" monitored cameras confirmed healthy":"Camera health will appear after setup"}</small></div></div>
          </aside>
        </section>

        {restaurantConfig&&change&&<section className={styles.change}>
          <div className={styles.sectionHead}><div><span>What changed</span><h2>Last 7 service days vs previous 7</h2></div><a href={withSite("/analytics/",siteId)}>Open insights</a></div>
          {change.ready?<div className={styles.changeList}>{change.items.map(function(item,i){return <div className={styles.changeItem} key={item}><span>{String(i+1).padStart(2,"0")}</span><p>{item}</p></div>})}</div>:<div className={styles.changeEmpty}><h3>A reliable comparison is not ready yet.</h3><p>WatchLog will compare completed service periods after enough service days are observed in both periods.</p></div>}
          <p className={styles.changeQualifier}>{change.qualifier}</p>
        </section>}

        <section className={styles.ask}>
          <div><span>Ask WatchLog</span><h2>Need something specific?</h2><p>Ask a direct question about this site and continue from the supporting information.</p></div>
          <div>{[
            "What needs my attention right now?",
            "What happened during the latest completed business day?",
            "Was the latest reporting period fully monitored?"
          ].map(function(prompt){return <a key={prompt} href={withSite("/ai/?prompt="+encodeURIComponent(prompt),siteId)}>{prompt}<b>→</b></a>})}</div>
        </section>
      </>}
    </main>
  </div>;
}
