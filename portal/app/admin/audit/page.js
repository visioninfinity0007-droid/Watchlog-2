"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { AdminNav, requirePlatformAdmin } from "../admin-shell";
import styles from "../admin.module.css";

const fmt=v=>v?new Date(v).toLocaleString():"Never";

export default function Audit(){
  const [admin,setAdmin]=useState(null);const [rows,setRows]=useState([]);const [error,setError]=useState("");
  useEffect(()=>{(async()=>{const guard=await requirePlatformAdmin();if(!guard)return;setAdmin(guard.admin);const {data,error}=await supabase().rpc("wl_platform_audit",{p_limit:500});if(error)setError(say(error));else setRows(data||[]);})();},[]);
  return <div className="shell"><AdminNav active="Audit" admin={admin}/><main className="main">
    <div className={styles.head}><div><h1>Platform audit</h1><p>Every cross-tenant commercial or administrator change is attributable to a platform user and a written reason.</p></div></div>
    {error&&<div className="err">{error}</div>}
    <div className={styles.audit}>{rows.map(a=><div className={styles.auditItem} key={a.id}><div className={styles.auditTop}><div><strong>{String(a.action).replaceAll("_"," ")}</strong>{a.tenant&&<> · <a className={styles.link} href={`/admin/tenants/?tenant=${a.tenant_id}`}>{a.tenant}</a></>}</div><span className={styles.role}>{a.actor_role}</span></div><div className={styles.auditReason}>{a.reason}</div><div className="muted" style={{marginTop:7,fontSize:"var(--font-size-xs)"}}>{a.actor_email||"System user"} · {fmt(a.created_at)}</div></div>)}{!rows.length&&<div className={styles.card}><div className={styles.empty}>No audited platform actions yet.</div></div>}</div>
  </main></div>;
}
