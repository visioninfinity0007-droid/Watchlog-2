"use client";

import { useEffect, useState } from "react";
import { supabase } from "../../lib/supabase";
import Mark from "../mark";

export default function AccountSuspended(){
  const[email,setEmail]=useState(""),[name,setName]=useState("your WatchLog account"),[checking,setChecking]=useState(true);
  useEffect(()=>{(async()=>{const sb=supabase();const{data:{session}}=await sb.auth.getSession();if(!session){location.replace("/login/");return;}setEmail(session.user.email||"");const{data,error}=await sb.rpc("wl_my_account");if(error){location.replace("/");return;}if(data?.account_status!=="suspended"){location.replace("/");return;}if(data?.name)setName(data.name);setChecking(false);})();},[]);
  async function signOut(){await supabase().auth.signOut();location.replace("/login/");}
  const brand=<header className="wl-auth-brand"><a className="wl-auth-logo" href="/"><Mark size={20}/><span>WatchLog</span></a><div className="wl-auth-pitch"><p>Know what happened at your business without watching hours of CCTV.</p></div></header>;
  if(checking)return <div className="wl-auth">{brand}<main className="wl-auth-main"><p className="wl-auth-status" role="status">Checking your account...</p></main></div>;
  return <div className="wl-auth">{brand}<main className="wl-auth-main"><div className="wl-auth-panel" style={{maxWidth:520}}>
    <div className="wl-auth-head"><div className="wl-auth-kicker">Account access</div><h1>Your WatchLog account is temporarily paused</h1><p className="wl-auth-lede">Access for <strong>{name}</strong> is currently paused. Your sites, history and account information have not been deleted.</p></div>
    <section className="wl-auth-sec" style={{borderTop:0,paddingTop:0}}><h2>Need help?</h2><p>Contact your WatchLog account manager or use your usual WatchLog support channel. Our team can review the account status and restore access when appropriate.</p></section>
    <div className="wl-auth-foot">{email&&<p>Signed in as {email}</p>}<button className="wl-auth-btn quiet small" type="button" onClick={signOut}>Sign out</button></div>
  </div></main></div>;
}
