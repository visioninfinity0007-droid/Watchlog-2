"use client";
import {useEffect,useState} from "react";import {supabase,say} from "../../lib/supabase";import {requireTenant} from "../shell";import {rememberSite,selectedSiteId} from "../site-context";
// Site Control data plane: capability diagnosis + business context + onboarding truth, all through the
// tenant-guarded RPCs. The diagnosis carries the Read -> Recommend -> Approve tiers, role-gated on the
// server (0079: read=all, recommend=owner/admin/manager, approve=owner/admin). No raw device command or
// credential ever crosses this boundary.
export default function useCustomerControl(){
  const[email,setEmail]=useState(""),[sites,setSites]=useState([]),[siteId,setSiteId]=useState(""),[data,setData]=useState(null),[context,setContext]=useState(null),[onboarding,setOnboarding]=useState(null),[error,setError]=useState(""),[note,setNote]=useState("");
  useEffect(()=>{(async()=>{const g=await requireTenant();if(!g)return;setEmail(g.session.user.email||"");const r=await supabase().rpc("wl_sites");if(r.error){setError(say(r.error));return}const list=r.data||[],id=new URLSearchParams(location.search).get("site")||selectedSiteId()||list[0]?.id||"";setSites(list);setSiteId(id);if(id)rememberSite(id,list.find(x=>x.id===id)?.name||"")})()},[]);
  useEffect(()=>{if(!siteId)return;(async()=>{const sb=supabase();const[d,c,o]=await Promise.all([sb.rpc("wl_my_site_diagnosis",{p_site_id:siteId}),sb.rpc("wl_my_site_context",{p_site_id:siteId}),sb.rpc("wl_onboarding_status",{p_site_id:siteId})]);if(d.error){setError(say(d.error));return}setData(d.data||null);setContext(c.error?null:(c.data||null));setOnboarding(o.error?null:(o.data||null));setError("")})()},[siteId]);
  async function saveSiteType(t){if(!siteId)return;const r=await supabase().rpc("wl_upsert_site_context",{p_site_id:siteId,p_site_type:t});if(r.error){setError(say(r.error));return}const c=await supabase().rpc("wl_my_site_context",{p_site_id:siteId});if(!c.error)setContext(c.data||null);setNote("Business context saved.");setTimeout(()=>setNote(""),2500)}
  return{email,siteId,site:sites.find(s=>s.id===siteId),sites,data,context,onboarding,error,note,saveSiteType};
}
