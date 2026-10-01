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
  const[role,setRole]=useState("viewer");
  const[privacy,setPrivacy]=useState({loaded:false,text:false,evidence:false});
  const[privacyBusy,setPrivacyBusy]=useState("");
  const[privacyNote,setPrivacyNote]=useState("");
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
    const roleResult=await supabase().rpc("wl_my_role");
    if(!roleResult.error)setRole(roleResult.data||"viewer");
    await loadSites();
  })()},[loadSites]);

  useEffect(()=>{let live=true;(async()=>{
    if(!siteId){setPrivacy({loaded:false,text:false,evidence:false});return}
    setPrivacy({loaded:false,text:false,evidence:false});setPrivacyNote("");
    const r=await supabase().rpc("wl_ai_site_egress",{p_site_id:siteId});
    if(!live)return;
    if(r.error){setError(say(r.error));return}
    setPrivacy({loaded:true,text:!!r.data?.external_text_egress_allowed,evidence:!!r.data?.external_egress_allowed});
  })();return()=>{live=false}},[siteId]);

  function choose(id){
    setSiteId(id);
    rememberSite(id,sites.find(s=>s.id===id)?.name||"");
    history.replaceState(null,"",`/settings/?site=${encodeURIComponent(id)}`);
  }

  async function setPrivacyPermission(kind,allowed){
    if(!siteId||privacyBusy||(role!=="owner"&&role!=="admin"))return false;
    if(kind==="text"&&privacy.evidence&&!allowed){
      setPrivacyNote("Camera-evidence permission also includes written site information. Turn off camera-evidence processing first.");
      return false;
    }
    setPrivacyBusy(kind);setError("");setPrivacyNote("");
    const rpc=kind==="evidence"?"wl_ai_set_site_egress":"wl_ai_set_site_text_egress";
    const r=await supabase().rpc(rpc,{p_site_id:siteId,p_allowed:!!allowed});
    setPrivacyBusy("");
    if(r.error){setError(say(r.error));return false}
    const refreshed=await supabase().rpc("wl_ai_site_egress",{p_site_id:siteId});
    if(refreshed.error){setError(say(refreshed.error));return false}
    setPrivacy({loaded:true,text:!!refreshed.data?.external_text_egress_allowed,evidence:!!refreshed.data?.external_egress_allowed});
    setPrivacyNote(allowed?"Permission updated for this site.":"Permission turned off for this site.");
    return true;
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
    error,setError,role,canManage:role==="owner"||role==="admin",
    privacy,privacyBusy,privacyNote,setPrivacyPermission,
    choose,removeSite,removing,reload:loadSites
  };
}
