"use client";
import {useEffect,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId} from "../site-context";

const REPORT_STYLE="Write this as a finished customer-facing management brief in plain, natural business language. Lead with what management needs to know and what needs action. Do not describe WatchLog's internal process or implementation. Do not use internal terms such as canonical dataset, frozen report, snapshot, frame, detector event, pixel verification, evidence class, RPC, provenance, pipeline, or tool result. Routine movement is not an incident.";
const PROMPTS={
  daily:`Give me today's management report. Cover serious security attention, office activity, important site observations, monitoring confidence and the priority action. ${REPORT_STYLE}`,
  monthly:`Summarize the last 30 days for management. Cover meaningful security patterns, recurring operational observations, restricted-area concerns, coverage confidence and the most important management actions. ${REPORT_STYLE}`,
  executive:`Give me a concise executive summary for leadership. State the security position, important operational patterns, anything requiring attention and the highest-priority action. ${REPORT_STYLE}`
};
const RESTAURANT_PROMPTS={
  daily:"Give me today's restaurant management report. Cover customer demand, occupied tables, estimated covers, observed time to food, service pressure, analytics quality, improvement recommendations, monitoring coverage and any security attention. Keep visible diners separate from unique footfall.",
  yesterday:"Give me the last completed service-day restaurant management report. Cover customer demand, occupied tables, estimated covers, observed time to food, service pressure, analytics quality, improvement recommendations, monitoring coverage and any security attention. Keep visible diners separate from unique footfall.",
  week:"Summarize the last 7 completed restaurant service days for management. Focus on demand patterns, floor and table utilization, estimated covers, observed time to food, handoff and kitchen pressure, analytics quality, coverage, repeated issues and practical improvements.",
  monthly:"Summarize the last 30 restaurant service days for management. Focus on weekly and weekday trends, demand by hour, floor and table utilization, estimated covers, observed time to food, service pressure, analytics quality, coverage, recurring issues and practical improvements."
};
const OFFICE_PROMPTS={
  daily:"Give me today's office management report. Lead with security attention, office activity, opening/closing status, after-hours exceptions, monitoring coverage and practical improvements. Use natural management language and do not call activity detections unique people.",
  yesterday:"Give me the last completed working-day office management report. Cover security attention, opening/closing activity, entrance/reception activity where mapping is confirmed, management/restricted activity where mapping is confirmed, after-hours exceptions, monitoring coverage and practical improvements.",
  week:"Summarize the last 7 completed working days for this office. Focus on incident/attention patterns, activity detections, after-hours exceptions, opening/closing consistency, monitoring coverage and repeated evidence-based improvements. Do not call detections unique people.",
  monthly:"Summarize the last 30 completed calendar days for this office, separating working-day and non-working-day patterns. Focus on security attention, after-hours exceptions, monitoring coverage, recurring activity patterns and practical improvements."
};
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
  const[restaurantPeriod,setRestaurantPeriod]=useState(null);
  const[restaurantConfig,setRestaurantConfig]=useState(null);
  const[officeDay,setOfficeDay]=useState(null);
  const[officePeriod,setOfficePeriod]=useState(null);
  const[siteContext,setSiteContext]=useState(null);
  const[busy,setBusy]=useState(true);
  const[error,setError]=useState("");

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
      setBusy(true);setAnswer("");setSnapshot(null);setReportWindow(null);setRestaurant(null);setRestaurantPeriod(null);setOfficeDay(null);setOfficePeriod(null);setError("");
      const sb=supabase();
      const [cfg,ctx]=await Promise.all([
        sb.rpc("wl_restaurant_site_config",{p_site_id:siteId}),
        sb.rpc("wl_my_site_context",{p_site_id:siteId})
      ]);
      if(!live)return;
      const restaurantEnabled=!cfg.error&&cfg.data?.enabled===true;
      const config=restaurantEnabled?(cfg.data||null):null;
      setRestaurantConfig(config);
      const context=!ctx.error?(ctx.data||null):null;
      setSiteContext(context);
      const chaiLayout=config?.report_layout_profile==="chaiwala_restaurant_ops_v1";
      const officeEnabled=context?.site_type==="office";
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
        const period=await sb.rpc("wl_office_period",{p_site_id:siteId,p_days:7,p_working_only:true});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setOfficePeriod(period.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:OFFICE_PROMPTS.week,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare the office review.");return}
        setAnswer(ai.data?.answer||"");
        return;
      }

      if(chaiLayout&&view==="week"){
        const period=await sb.rpc("wl_restaurant_period",{p_site_id:siteId,p_days:7,p_end_date:null});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setRestaurantPeriod(period.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:RESTAURANT_PROMPTS.week,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare the management reading.");return}
        setAnswer(ai.data?.answer||"");
        return;
      }

      if(officeLayout&&view==="monthly"){
        const period=await sb.rpc("wl_office_period",{p_site_id:siteId,p_days:30,p_working_only:false});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setOfficePeriod(period.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:OFFICE_PROMPTS.monthly,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare the office review.");return}
        setAnswer(ai.data?.answer||"");
        return;
      }

      if(chaiLayout&&view==="monthly"){
        const period=await sb.rpc("wl_restaurant_period",{p_site_id:siteId,p_days:30,p_end_date:null});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setRestaurantPeriod(period.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:RESTAURANT_PROMPTS.monthly,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare the management reading.");return}
        setAnswer(ai.data?.answer||"");
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
          const rr=await sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:date});
          if(!live)return;
          if(!rr.error)rest=rr.data||null;
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
        const completedReviewedRestaurant=chaiLayout&&report.data?.payload?.manual_business_report===true;
        if(!completedReviewedRestaurant&&(chaiLayout||officeLayout)){
          const prompt=chaiLayout?RESTAURANT_PROMPTS.yesterday:OFFICE_PROMPTS.yesterday;
          const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt,site_id:siteId,conversation_id:null}});
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
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:OFFICE_PROMPTS.daily,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare today's office report.");return}
        setAnswer(ai.data?.answer||"");
        return;
      }

      if(chaiLayout&&view==="daily"){
        const rr=await sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:null});
        if(!live)return;
        if(rr.error){setBusy(false);setError(say(rr.error));return}
        setRestaurant(rr.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:RESTAURANT_PROMPTS.daily,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(ai.error||ai.data?.error){setError(ai.data?.message||say(ai.error)||"WatchLog could not prepare the management reading.");return}
        setAnswer(ai.data?.answer||"");
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

  const isChaiWalaRestaurant=restaurantConfig?.report_layout_profile==="chaiwala_restaurant_ops_v1";
  const isOffice=siteContext?.site_type==="office";
  return{email,siteId,site,view,setView,requestedReportDate,answer,snapshot,reportWindow,restaurant,restaurantPeriod,restaurantConfig,isChaiWalaRestaurant,officeDay,officePeriod,siteContext,isOffice,busy,error};
}
