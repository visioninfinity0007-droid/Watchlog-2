"use client";
import {useEffect,useMemo,useState} from "react";
import Mark from "./mark";
import {PortalIcon} from "./icons";
import {supabase} from "../lib/supabase";
import {rememberSite,selectedSiteId,selectedSiteName,withSite} from "./site-context";
import {MAIN_TABS,MORE_TABS,MORE_ACTIVE,ACTIVE_ROUTE} from "./nav-config";

function allowedSite(list,id){return Boolean(id)&&list.some(s=>String(s.id)===String(id))}
function siteState(s){
  if(s.online)return["Monitoring","ok"];
  if(s.setup_state==="ready")return["Needs attention","warn"];
  return["Setup required",""];
}

// Signal Ledger owner navigation: quiet ink foundation, site-aware, owner-first jobs, then More.
export function Nav({active,email,right,currentSiteId=""}){
  const[platform,setPlatform]=useState(null);
  const[sites,setSites]=useState([]);
  const[conversations,setConversations]=useState([]);
  const[navReady,setNavReady]=useState(false);
  const[siteId,setSiteId]=useState(currentSiteId||"");
  const[moreOpen,setMoreOpen]=useState(MORE_ACTIVE.has(active));
  const[mobileOpen,setMobileOpen]=useState(false);
  const[convId,setConvId]=useState("");
  useEffect(()=>{try{setConvId(new URLSearchParams(window.location.search).get("conversation")||"")}catch{}},[]);

  useEffect(()=>{let live=true;(async()=>{
    const sb=supabase();
    try{
      const[p,s,c]=await Promise.all([
        sb.rpc("wl_platform_me"),
        sb.rpc("wl_sites"),
        sb.rpc("wl_ai_conversations",{p_limit:12})
      ]);
      if(!live)return;
      if(p.data?.role)setPlatform(p.data);
      const next=s.data||[];
      setSites(next);
      setConversations(c.data||[]);
      const requested=currentSiteId||selectedSiteId()||"";
      const preferred=allowedSite(next,requested)?requested:(next[0]?.id||"");
      setSiteId(preferred);
      if(preferred)rememberSite(preferred,next.find(x=>x.id===preferred)?.name||selectedSiteName());
    }finally{
      if(live)setNavReady(true);
    }
  })();return()=>{live=false}},[]);

  useEffect(()=>{
    if(!sites.length)return;
    if(currentSiteId&&allowedSite(sites,currentSiteId)){
      if(currentSiteId!==siteId){setSiteId(currentSiteId);rememberSite(currentSiteId,sites.find(x=>x.id===currentSiteId)?.name||"")}
      return;
    }
    if(!allowedSite(sites,siteId)){
      const fallback=sites[0]?.id||"";
      setSiteId(fallback);
      if(fallback)rememberSite(fallback,sites[0]?.name||"");
    }
  },[currentSiteId,siteId,sites]);

  useEffect(()=>{
    if(!mobileOpen)return;
    const close=e=>{if(e.key==="Escape")setMobileOpen(false)};
    window.addEventListener("keydown",close);
    return()=>window.removeEventListener("keydown",close);
  },[mobileOpen]);

  const currentSite=useMemo(()=>sites.find(x=>x.id===siteId)||null,[sites,siteId]);
  const route=ACTIVE_ROUTE[active]||"/home/";
  function choose(id,name){setSiteId(id);rememberSite(id,name);setMobileOpen(false)}
  async function signOut(){await supabase().auth.signOut();location.replace("/login/")}

  return <>
    <div className="ow-topbar">
      <a href={withSite("/home/",siteId)} aria-label="WatchLog Home"><Mark size={20}/><b>WatchLog</b></a>
      <span>{currentSite?.name||""}</span>
    </div>
    {mobileOpen&&<button type="button" className="ow-scrim" onClick={()=>setMobileOpen(false)} aria-label="Close navigation"/>}
    <aside className={"ow-nav"+(mobileOpen?" open":"")} aria-busy={!navReady} aria-label="WatchLog">
      <div className="ow-nav-top">
        <a href={withSite("/home/",siteId)} className="ow-nav-brand"><Mark size={20}/><span>WatchLog</span></a>
        <button type="button" className="ow-nav-close" onClick={()=>setMobileOpen(false)} aria-label="Close WatchLog navigation">×</button>
      </div>

      <div className="ow-nav-label">{sites.length>1?"Sites":"Site"}</div>
      <div className="ow-nav-sites">
        {!navReady?<div className="ow-nav-skel" aria-hidden="true"/>:sites.length?sites.map(s=>{
          const[label,state]=siteState(s);
          return <a key={s.id} href={withSite(route,s.id)} onClick={()=>choose(s.id,s.name)} aria-current={s.id===siteId?"true":undefined} className={"ow-nav-site"+(s.id===siteId?" active":"")}>
            <i className={"ow-dot "+state} aria-hidden="true"/>
            <span><b>{s.name}</b><small>{label}</small></span>
          </a>;
        }):<a className="ow-nav-site" href="/settings/"><i className="ow-dot" aria-hidden="true"/><span><b>Add your first site</b><small>Get started</small></span></a>}
      </div>

      <nav className="ow-nav-links" aria-label="WatchLog navigation">
        {MAIN_TABS.map(([label,href,match])=><a key={href} href={withSite(href,siteId)} aria-current={active===match?"page":undefined} className={"ow-nav-link"+(active===match?" active":"")}><PortalIcon name={label}/><span>{label}</span></a>)}
        <button type="button" className="ow-nav-more" onClick={()=>setMoreOpen(v=>!v)} aria-expanded={moreOpen} aria-controls="ow-nav-more-list"><PortalIcon name="More"/><span>More</span><b aria-hidden="true">{moreOpen?"−":"+"}</b></button>
        {moreOpen&&<div className="ow-nav-sub" id="ow-nav-more-list">{MORE_TABS.map(([label,href,match])=><a key={href} href={withSite(href,siteId)} aria-current={active===match?"page":undefined} className={"ow-nav-link"+(active===match?" active":"")}><PortalIcon name={label}/><span>{label}</span></a>)}</div>}
      </nav>

      {active==="WatchLog AI"&&<>
        <div className="ow-nav-label">Recent conversations</div>
        <div className="ow-nav-recent">
          {!navReady?<div className="ow-nav-skel" aria-hidden="true"/>:conversations.slice(0,8).map(c=>{
            const title=c.title||"WatchLog conversation";
            const siteName=c.site_name||"Site conversation";
            const conversationSite=allowedSite(sites,c.site_id)?c.site_id:siteId;
            const isCurrent=Boolean(convId)&&String(c.id)===String(convId);
            return <a key={c.id} title={`${title} · ${siteName}`} aria-current={isCurrent?"page":undefined} href={`/ai/?site=${encodeURIComponent(conversationSite||"")}&conversation=${encodeURIComponent(c.id)}`} onClick={()=>conversationSite&&choose(conversationSite,c.site_name||"")} className={"ow-nav-convo"+(isCurrent?" active":"")}><span>{title}</span><small>{siteName}</small></a>;
          })}
          {navReady&&!conversations.length&&<div className="ow-nav-empty">Your recent conversations will appear here.</div>}
        </div>
      </>}

      <div className="ow-nav-foot">
        {right&&<div>{right}</div>}
        {email&&<div className="ow-nav-user" title={email}>{email}</div>}
        {platform&&<a href="/admin/">WatchLog Admin</a>}
        <button type="button" onClick={signOut}>Sign out</button>
      </div>
    </aside>
    <nav className="ow-bottom" aria-label="WatchLog mobile navigation">
      <a href={withSite("/home/",siteId)} aria-current={active==="Home"?"page":undefined} className={active==="Home"?"active":""}><PortalIcon name="Home"/><span>Home</span></a>
      <a href={withSite("/notifications/",siteId)} aria-current={active==="Notifications"?"page":undefined} className={active==="Notifications"?"active":""}><PortalIcon name="Attention"/><span>Attention</span></a>
      <a href={withSite("/ai/",siteId)} aria-current={active==="WatchLog AI"?"page":undefined} className={active==="WatchLog AI"?"active":""}><PortalIcon name="Ask"/><span>Ask</span></a>
      <a href={withSite("/reports/?view=yesterday",siteId)} aria-current={active==="Reports"?"page":undefined} className={active==="Reports"?"active":""}><PortalIcon name="Reports"/><span>Reports</span></a>
      <button type="button" aria-haspopup="true" aria-expanded={mobileOpen} className={MORE_ACTIVE.has(active)||["Analytics"].includes(active)?"active":""} onClick={()=>setMobileOpen(true)}><PortalIcon name="More"/><span>More</span></button>
    </nav>
  </>;
}

export const SETUP_STEPS=[["awaiting_agent","Waiting for setup"],["enrolled","WatchLog connected"],["recorder_connected","Camera system connected"],["cameras_discovered","Cameras ready"],["ready","Ready"]];
export function setupPill(state,online){const i=SETUP_STEPS.findIndex(([k])=>k===state),label=i>=0?SETUP_STEPS[i][1]:"Setup status unavailable";if(state==="ready")return[label,online?"s-ok":"s-warn"];if(state==="awaiting_agent")return[label,"s-unk"];return[label,"s-warn"]}
async function accountState(sb){const{data,error}=await sb.rpc("wl_my_account");if(!error)return data;if(/wl_my_account|schema cache|function/i.test(error.message||""))return undefined;throw error}
export async function requireTenant(){const sb=supabase();const{data:{session}}=await sb.auth.getSession();if(!session){location.replace("/login/");return null}try{const account=await accountState(sb);if(account?.account_status==="suspended"){location.replace("/account-suspended/");return null}if(account===null){location.replace("/onboarding/");return null}}catch{location.replace("/login/");return null}const{data:tenant,error}=await sb.rpc("wl_my_tenant");if(error||!tenant){location.replace("/onboarding/");return null}return{session,tenant}}
