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
const VALID_VIEWS=new Set(["daily","yesterday","monthly","executive"]);

function yesterdayInKarachi(){
  const d=new Date(Date.now()-86400000);
  const parts=new Intl.DateTimeFormat("en-US",{timeZone:"Asia/Karachi",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(d);
  const v=Object.fromEntries(parts.filter(x=>x.type!=="literal").map(x=>[x.type,x.value]));
  return `${v.year}-${v.month}-${v.day}`;
}
function allowedSite(list,id){return Boolean(id)&&list.some(s=>String(s.id)===String(id))}

export default function useReport(){
  const[email,setEmail]=useState("");
  const[siteId,setSiteId]=useState("");
  const[view,setView]=useState("yesterday");
  const[answer,setAnswer]=useState("");
  const[snapshot,setSnapshot]=useState(null);
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
    const requestedSite=params.get("site")||selectedSiteId()||"";
    const resolvedSite=allowedSite(sites,requestedSite)?requestedSite:(sites[0]?.id||"");
    if(!resolvedSite){setError("No site is available for this account yet.");setBusy(false);return}
    const resolved=sites.find(s=>s.id===resolvedSite);
    rememberSite(resolvedSite,resolved?.name||"");
    setSiteId(resolvedSite);
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
      setBusy(true);setAnswer("");setSnapshot(null);setError("");
      if(view==="yesterday"){
        const r=await supabase().rpc("wl_my_report_snapshot",{p_site_id:siteId,p_date:yesterdayInKarachi()});
        if(!live)return;
        setBusy(false);
        if(r.error){setError(say(r.error));return}
        if(!r.data){setError("Yesterday's report is not available yet.");return}
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
