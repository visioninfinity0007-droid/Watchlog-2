"use client";

import { useEffect, useState } from "react";
import Mark from "./mark";
import { supabase } from "../lib/supabase";

// WatchLog is AI-first. Operational surfaces stay available as focused tools,
// but every signed-in page uses the same quiet product shell as WatchLog AI.
const TABS = [
  ["WatchLog AI", "/ai/"],
  ["Reports", "/reports/"],
  ["Setup", "/setup/"],
  ["Settings", "/settings/"],
];
const TOOL_TABS = [
  ["Site Health", "/site-health/"],
  ["Incidents", "/incidents/"],
  ["Camera View", "/control-room/"],
  ["Analytics", "/analytics/"],
  ["Evidence Archive", "/archive/"],
  ["Advanced Site Control", "/site-control/"],
];
const TOOL_ACTIVE = new Set(["Overview","Control Room","Incidents","Operations","Site Health","Site Control","Analytics","Executive","Archive","Team"]);

export function Nav({ active, email, right }) {
  const [platform,setPlatform]=useState(null);
  const [toolsOpen,setToolsOpen]=useState(TOOL_ACTIVE.has(active));
  useEffect(()=>{let live=true;(async()=>{const {data}=await supabase().rpc("wl_platform_me");if(live&&data?.role)setPlatform(data);})();return()=>{live=false;};},[]);
  async function signOut(){await supabase().auth.signOut();location.replace("/login/");}
  return <aside className="productRail">
    <a href="/ai/" className="productRailBrand"><Mark size={28}/><b>WatchLog</b></a>
    <nav className="productRailNav" aria-label="WatchLog navigation">
      {TABS.map(([label,href])=><a key={href} href={href} className={"productRailLink"+(active===label?" active":"")}>{label}</a>)}
      <button type="button" className={"productRailLink productRailTools"+(TOOL_ACTIVE.has(active)?" active":"")} onClick={()=>setToolsOpen((v)=>!v)} aria-expanded={toolsOpen}>Tools <span>{toolsOpen?"−":"+"}</span></button>
      {toolsOpen&&<div className="productRailSubnav">{TOOL_TABS.map(([label,href])=><a key={href} href={href} className={"productRailSubLink"+(active===label||((active==="Control Room")&&label==="Camera View")||((active==="Archive")&&label==="Evidence Archive")||((active==="Site Control")&&label==="Advanced Site Control")?" active":"")}>{label}</a>)}</div>}
    </nav>
    <div className="productRailBottom">
      {right&&<div className="productRailUtility">{right}</div>}
      {platform&&<a href="/admin/" className="productRailAdmin">WatchLog Admin</a>}
      {email&&<div className="productRailUser" title={email}>{email}</div>}
      <button className="productRailSignout" onClick={signOut}>Sign out</button>
    </div>
  </aside>;
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
