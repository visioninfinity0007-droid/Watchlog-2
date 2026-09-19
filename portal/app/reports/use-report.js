"use client";
import {useEffect,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {selectedSiteId} from "../site-context";

const PROMPTS={
  daily:"Give me today's management report. Cover monitoring reliability, important activity, security attention and the priority action.",
  monthly:"Summarize the last 30 days for management: monitoring reliability, important patterns, incidents and the most important change.",
  executive:"Give me a concise executive summary: monitoring reliability, security attention, meaningful patterns and the highest-priority action."
};
const VALID_VIEWS=new Set(["daily","yesterday","monthly","executive"]);

function yesterdayInKarachi(){
  const d=new Date(Date.now()-86400000);
  const parts=new Intl.DateTimeFormat("en-US",{timeZone:"Asia/Karachi",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(d);
  const v=Object.fromEntries(parts.filter(x=>x.type!=="literal").map(x=>[x.type,x.value]));
  return `${v.year}-${v.month}-${v.day}`;
}

export default function useReport(){
  const[email,setEmail]=useState("");
  const[siteId,setSiteId]=useState("");
  const[view,setView]=useState("daily");
  const[answer,setAnswer]=useState("");
  const[snapshot,setSnapshot]=useState(null);
  const[busy,setBusy]=useState(false);
  const[error,setError]=useState("");

  useEffect(()=>{(async()=>{
    const g=await requireTenant();if(!g)return;
    setEmail(g.session.user.email||"");
    const params=new URLSearchParams(location.search),requested=params.get("view");
    setSiteId(params.get("site")||selectedSiteId());
    if(VALID_VIEWS.has(requested))setView(requested);
  })()},[]);

  useEffect(()=>{
    if(!siteId)return;
    let live=true;
    (async()=>{
      setBusy(true);setAnswer("");setSnapshot(null);setError("");
      if(view==="yesterday"){
        const r=await supabase().rpc("wl_my_report_snapshot",{p_site_id:siteId,p_date:yesterdayInKarachi()});
        if(!live)return;
        setBusy(false);
        if(r.error){setError(say(r.error));return}
        if(!r.data){setError("No saved report is available for yesterday yet.");return}
        setSnapshot(r.data);
        return;
      }
      const r=await supabase().functions.invoke("watchlog-ai",{body:{prompt:PROMPTS[view],site_id:siteId,conversation_id:null}});
      if(!live)return;
      setBusy(false);
      if(r.error||r.data?.error){setError(r.data?.message||say(r.error)||"WatchLog could not load this report.");return}
      setAnswer(r.data.answer||"");
    })();
    return()=>{live=false};
  },[siteId,view]);

  return{email,siteId,view,setView,answer,snapshot,busy,error};
}
