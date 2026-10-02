"use client";
import {useEffect,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId} from "../site-context";
import {siteProfile,selectSiteProfile} from "../owner/site-profiles";

const REPORT_STYLE="Write this as a finished customer-facing management brief in plain, natural business language. Lead with what management needs to know and what needs action. Do not describe WatchLog's internal process or implementation. Do not use internal terms such as canonical dataset, frozen report, snapshot, frame, detector event, pixel verification, evidence class, RPC, provenance, pipeline, or tool result. Routine movement is not an incident.";
const PROMPTS={
  daily:`Give me today's management report. Cover serious security attention, office activity, important site observations, monitoring confidence and the priority action. ${REPORT_STYLE}`,
  monthly:`Summarize the last 30 days for management. Cover meaningful security patterns, recurring operational observations, restricted-area concerns, coverage confidence and the most important management actions. ${REPORT_STYLE}`,
  executive:`Give me a concise executive summary for leadership. State the security position, important operational patterns, anything requiring attention and the highest-priority action. ${REPORT_STYLE}`
};
const RESTAURANT_PROMPTS={
  daily:"Explain today's restaurant management view in plain business language. Lead with what is happening now, any security attention and the next justified action. Keep estimates clearly labelled and do not discuss WatchLog's internal review method.",
  yesterday:"Explain the completed restaurant service day in plain business language. Lead with what mattered, any security exception and the most important management action. Keep estimates clearly labelled.",
  week:"Explain the weekly restaurant management view. Focus on repeated demand/service patterns, security exceptions and justified actions. Compare with the prior week only when the periods are genuinely comparable.",
  monthly:"Explain the monthly restaurant management view. Focus on patterns that are supported by enough represented days, recurring security exceptions and justified actions. Do not present sparse data as a full-month trend."
};
const OFFICE_PROMPTS={
  daily:"Give me today's office management report. Lead with security attention, office activity, opening/closing status, after-hours exceptions, monitoring coverage and practical improvements. Use natural management language and do not call activity detections unique people.",
  yesterday:"Give me the last completed working-day office management report. Cover security attention, opening/closing activity, entrance/reception activity where mapping is confirmed, management/restricted activity where mapping is confirmed, after-hours exceptions, monitoring coverage and practical improvements.",
  week:"Summarize the last 7 completed working days for this office. Focus on incident/attention patterns, activity detections, after-hours exceptions, opening/closing consistency, monitoring coverage and repeated evidence-based improvements. Do not call detections unique people.",
  monthly:"Summarize the last 30 completed calendar days for this office, separating working-day and non-working-day patterns. Focus on security attention, after-hours exceptions, monitoring coverage, recurring activity patterns and practical improvements."
};
// Office, warehouse, factory and retail sites share the governed working-day reporting model
// (wl_my_daily_intelligence + the profile's governed period: wl_office_period for offices, the site-neutral
// wl_site_period for warehouse/factory/retail); each site type changes the questions asked of it.
const TYPE_FOCUS={
  warehouse:"Focus on receiving, loading-dock and dispatch activity, notable quiet periods at operational areas (observations, not proof of delay), vehicle episodes at gate and dock cameras, restricted storage access and monitoring coverage. Dock activity is not shipments or orders. Suggest what to check; do not assert a cause.",
  factory:"Focus on when production-area activity was first and last observed, notable quiet periods in production areas (observations, not downtime), material movement, restricted and maintenance access and monitoring coverage. Activity is not production output or machine uptime. Suggest what to check; do not assert a cause.",
  retail:"Focus on store activity by hour, the busiest and quietest areas, entrance versus checkout activity, after-hours stock-room access and monitoring coverage. Activity is not sales, transactions or unique customers. Suggest what to check; do not assert a cause."
};
function typePrompts(type){
  if(!TYPE_FOCUS[type])return OFFICE_PROMPTS;
  const p=siteProfile(type),f=TYPE_FOCUS[type],place=type==="retail"?"store":type;
  return{
    daily:`Give me today's ${place} management report so far. Lead with security attention. ${f}`,
    yesterday:`Give me the last completed ${p.dayNoun} report for this ${place}. Lead with security attention. ${f}`,
    week:`Summarize the last 7 completed ${p.dayNoun}s for this ${place}. ${f} Compare with the previous period only when it is genuinely comparable.`,
    monthly:`Summarize the last 30 days for this ${place}, separating working and non-working days. ${f}`
  };
}
const VALID_VIEWS=new Set(["daily","yesterday","week","monthly","executive"]);

function dateInZone(timeZone,offsetDays=0){
  const now=new Date(Date.now()+offsetDays*86400000);
  try{
    const parts=new Intl.DateTimeFormat("en-US",{timeZone:timeZone||"Asia/Karachi",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(now);
    const v=Object.fromEntries(parts.filter(x=>x.type!=="literal").map(x=>[x.type,x.value]));
    return `${v.year}-${v.month}-${v.day}`;
  }catch{return now.toISOString().slice(0,10)}
}
function shiftDate(iso,days){
  const d=new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate()+days);
  return d.toISOString().slice(0,10);
}
function allowedSite(list,id){return Boolean(id)&&list.some(s=>String(s.id)===String(id))}

export default function useReport(){
  const[email,setEmail]=useState("");
  const[siteId,setSiteId]=useState("");
  const[site,setSite]=useState(null);
  const[view,setView]=useState("yesterday");
  const[requestedReportDate,setRequestedReportDate]=useState("");
  const[answer,setAnswer]=useState("");
  const[snapshot,setSnapshot]=useState(null);
  const[reportWindow,setReportWindow]=useState(null);
  const[restaurant,setRestaurant]=useState(null);
  const[restaurantSecurity,setRestaurantSecurity]=useState(null);
  const[restaurantPeriod,setRestaurantPeriod]=useState(null);
  const[restaurantConfig,setRestaurantConfig]=useState(null);
  const[officeDay,setOfficeDay]=useState(null);
  const[officePeriod,setOfficePeriod]=useState(null);
  const[siteContext,setSiteContext]=useState(null);
  const[siteAi,setSiteAi]=useState(null);
  const[busy,setBusy]=useState(true);
  const[error,setError]=useState("");

  function selectView(next){
    if(!VALID_VIEWS.has(next))return;
    setView(next);
    setRequestedReportDate("");
    try{
      const params=new URLSearchParams(location.search);
      params.set("view",next);
      params.delete("date");
      history.replaceState(null,"",location.pathname+"?"+params.toString()+(location.hash||""));
    }catch{}
  }

  useEffect(()=>{let live=true;(async()=>{
    const g=await requireTenant();if(!g||!live)return;
    setEmail(g.session.user.email||"");
    const sb=supabase();
    const sitesResult=await sb.rpc("wl_sites");
    if(!live)return;
    if(sitesResult.error){setError(say(sitesResult.error));setBusy(false);return}
    const sites=sitesResult.data||[];
    const params=new URLSearchParams(location.search),requestedView=params.get("view");
    if(VALID_VIEWS.has(requestedView))setView(requestedView);
    const requestedDate=params.get("date")||"";
    if(/^\d{4}-\d{2}-\d{2}$/.test(requestedDate))setRequestedReportDate(requestedDate);
    const requestedSite=params.get("site")||selectedSiteId()||"";
    const resolvedSite=allowedSite(sites,requestedSite)?requestedSite:(sites[0]?.id||"");
    if(!resolvedSite){setError("No site is available for this account yet.");setBusy(false);return}
    const resolved=sites.find(s=>String(s.id)===String(resolvedSite))||null;
    rememberSite(resolvedSite,resolved?.name||"");
    setSiteId(resolvedSite);setSite(resolved);
    if(requestedSite!==resolvedSite){
      params.set("site",resolvedSite);
      if(!params.get("view"))params.set("view",requestedView||"yesterday");
      history.replaceState(null,"",`${location.pathname}?${params.toString()}${location.hash||""}`);
    }
  })().catch(e=>{if(live){setError(say(e)||"WatchLog could not load this report.");setBusy(false)}});return()=>{live=false}},[]);

  useEffect(()=>{
    if(!siteId)return;
    let live=true;
    (async()=>{
      setBusy(true);setAnswer("");setSnapshot(null);setReportWindow(null);setRestaurant(null);setRestaurantSecurity(null);setRestaurantPeriod(null);setOfficeDay(null);setOfficePeriod(null);setError("");
      const sb=supabase();
      const [cfg,ctx,aiCtx]=await Promise.all([
        sb.rpc("wl_restaurant_site_config",{p_site_id:siteId}),
        sb.rpc("wl_my_site_context",{p_site_id:siteId}),
        sb.rpc("wl_ai_context",{p_site_id:siteId})
      ]);
      if(!live)return;
      const restaurantEnabled=!cfg.error&&cfg.data?.enabled===true;
      const config=restaurantEnabled?(cfg.data||null):null;
      setRestaurantConfig(config);
      const context=!ctx.error?(ctx.data||null):null;
      setSiteContext(context);
      setSiteAi(!aiCtx.error?(aiCtx.data||null):null);
      // One registry selects the profile and composer for every site type, restaurant included.
      const selected=selectSiteProfile({restaurantConfig:config,contextType:context?.site_type,studioType:site?.site_type});
      const chaiLayout=selected.composer==="restaurant"&&config?.report_layout_profile==="chaiwala_restaurant_ops_v1";
      const businessType=selected.composer==="business"?selected.key:null;
      const officeEnabled=Boolean(businessType);
      // A period source that is not available yet (e.g. wl_site_period before its migration) is "not
      // available", never an error page; offices keep failing loudly on their established period.
      async function loadPeriod(window){
        const res=await sb.rpc(selected.period.rpc,{p_site_id:siteId,...window});
        if(res.error&&selected.period.rpc!=="wl_office_period")return{data:{enabled:false,site_type:businessType},error:null};
        return res;
      }
      const OFFICE_PROMPTS_FOR=typePrompts(businessType);
      const officeLayout=officeEnabled&&(context?.reporting_prefs?.report_layout_profile==="office_ops_v1"||true);

      let savedWindow=null;
      if(view==="yesterday"||view==="week"||view==="monthly"){
        const windowDays=view==="monthly"?30:7;
        const windowEnd=view==="yesterday"&&requestedReportDate?requestedReportDate:null;
        const wr=await sb.rpc("wl_my_report_window",{p_site_id:siteId,p_days:windowDays,p_end_date:windowEnd});
        if(!live)return;
        if(!wr.error){
          savedWindow=wr.data||null;
          setReportWindow(savedWindow);
        }
      }

      if(officeLayout&&view==="week"){
        const period=await loadPeriod({p_days:7,p_working_only:true});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setOfficePeriod(period.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:OFFICE_PROMPTS_FOR.week,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare the office review.");return}
        setAnswer(ai.data?.answer||"");
        return;
      }

      if(chaiLayout&&view==="week"){
        const structured=savedWindow?.structured_restaurant_metrics||null;
        if(structured){
          setRestaurantPeriod(structured);
          setBusy(false);
          return;
        }
        const period=await sb.rpc("wl_restaurant_period",{p_site_id:siteId,p_days:7,p_end_date:null});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setRestaurantPeriod(period.data||null);
        setBusy(false);
        return;
      }

      if(officeLayout&&view==="monthly"){
        const period=await loadPeriod({p_days:30,p_working_only:false});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setOfficePeriod(period.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:OFFICE_PROMPTS_FOR.monthly,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare the office review.");return}
        setAnswer(ai.data?.answer||"");
        return;
      }

      if(chaiLayout&&view==="monthly"){
        const structured=savedWindow?.structured_restaurant_metrics||null;
        if(structured){
          setRestaurantPeriod(structured);
          setBusy(false);
          return;
        }
        const period=await sb.rpc("wl_restaurant_period",{p_site_id:siteId,p_days:30,p_end_date:null});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setRestaurantPeriod(period.data||null);
        setBusy(false);
        return;
      }

      if(view==="yesterday"){
        let date=requestedReportDate||dateInZone(site?.timezone||"Asia/Karachi",-1),rest=null;
        if(!requestedReportDate){
          const resolved=await sb.rpc("wl_my_last_completed_business_date",{p_site_id:siteId});
          if(!live)return;
          if(!resolved.error&&resolved.data)date=String(resolved.data);
        }
        if(restaurantEnabled){
          const [rr,securityDay]=await Promise.all([
            sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:date}),
            sb.rpc("wl_my_daily_intelligence",{p_site_id:siteId,p_date:date})
          ]);
          if(!live)return;
          if(!rr.error)rest=rr.data||null;
          if(!securityDay.error){
            const d=securityDay.data||{};
            setRestaurantSecurity({incidents:d.incidents||[],attention:d.attention||{},coverage:d.coverage||{}});
          }
        }
        if(officeEnabled){
          const od=await sb.rpc("wl_my_daily_intelligence",{p_site_id:siteId,p_date:date});
          if(!live)return;
          if(od.error){setBusy(false);setError(say(od.error));return}
          setOfficeDay(od.data||null);
        }
        const report=await sb.rpc("wl_my_report_snapshot",{p_site_id:siteId,p_date:date});
        if(!live)return;
        if(!report.error)setSnapshot(report.data||null);
        setRestaurant(report.data?(report.data?.payload?.restaurant||rest):rest);
        if(chaiLayout){
          setBusy(false);
          return;
        }
        if(officeLayout){
          const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:OFFICE_PROMPTS_FOR.yesterday,site_id:siteId,conversation_id:null}});
          if(!live)return;
          if(ai.error||ai.data?.error){setBusy(false);setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare the management reading.");return}
          setAnswer(ai.data?.answer||"");
        }
        setBusy(false);
        return;
      }

      if(officeLayout&&view==="daily"){
        const od=await sb.rpc("wl_my_daily_intelligence",{p_site_id:siteId,p_date:null});
        if(!live)return;
        if(od.error){setBusy(false);setError(say(od.error));return}
        setOfficeDay(od.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:OFFICE_PROMPTS_FOR.daily,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare today's office report.");return}
        setAnswer(ai.data?.answer||"");
        return;
      }

      if(chaiLayout&&view==="daily"){
        const [rr,securityDay]=await Promise.all([
          sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:null}),
          sb.rpc("wl_my_daily_intelligence",{p_site_id:siteId,p_date:null})
        ]);
        if(!live)return;
        if(rr.error){setBusy(false);setError(say(rr.error));return}
        setRestaurant(rr.data||null);
        if(!securityDay.error){
          const d=securityDay.data||{};
          setRestaurantSecurity({incidents:d.incidents||[],attention:d.attention||{},coverage:d.coverage||{}});
        }
        setBusy(false);
        return;
      }

      const prompt=PROMPTS[view]||PROMPTS.daily;
      const r=await sb.functions.invoke("watchlog-ai",{body:{prompt,site_id:siteId,conversation_id:null}});
      if(!live)return;
      setBusy(false);
      if(r.error||r.data?.error){setError(r.data?.message||say(r.error)||"WatchLog could not load this report.");return}
      setAnswer(r.data.answer||"");
    })();
    return()=>{live=false};
  },[siteId,site?.timezone,view,requestedReportDate]);

  const profile=selectSiteProfile({restaurantConfig,contextType:siteContext?.site_type,studioType:site?.site_type});
  const isChaiWalaRestaurant=profile.composer==="restaurant"&&restaurantConfig?.report_layout_profile==="chaiwala_restaurant_ops_v1";
  // isOffice: the site uses the office-model working-day reports (office, warehouse, factory, retail).
  const siteType=profile.key;
  const isOffice=profile.composer==="business";
  return{siteType,profile,siteAi,email,siteId,site,view,setView:selectView,requestedReportDate,answer,snapshot,reportWindow,restaurant,restaurantSecurity,restaurantPeriod,restaurantConfig,isChaiWalaRestaurant,officeDay,officePeriod,siteContext,isOffice,busy,error};
}
