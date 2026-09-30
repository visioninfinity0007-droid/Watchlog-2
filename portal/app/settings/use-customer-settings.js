"use client";
import {useCallback,useEffect,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {clearRememberedSite,rememberSite,selectedSiteId} from "../site-context";

export default function useCustomerSettings(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[error,setError]=useState("");
  const[removing,setRemoving]=useState(false);

  const loadSites=useCallback(async(preferred="")=>{
    const r=await supabase().rpc("wl_sites");
    if(r.error){setError(say(r.error));return []}
    const list=r.data||[];
    setSites(list);
    const requested=preferred||new URLSearchParams(location.search).get("site")||selectedSiteId()||"";
    const valid=list.some(s=>String(s.id)===String(requested));
    const next=valid?requested:(list[0]?.id||"");
    setSiteId(next);
    if(next){
      const row=list.find(s=>String(s.id)===String(next));
      rememberSite(next,row?.name||"");
      history.replaceState(null,"",`/settings/?site=${encodeURIComponent(next)}`);
    }else{
      clearRememberedSite();
      history.replaceState(null,"","/settings/");
    }
    return list;
  },[]);

  useEffect(()=>{(async()=>{
    const g=await requireTenant();
    if(!g)return;
    setEmail(g.session.user.email||"");
    await loadSites();
  })()},[loadSites]);

  function choose(id){
    setSiteId(id);
    rememberSite(id,sites.find(s=>s.id===id)?.name||"");
    history.replaceState(null,"",`/settings/?site=${encodeURIComponent(id)}`);
  }

  async function removeSite(site){
    if(!site?.id||removing)return null;
    setRemoving(true);setError("");
    const r=await supabase().rpc("wl_remove_site",{
      p_site_id:site.id,
      p_confirm_name:site.name
    });
    setRemoving(false);
    if(r.error){setError(say(r.error));return null}
    clearRememberedSite();
    await loadSites("");
    return r.data||{ok:true};
  }

  return{
    email,sites,siteId,current:sites.find(s=>s.id===siteId),
    error,setError,choose,removeSite,removing,reload:loadSites
  };
}
