"use client";
import {useEffect,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId} from "../site-context";

const PROMPTS={
  daily:"Give me today's management report. Cover monitoring reliability, important activity, security attention and the priority action.",
  monthly:"Summarize the last 30 days for management: monitoring reliability, important patterns, incidents and the most important change.",
  executive:"Give me a concise executive summary: monitoring reliability, security attention, meaningful patterns and the highest-priority action."
};
const VALID_VIEWS=new Set(["daily","yesterday","monthly","executive"]);

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
      setBusy(true);setAnswer("");setSnapshot(null);setRestaurant(null);setError("");
      const sb=supabase();
      const cfg=await sb.rpc("wl_restaurant_site_config",{p_site_id:siteId});
      if(!live)return;
      const restaurantEnabled=!cfg.error&&cfg.data?.enabled===true;

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
        setBusy(false);
        if(report.error){setError(say(report.error));return}
        setSnapshot(report.data||null);
        setRestaurant(report.data ? (report.data?.payload?.restaurant||null) : rest);
        return;
      }

      if(view==="daily"&&restaurantEnabled){
        const rr=await sb.rpc("wl_restaurant_day",{p_site_id:siteId,p_date:null});
        if(!live)return;
        if(!rr.error)setRestaurant(rr.data||null);
      }

      const r=await sb.functions.invoke("watchlog-ai",{body:{prompt:PROMPTS[view],site_id:siteId,conversation_id:null}});
      if(!live)return;
      setBusy(false);
      if(r.error||r.data?.error){setError(r.data?.message||say(r.error)||"WatchLog could not load this report.");return}
      setAnswer(r.data.answer||"");
    })();
    return()=>{live=false};
  },[siteId,site?.timezone,view]);

  return{email,siteId,site,view,setView,answer,snapshot,restaurant,busy,error};
}
