"use client";

import { useEffect, useState } from "react";
import Mark from "./mark";
import { supabase } from "../lib/supabase";

// WatchLog is AI-first. Operational surfaces remain available as deep links/tools,
// but customers should not have to learn a 12-module dashboard taxonomy.
const TABS = [
  ["WatchLog AI", "/ai/"],
  ["Reports", "/reports/"],
  ["Setup", "/setup/"],
  ["Settings", "/settings/"],
];

export function Nav({ active, email, right }) {
  const [platform,setPlatform]=useState(null);
  const [toolsOpen,setToolsOpen]=useState(false);
  useEffect(()=>{let live=true;(async()=>{const {data}=await supabase().rpc("wl_platform_me");if(live&&data?.role)setPlatform(data);})();return()=>{live=false;};},[]);
  async function signOut() { await supabase().auth.signOut(); location.replace("/login/"); }
  return <header className="topbar"><a href="/ai/" className="brandlink"><Mark size={26}/><b>WatchLog</b></a><nav className="nav">{TABS.map(([label,href])=><a key={href} href={href} className={"navlink"+(active===label?" active":"")}>{label}</a>)}<span style={{position:"relative"}}><button type="button" className={"navlink"+(["Overview","Control Room","Incidents","Operations","Site Health","Site Control","Analytics","Executive","Archive","Team"].includes(active)?" active":"")} style={{background:"transparent",border:0,width:"auto",padding:"8px 10px"}} onClick={()=>setToolsOpen((v)=>!v)}>Tools ▾</button>{toolsOpen&&<span style={{position:"absolute",top:"calc(100% + 8px)",left:0,minWidth:190,padding:7,background:"var(--color-surface, #111827)",border:"1px solid var(--wl-target-line, #26354f)",borderRadius:10,zIndex:100,boxShadow:"0 14px 36px rgba(0,0,0,.22)",display:"grid",gap:2}}><a className="navlink" href="/site-health/">Site Health</a><a className="navlink" href="/incidents/">Incidents</a><a className="navlink" href="/control-room/">Camera View</a><a className="navlink" href="/analytics/">Analytics</a><a className="navlink" href="/archive/">Evidence Archive</a><a className="navlink" href="/site-control/">Advanced Site Control</a></span>}</span></nav><span className="spacer"/>{platform&&<a href="/admin/" className="navlink hide-sm" style={{color:"var(--wl-ice)"}}>WatchLog Admin</a>}{email&&<span className="muted hide-sm" style={{fontSize:"var(--font-size-xs)"}}>{email}</span>}{right}<button className="ghost small" onClick={signOut}>Sign out</button></header>;
}

export const SETUP_STEPS=[["awaiting_agent","Waiting for setup"],["enrolled","WatchLog connected"],["recorder_connected","Camera system connected"],["cameras_discovered","Cameras ready"],["ready","Ready"]];
export function setupPill(state,online){const idx=SETUP_STEPS.findIndex(([k])=>k===state),label=idx>=0?SETUP_STEPS[idx][1]:"Setup status unavailable";if(state==="ready")return[label,online?"s-ok":"s-warn"];if(state==="awaiting_agent")return[label,"s-unk"];return[label,"s-warn"];}

async function accountState(sb){
  const {data,error}=await sb.rpc("wl_my_account");
  if(!error)return data;
  if(/wl_my_account|schema cache|function/i.test(error.message||""))return undefined;
  throw error;
}

export async function requireTenant(){
  const sb=supabase();
  const{data:{session}}=await sb.auth.getSession();
  if(!session){location.replace("/login/");return null;}
  try{
    const account=await accountState(sb);
    if(account?.account_status==="suspended"){location.replace("/account-suspended/");return null;}
    if(account===null){location.replace("/onboarding/");return null;}
  }catch{location.replace("/login/");return null;}
  const{data:tenant,error}=await sb.rpc("wl_my_tenant");
  if(error||!tenant){location.replace("/onboarding/");return null;}
  return{session,tenant};
}
