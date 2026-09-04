"use client";

import { useEffect, useState } from "react";
import { supabase } from "../../lib/supabase";
import Mark from "../mark";

export default function AccountSuspended(){
  const[email,setEmail]=useState("");const[name,setName]=useState("your WatchLog account");
  useEffect(()=>{(async()=>{const sb=supabase();const{data:{session}}=await sb.auth.getSession();if(!session){location.replace("/login/");return;}setEmail(session.user.email||"");const{data,error}=await sb.rpc("wl_my_account");if(!error&&data?.account_status!=="suspended"){location.replace("/");return;}if(data?.name)setName(data.name);})();},[]);
  async function signOut(){await supabase().auth.signOut();location.replace("/login/");}
  return <div className="center"><div className="auth-card" style={{maxWidth:560}}><div className="brand"><Mark tone="white"/><span className="brand-name">WatchLog</span></div><div className="target-eyebrow">Account access</div><h1>Your WatchLog account is temporarily paused</h1><p className="sub">Access for <strong>{name}</strong> is currently paused. Your sites, history and account information have not been deleted.</p><div className="card" style={{margin:"18px 0"}}><h2 style={{fontSize:16,textTransform:"none",letterSpacing:"-.02em",color:"#fff"}}>Need help?</h2><p className="muted" style={{marginBottom:0}}>Please contact the WatchLog team for account assistance. We can review the account status and restore access when appropriate.</p></div>{email&&<p className="muted" style={{fontSize:12}}>Signed in as {email}</p>}<div style={{display:"flex",gap:10,flexWrap:"wrap"}}><a href="mailto:support@watchlog.ai" className="button">Contact WatchLog Support</a><button className="ghost" onClick={signOut}>Sign out</button></div></div></div>;
}
