"use client";

import { useEffect, useMemo, useState } from "react";
import Mark from "./mark";
import { supabase, say } from "../lib/supabase";
import { rememberSite, selectedSiteId, selectedSiteName, withSite } from "./site-context";

const MAIN_TABS=[["Reports","/reports/"],["Setup","/setup/"],["Settings","/settings/"]];
const TOOL_TABS=[
  ["Site Health","/site-health/"],
  ["Incidents","/incidents/"],
  ["Camera View","/control-room/"],
  ["Analytics","/analytics/"],
  ["Evidence","/archive/"],
  ["Advanced Site Control","/site-control/"],
];
const TOOL_ACTIVE=new Set(["Overview","Control Room","Incidents","Operations","Site Health","Site Control","Analytics","Executive","Archive","Team"]);

export function Nav({active,email,right,currentSiteId=""}){
  const[platform,setPlatform]=useState(null);
  const[sites,setSites]=useState([]);
  const[conversations,setConversations]=useState([]);
  const[siteId,setSiteId]=useState(currentSiteId||"");
  const[toolsOpen,setToolsOpen]=useState(TOOL_ACTIVE.has(active));
  const[mobileOpen,setMobileOpen]=useState(false);
  const[askOpen,setAskOpen]=useState(false);
  const[askDraft,setAskDraft]=useState("");
  const[askBusy,setAskBusy]=useState(false);
  const[askAnswer,setAskAnswer]=useState("");
  const[askError,setAskError]=useState("");

  useEffect(()=>{let live=true;(async()=>{
    const sb=supabase();
    const[p,s,c]=await Promise.all([sb.rpc("wl_platform_me"),sb.rpc("wl_sites"),sb.rpc("wl_ai_conversations",{p_limit:18})]);
    if(!live)return;
    if(p.data?.role)setPlatform(p.data);
    const nextSites=s.data||[];setSites(nextSites);setConversations(c.data||[]);
    const preferred=currentSiteId||selectedSiteId()||nextSites[0]?.id||"";
    if(preferred){setSiteId(preferred);const match=nextSites.find((x)=>x.id===preferred);rememberSite(preferred,match?.name||selectedSiteName());}
  })();return()=>{live=false;};},[currentSiteId]);

  useEffect(()=>{if(currentSiteId&&currentSiteId!==siteId){setSiteId(currentSiteId);const match=sites.find((x)=>x.id===currentSiteId);rememberSite(currentSiteId,match?.name||"");}},[currentSiteId,siteId,sites]);

  const currentSite=useMemo(()=>sites.find((x)=>x.id===siteId)||null,[sites,siteId]);
  const currentName=currentSite?.name||selectedSiteName()||"Selected site";

  function chooseSite(id,name){setSiteId(id);rememberSite(id,name);setMobileOpen(false);}
  async function signOut(){await supabase().auth.signOut();location.replace("/login/");}
  async function askWatchLog(e){e?.preventDefault();const prompt=askDraft.trim();if(!prompt||!siteId||askBusy)return;setAskBusy(true);setAskError("");setAskAnswer("");const{data,error}=await supabase().functions.invoke("watchlog-ai",{body:{prompt,site_id:siteId,conversation_id:null}});setAskBusy(false);if(error||data?.error){setAskError(data?.message||say(error)||"WatchLog could not answer that request.");return;}setAskAnswer(data?.answer||"WatchLog completed the request.");}

  return <>
    <aside className={`productRail ${mobileOpen?"open":""}`}>
      <div className="productRailMobileHead"><a href={withSite("/ai/",siteId)} className="productRailBrand"><Mark size={28}/><b>WatchLog</b></a><button className="productRailMenu" onClick={()=>setMobileOpen((v)=>!v)} aria-label="Open WatchLog navigation" aria-expanded={mobileOpen}>☰</button></div>
      <a className="productRailNewChat" href={withSite("/ai/",siteId)}>＋ <span>New chat</span></a>

      <div className="productRailSectionLabel">Sites</div>
      <div className="productRailSites">{sites.length?sites.map((s)=><a key={s.id} href={`/ai/?site=${encodeURIComponent(s.id)}`} onClick={()=>chooseSite(s.id,s.name)} className={`productRailSite ${s.id===siteId?"active":""}`}><span>{s.name}</span><small>{s.online?"Monitoring":s.setup_state==="ready"?"Needs attention":"Setup required"}</small></a>):<a className="productRailSite" href="/setup/"><span>Add your first site</span><small>Start setup</small></a>}</div>

      <div className="productRailSectionLabel">Recent</div>
      <div className="productRailRecent">{conversations.slice(0,7).map((c)=><a key={c.id} href={`/ai/?site=${encodeURIComponent(c.site_id||siteId)}&conversation=${encodeURIComponent(c.id)}`} onClick={()=>c.site_id&&chooseSite(c.site_id,c.site_name||"")} className="productRailConversation"><span>{c.title||"WatchLog conversation"}</span><small>{c.site_name||"Site conversation"}</small></a>)}{!conversations.length&&<div className="productRailEmpty">Your WatchLog conversations will appear here.</div>}</div>

      <nav className="productRailNav" aria-label="WatchLog navigation">
        {MAIN_TABS.map(([label,href])=><a key={href} href={withSite(href,siteId)} className={"productRailLink"+(active===label?" active":"")}>{label}</a>)}
        <button type="button" className={"productRailLink productRailTools"+(TOOL_ACTIVE.has(active)?" active":"")} onClick={()=>setToolsOpen((v)=>!v)} aria-expanded={toolsOpen}>Site views <span>{toolsOpen?"−":"+"}</span></button>
        {toolsOpen&&<div className="productRailSubnav">{TOOL_TABS.map(([label,href])=><a key={href} href={withSite(href,siteId)} className={"productRailSubLink"+(active===label||((active==="Control Room")&&label==="Camera View")||((active==="Archive")&&label==="Evidence")||((active==="Site Control")&&label==="Advanced Site Control")?" active":"")}>{label}</a>)}</div>}
      </nav>

      <div className="productRailBottom">
        <button className="productRailAsk" onClick={()=>{setAskOpen(true);setMobileOpen(false);}}>✦ Ask WatchLog</button>
        {right&&<div className="productRailUtility">{right}</div>}
        {platform&&<a href="/admin/" className="productRailAdmin">WatchLog Admin</a>}
        {email&&<div className="productRailUser" title={email}>{email}</div>}
        <button className="productRailSignout" onClick={signOut}>Sign out</button>
      </div>
    </aside>

    {askOpen&&<><button className="watchlogAskScrim" onClick={()=>setAskOpen(false)} aria-label="Close WatchLog assistant"/><aside className="watchlogAskDrawer" role="dialog" aria-modal="true" aria-label="Ask WatchLog"><div className="watchlogAskHead"><div><span>Ask WatchLog</span><b>{siteId?currentName:"Choose a site first"}</b></div><button onClick={()=>setAskOpen(false)} aria-label="Close">×</button></div><div className="watchlogAskBody">{askAnswer?<div className="watchlogAskAnswer"><Mark size={24}/><p>{askAnswer}</p></div>:<div className="watchlogAskIntro"><Mark size={34}/><h3>Ask about what you're viewing.</h3><p>WatchLog uses the selected site's verified recorder, camera, incident and coverage data.</p></div>}{askError&&<div className="err">{askError}</div>}</div><form className="watchlogAskComposer" onSubmit={askWatchLog}><textarea rows={2} value={askDraft} onChange={(e)=>setAskDraft(e.target.value)} placeholder={siteId?`Ask about ${currentName}...`:"Select a site first"} disabled={!siteId||askBusy}/><button disabled={!siteId||!askDraft.trim()||askBusy}>{askBusy?"…":"↑"}</button></form></aside></>}
  </>;
}

export const SETUP_STEPS=[["awaiting_agent","Waiting for setup"],["enrolled","WatchLog connected"],["recorder_connected","Camera system connected"],["cameras_discovered","Cameras ready"],["ready","Ready"]];
export function setupPill(state,online){const idx=SETUP_STEPS.findIndex(([k])=>k===state),label=idx>=0?SETUP_STEPS[idx][1]:"Setup status unavailable";if(state==="ready")return[label,online?"s-ok":"s-warn"];if(state==="awaiting_agent")return[label,"s-unk"];return[label,"s-warn"];}

async function accountState(sb){const{data,error}=await sb.rpc("wl_my_account");if(!error)return data;if(/wl_my_account|schema cache|function/i.test(error.message||""))return undefined;throw error;}

export async function requireTenant(){const sb=supabase();const{data:{session}}=await sb.auth.getSession();if(!session){location.replace("/login/");return null;}try{const account=await accountState(sb);if(account?.account_status==="suspended"){location.replace("/account-suspended/");return null;}if(account===null){location.replace("/onboarding/");return null;}}catch{location.replace("/login/");return null;}const{data:tenant,error}=await sb.rpc("wl_my_tenant");if(error||!tenant){location.replace("/onboarding/");return null;}return{session,tenant};}
