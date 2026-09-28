"use client";
import {useEffect,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId} from "../site-context";

const PROMPTS={
  daily:"Give me today's restaurant management report. Cover customer demand, occupied tables, estimated covers, observed time to food, service pressure, monitoring coverage and any security attention. Keep visible diners separate from unique footfall.",
  yesterday:"Give me yesterday's restaurant management report. Cover customer demand, occupied tables, estimated covers, observed time to food, service pressure, monitoring coverage and any security attention. Keep visible diners separate from unique footfall.",
  week:"Summarize the last 7 restaurant service days for management. Focus on demand patterns, floor and table utilization, estimated covers, observed time to food, handoff and kitchen pressure, coverage, repeated issues and practical improvements.",
  monthly:"Summarize the last 30 restaurant service days for management. Focus on weekly and weekday trends, demand by hour, floor and table utilization, estimated covers, observed time to food, service pressure, coverage, recurring issues and practical improvements.",
  executive:"Give me a concise executive summary: monitoring reliability, security attention, meaningful patterns and the highest-priority action."
};
const VALID_VIEWS=new Set(["daily","yesterday","week","monthly","executive"]);

function localDate(timeZone){
  try{
    const parts=new Intl.DateTimeFormat("en-CA",{timeZone,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());
    const v=Object.fromEntries(parts.filter(x=>x.type!=="literal").map(x=>[x.type,x.value]));
    return `${v.year}-${v.month}-${v.day}`;
  }catch{return new Date().toISOString().slice(0,10)}
}
function shiftDate(iso,days){
  const d=new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate()+days);
  return d.toISOString().slice(0,10);
}

export default function useReport(){
  const[email,setEmail]=useState("");
  const[siteId,setSiteId]=useState("");
  const[site,setSite]=useState(null);
  const[view,setView]=useState("daily");
  const[answer,setAnswer]=useState("");
  const[snapshot,setSnapshot]=useState(null);
  const[restaurant,setRestaurant]=useState(null);
  const[restaurantPeriod,setRestaurantPeriod]=useState(null);
  const[restaurantConfig,setRestaurantConfig]=useState(null);
  const[busy,setBusy]=useState(false);
  const[error,setError]=useState("");

  useEffect(()=>{(async()=>{
    const g=await requireTenant();if(!g)return;
    setEmail(g.session.user.email||"");
    const sb=supabase(),sites=await sb.rpc("wl_sites");
    if(sites.error){setError(say(sites.error));return}
    const list=sites.data||[],params=new URLSearchParams(location.search),requested=params.get("view");
    const id=params.get("site")||selectedSiteId()||list[0]?.id||"";
    const selected=list.find(x=>x.id===id)||null;
    setSiteId(id);setSite(selected);
    if(id)rememberSite(id,selected?.name||"");
    if(VALID_VIEWS.has(requested))setView(requested);
  })()},[]);

  useEffect(()=>{
    if(!siteId)return;
    let live=true;
    (async()=>{
      setBusy(true);setAnswer("");setSnapshot(null);setRestaurant(null);setRestaurantPeriod(null);setError("");
      const sb=supabase();
      const cfg=await sb.rpc("wl_restaurant_site_config",{p_site_id:siteId});
      if(!live)return;
      const restaurantEnabled=!cfg.error&&cfg.data?.enabled===true;
      const config=restaurantEnabled?(cfg.data||null):null;
      setRestaurantConfig(config);
      const chaiLayout=config?.report_layout_profile==="chaiwala_restaurant_ops_v1";

      if(chaiLayout&&view==="week"){
        const period=await sb.rpc("wl_restaurant_period",{p_site_id:siteId,p_days:7,p_end_date:null});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setRestaurantPeriod(period.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:PROMPTS.week,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(!ai.error&&!ai.data?.error)setAnswer(ai.data?.answer||"");
        return;
      }

      if(chaiLayout&&view==="monthly"){
        const period=await sb.rpc("wl_restaurant_period",{p_site_id:siteId,p_days:30,p_end_date:null});
        if(!live)return;
        if(period.error){setBusy(false);setError(say(period.error));return}
        setRestaurantPeriod(period.data||null);
        const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:PROMPTS.monthly,site_id:siteId,conversation_id:null}});
        if(!live)return;
        setBusy(false);
        if(!ai.error&&!ai.data?.error)setAnswer(ai.data?.answer||"");
        return;
      }

      if(view==="yesterday"){
        let date=shiftDate(localDate(site?.timezone||"UTC"),-1),rest=null;
        if(restaurantEnabled){
          const current=await sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:null});
          if(!live)return;
          if(!current.error&&current.data?.service_date){
            date=shiftDate(current.data.service_date,-1);
            const rr=await sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:date});
            if(!live)return;
            if(!rr.error)rest=rr.data||null;
          }
        }
        const report=await sb.rpc("wl_my_report_snapshot",{p_site_id:siteId,p_date:date});
        if(!live)return;
        if(report.error){setBusy(false);setError(say(report.error));return}
        setSnapshot(report.data||null);
        setRestaurant(report.data ? (report.data?.payload?.restaurant||rest) : rest);
        if(chaiLayout){
          const ai=await sb.functions.invoke("watchlog-ai",{body:{prompt:PROMPTS.yesterday,site_id:siteId,conversation_id:null}});
          if(!live)return;
          if(!ai.error&&!ai.data?.error)setAnswer(ai.data?.answer||"");
        }
        setBusy(false);
        return;
      }

      if(view==="daily"&&restaurantEnabled){
        const rr=await sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:null});
        if(!live)return;
        if(!rr.error)setRestaurant(rr.data||null);
      }

      const prompt=PROMPTS[view]||PROMPTS.daily;
      const r=await sb.functions.invoke("watchlog-ai",{body:{prompt,site_id:siteId,conversation_id:null}});
      if(!live)return;
      setBusy(false);
      if(r.error||r.data?.error){setError(r.data?.message||say(r.error)||"WatchLog could not load this report.");return}
      setAnswer(r.data.answer||"");
    })();
    return()=>{live=false};
  },[siteId,site?.timezone,view]);

  const isChaiWalaRestaurant=restaurantConfig?.report_layout_profile==="chaiwala_restaurant_ops_v1";
  return{email,siteId,site,view,setView,answer,snapshot,restaurant,restaurantPeriod,restaurantConfig,isChaiWalaRestaurant,busy,error};
}
