"use client";

import { useEffect, useState } from "react";
import { supabase } from "../../lib/supabase";
import Mark from "../mark";

export default function AccountSuspended(){
  const[email,setEmail]=useState(""),[name,setName]=useState("your WatchLog account"),[checking,setChecking]=useState(true);
  useEffect(()=>{(async()=>{const sb=supabase();const{data:{session}}=await sb.auth.getSession();if(!session){location.replace("/login/");return;}setEmail(session.user.email||"");const{data,error}=await sb.rpc("wl_my_account");if(error){location.replace("/");return;}if(data?.account_status!=="suspended"){location.replace("/");return;}if(data?.name)setName(data.name);setChecking(false);})();},[]);
  async function signOut(){await supabase().auth.signOut();location.replace("/login/");}
  if(checking)return <div className="center"><p className="muted">Checking your account...</p></div>;
  return <div className="center"><div className="auth-card" style={{maxWidth:560}}><div className="brand"><Mark tone="white"/><span className="brand-name">WatchLog</span></div><div className="target-eyebrow">Account access</div><h1>Your WatchLog account is temporarily paused</h1><p className="sub">Access for <strong>{name}</strong> is currently paused. Your sites, history and account information have not been deleted.</p><div className="card" style={{margin:"18px 0"}}><h2 style={{fontSize:16,textTransform:"none",letterSpacing:"-.02em",color:"#fff"}}>Need help?</h2><p className="muted" style={{marginBottom:0}}>Contact your WatchLog account manager or use your usual WatchLog support channel. Our team can review the account status and restore access when appropriate.</p></div>{email&&<p className="muted" style={{fontSize:12}}>Signed in as {email}</p>}<button className="ghost" onClick={signOut}>Sign out</button></div></div>;
}
