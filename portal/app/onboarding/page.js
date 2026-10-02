"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";
import { SETUP_STEPS } from "../shell";

async function accountState(sb){const{data,error}=await sb.rpc("wl_my_account");if(!error)return data;if(/wl_my_account|schema cache|function/i.test(error.message||""))return undefined;throw error;}

export default function Onboarding(){
  const[stage,setStage]=useState("loading"),[company,setCompany]=useState(""),[site,setSite]=useState("Main site"),[busy,setBusy]=useState(false),[error,setError]=useState(""),[code,setCode]=useState(""),[setupState,setSetupState]=useState("awaiting_agent");
  useEffect(()=>{if(stage!=="connect")return;let live=true;const tick=async()=>{const{data}=await supabase().rpc("wl_sites");if(live&&data?.[0])setSetupState(data[0].setup_state);};tick();const t=setInterval(tick,8000);return()=>{live=false;clearInterval(t);};},[stage]);
  useEffect(()=>{(async()=>{const sb=supabase();const{data:{session}}=await sb.auth.getSession();if(!session){location.replace("/login/");return;}try{const account=await accountState(sb);if(account?.account_status==="suspended"){location.replace("/account-suspended/");return;}if(account?.tenant_id){location.replace("/home/");return;}}catch{setError("WatchLog could not verify your account. Please try again.");setStage("error");return;}const{data:tenant}=await sb.rpc("wl_my_tenant");if(tenant){location.replace("/home/");return;}setCompany(session.user?.user_metadata?.company||"");setStage("form");})();},[]);
  async function createTenant(e){e.preventDefault();setBusy(true);setError("");const{data,error}=await supabase().rpc("wl_bootstrap_tenant",{p_company:company,p_site_name:site,p_timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||"Asia/Karachi"});if(error){setError(say(error));setBusy(false);return;}setCode(data.enrollment_code||"");setStage("connect");setBusy(false);}
  const brand=<header className="wl-auth-brand"><a className="wl-auth-logo" href="/"><Mark size={20}/><span>WatchLog</span></a><div className="wl-auth-pitch"><p>Know what happened at your business without watching hours of CCTV.</p><ul><li>What happened</li><li>What needs attention</li><li>What WatchLog can verify</li></ul></div></header>;
  if(stage==="loading")return <div className="wl-auth">{brand}<main className="wl-auth-main"><p className="wl-auth-status" role="status">Loading...</p></main></div>;
  if(stage==="error")return <div className="wl-auth">{brand}<main className="wl-auth-main"><div className="wl-auth-panel"><div className="wl-auth-head"><h1>We could not open your account</h1></div><div className="wl-auth-note bad" role="alert">{error}</div><div className="wl-auth-actions"><a className="wl-auth-btn" href="/">Try again</a></div></div></main></div>;
  if(stage==="form")return <div className="wl-auth">{brand}<main className="wl-auth-main"><form className="wl-auth-panel" onSubmit={createTenant} aria-busy={busy}><div className="wl-auth-head"><div className="wl-auth-kicker">Welcome to WatchLog</div><h1>Set up your first site</h1><p className="wl-auth-lede">Tell us where your first cameras are located. You can add more sites whenever you need them.</p></div>{error&&<div className="wl-auth-note bad" role="alert">{error}</div>}<div className="wl-auth-fields"><label htmlFor="company">Company<input id="company" required value={company} onChange={(e)=>setCompany(e.target.value)}/></label><label htmlFor="site">Site name<input id="site" required value={site} onChange={(e)=>setSite(e.target.value)} placeholder="Head Office, Warehouse, Plant"/></label></div><div className="wl-auth-actions"><button className="wl-auth-btn" type="submit" disabled={busy}>{busy?"Creating...":"Continue"}</button></div></form></main></div>;

  const current=SETUP_STEPS.findIndex(([k])=>k===setupState);
  return <div className="wl-auth">{brand}<main className="wl-auth-main"><div className="wl-auth-panel wide">
    <div className="wl-auth-head"><div className="wl-auth-kicker">First site</div><h1>Connect your cameras</h1><p className="wl-auth-lede">Follow the guided Windows setup at your site. Most installations take about ten minutes.</p></div>
    {error&&<div className="wl-auth-note bad" role="alert">{error}</div>}

    <section className="wl-auth-sec" aria-label="Setup progress"><h2>Setup progress</h2><p>This page updates automatically while WatchLog connects the site and prepares the cameras.</p><div className="setup-progress" role="status" aria-live="polite">{SETUP_STEPS.slice(1).map(([key,label],i)=>{const absoluteIndex=i+1,ready=setupState==="ready",done=ready?absoluteIndex<=current:absoluteIndex<current,active=!done&&absoluteIndex===current,pending=absoluteIndex>current;return <div className={`setup-progress-row${done?" done":active?" active":pending?" pending":""}`} key={key}><span className="setup-progress-dot" aria-hidden="true">{done?"✓":absoluteIndex}</span><b>{label}</b><span className="setup-progress-state">{done?"Done":active?"In progress":"Pending"}</span></div>;})}</div></section>

    <section className="wl-auth-sec" aria-label="Before you start"><h2>Before you start</h2><ul className="wl-auth-list"><li>A Windows computer at the site, connected to the same network as your CCTV system</li><li>Your CCTV username and password for the guided connection step</li><li>A normal internet connection so WatchLog can update your portal and reports</li></ul></section>

    <section className="wl-auth-sec" aria-label="Install steps"><h2>Install at the site</h2><ol className="wl-auth-tasks">
      <li><span className="wl-auth-task-no" aria-hidden="true">1</span><div><b>Download WatchLog for Windows</b><small>The guided setup will help connect this location.</small></div><div>{process.env.NEXT_PUBLIC_INSTALLER_URL?<a className="wl-auth-btn small" href={process.env.NEXT_PUBLIC_INSTALLER_URL}>Download for Windows</a>:<small>Contact WatchLog Support for the Windows setup</small>}</div></li>
      <li><span className="wl-auth-task-no" aria-hidden="true">2</span><div><b>Run WatchLog and enter this setup code</b><small>The code can be used once and is valid for 14 days.</small></div><div><span className="wl-auth-code">{code||"-"}</span></div></li>
      <li><span className="wl-auth-task-no" aria-hidden="true">3</span><div><b>Watch your site come online</b><small>Once cameras are ready, Home, System Health and Insights will begin filling in.</small></div><div/></li>
    </ol></section>

    <div className="wl-auth-foot"><a href="/home/">Open WatchLog Home</a></div>
  </div></main></div>;
}
