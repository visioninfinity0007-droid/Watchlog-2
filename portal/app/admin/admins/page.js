"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { AdminNav, requirePlatformAdmin } from "../admin-shell";
import styles from "../admin.module.css";

const fmt=v=>v?new Date(v).toLocaleString():"Never";

export default function PlatformAdmins(){
  const [admin,setAdmin]=useState(null);const [rows,setRows]=useState([]);const [email,setEmail]=useState("");const [role,setRole]=useState("platform_support");const [reason,setReason]=useState("");const [error,setError]=useState("");const [note,setNote]=useState("");const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{const {data,error}=await supabase().rpc("wl_platform_admins");if(error)setError(say(error));else setRows(data||[]);},[]);
  useEffect(()=>{(async()=>{const guard=await requirePlatformAdmin();if(!guard)return;setAdmin(guard.admin);if(guard.admin.role!=="platform_owner"){location.replace("/admin/");return;}await load();})();},[load]);
  async function save(e){e.preventDefault();setBusy(true);setError("");setNote("");const {error}=await supabase().rpc("wl_platform_set_admin",{p_email:email.trim(),p_role:role,p_reason:reason.trim()});if(error)setError(say(error));else{setNote("Platform access updated.");setEmail("");setReason("");await load();}setBusy(false);}
  async function remove(row){const why=prompt(`Reason for removing ${row.email} from platform administration:`);if(!why||why.trim().length<4)return;if(!confirm(`Remove platform access for ${row.email}?`))return;setBusy(true);const {error}=await supabase().rpc("wl_platform_remove_admin",{p_user_id:row.user_id,p_reason:why.trim()});if(error)setError(say(error));else{setNote("Platform access removed.");await load();}setBusy(false);}
  return <div className="shell"><AdminNav active="Admins" admin={admin}/><main className="main">
    <div className={styles.head}><div><h1>Platform administrators</h1><p>Platform access is separate from tenant roles. Grant the minimum role required for each internal user.</p></div></div>
    {error&&<div className="err">{error}</div>}{note&&<div className="ok-note">{note}</div>}
    <div className={styles.twoCol}><section className={styles.card}><h2>Current administrators</h2><div className={styles.stack}>{rows.map(r=><div className={styles.row} key={r.user_id}><div><strong>{r.email}</strong><small>Added {fmt(r.created_at)}{r.is_you?" · This is you":""}</small></div><div className={styles.actions}><span className={styles.badge}>{String(r.role).replace("platform_","")}</span><button className="btn-danger" disabled={busy} onClick={()=>remove(r)}>Remove</button></div></div>)}{!rows.length&&<div className={styles.empty}>No platform administrators are visible.</div>}</div></section>
    <section className={styles.card}><h2>Grant or change access</h2><form className={styles.form} onSubmit={save}><div><label>Existing WatchLog user email</label><input type="email" required value={email} onChange={e=>setEmail(e.target.value)} placeholder="name@company.com"/></div><div><label>Platform role</label><select value={role} onChange={e=>setRole(e.target.value)}><option value="platform_support">Support, read only</option><option value="platform_admin">Admin, tenant operations</option><option value="platform_owner">Owner, commercial and admin control</option></select></div><div><label>Reason</label><textarea required minLength="4" value={reason} onChange={e=>setReason(e.target.value)} placeholder="Why does this person need platform access?"/></div><button disabled={busy}>{busy?"Saving...":"Save platform access"}</button></form></section></div>
    <section className={styles.card}><h2>Role boundaries</h2><div className={styles.detailGrid}><div className={styles.kv}><small>Platform support</small><strong>Read-only tenant, fleet and support visibility</strong></div><div className={styles.kv}><small>Platform admin</small><strong>Support visibility plus trial and tenant operations</strong></div><div className={styles.kv}><small>Platform owner</small><strong>Commercial override and platform-admin lifecycle</strong></div></div></section>
  </main></div>;
}
