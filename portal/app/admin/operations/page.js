"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { AdminNav, requirePlatformAdmin } from "../admin-shell";
import styles from "../admin.module.css";

const fmt=v=>v?new Date(v).toLocaleString():"Never";

export default function Operations(){
  const [admin,setAdmin]=useState(null);const [sites,setSites]=useState([]);const [error,setError]=useState("");const [filter,setFilter]=useState("all");
  useEffect(()=>{(async()=>{const guard=await requirePlatformAdmin();if(!guard)return;setAdmin(guard.admin);const {data,error}=await supabase().rpc("wl_platform_operations",{p_limit:500});if(error)setError(say(error));else setSites(data?.sites||[]);})();},[]);
  const rows=useMemo(()=>sites.filter(s=>filter==="all"||(filter==="offline"&&!s.agent_online)||(filter==="report_failures"&&Number(s.report_failures_7d)>0)||(filter==="analytics"&&Number(s.analytics_rules)>0)),[sites,filter]);
  const offline=sites.filter(s=>!s.agent_online).length;const failed=sites.filter(s=>Number(s.report_failures_7d)>0).length;
  return <div className="shell"><AdminNav active="Operations" admin={admin}/><main className="main">
    <div className={styles.head}><div><h1>Operations</h1><p>One fleet view for site PCs, recorders, camera estate, analytics activity and daily report health.</p></div></div>
    {error&&<div className="err">{error}</div>}
    <div className={styles.grid4}><div className={styles.metric}><div className={styles.metricLabel}>Sites</div><div className={styles.metricValue}>{sites.length}</div><div className={styles.metricHint}>Across all tenants</div></div><div className={styles.metric}><div className={styles.metricLabel}>Agent offline</div><div className={styles.metricValue}>{offline}</div><div className={styles.metricHint}>No heartbeat in 5 minutes</div></div><div className={styles.metric}><div className={styles.metricLabel}>Report issues</div><div className={styles.metricValue}>{failed}</div><div className={styles.metricHint}>Failed delivery in 7 days</div></div><div className={styles.metric}><div className={styles.metricLabel}>Analytics sites</div><div className={styles.metricValue}>{sites.filter(s=>Number(s.analytics_rules)>0).length}</div><div className={styles.metricHint}>At least one active rule</div></div></div>
    <div className={styles.toolbar}><select value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">All sites</option><option value="offline">Agent offline</option><option value="report_failures">Report failures</option><option value="analytics">Analytics configured</option></select><span className="muted">{rows.length} site{rows.length===1?"":"s"}</span></div>
    <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Tenant / site</th><th>Recorder</th><th>Agent</th><th>Cameras</th><th>24h activity</th><th>Reporting</th></tr></thead><tbody>{rows.map(s=><tr key={s.site_id}><td><a className={styles.link} href={`/admin/tenants/?tenant=${s.tenant_id}`}>{s.tenant}</a><div>{s.site}</div><span className="muted">{s.site_type||"custom"}</span></td><td>{s.recorder_vendor||"Not identified"}</td><td><span className={`${styles.badge} ${s.agent_online?styles.ok:styles.bad}`}>{s.agent_online?"Online":"Offline"}</span><div className="muted" style={{marginTop:6}}>{s.agent_version||"Unknown version"}</div><div className="muted">{fmt(s.last_seen_at)}</div></td><td>{s.cameras}<div className="muted">{s.analytics_rules} analytics rules</div></td><td>{s.incidents_24h} incidents<div className="muted">{s.analytics_24h} analytics</div></td><td>{fmt(s.last_report_at)}<div style={{marginTop:6}}><span className={`${styles.badge} ${Number(s.report_failures_7d)>0?styles.bad:styles.ok}`}>{Number(s.report_failures_7d)>0?`${s.report_failures_7d} failures`:`No recent failures`}</span></div></td></tr>)}{!rows.length&&<tr><td colSpan="6"><div className={styles.empty}>No sites match this operational filter.</div></td></tr>}</tbody></table></div>
  </main></div>;
}
