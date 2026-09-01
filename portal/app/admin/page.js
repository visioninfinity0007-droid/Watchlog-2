"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { AdminNav, requirePlatformAdmin, roleLabel } from "./admin-shell";
import styles from "./admin.module.css";

function when(v) {
  if (!v) return "Never";
  try { return new Date(v).toLocaleString(); } catch { return String(v); }
}

export default function PlatformOverview() {
  const [admin,setAdmin]=useState(null);
  const [data,setData]=useState(null);
  const [error,setError]=useState("");

  useEffect(()=>{(async()=>{
    const guard=await requirePlatformAdmin(); if(!guard)return;
    setAdmin(guard.admin);
    const {data,error}=await supabase().rpc("wl_platform_overview");
    if(error)setError(say(error));else setData(data);
  })();},[]);

  const tenants=data?.tenants||{}; const agents=data?.agents||{}; const last=data?.last_24h||{};
  return <div className="shell">
    <AdminNav active="Overview" admin={admin}/>
    <main className="main">
      <div className={styles.head}><div>
        <div className={styles.role}>{admin?roleLabel(admin.role):"Platform"}</div>
        <h1>Platform overview</h1>
        <p>Commercial, fleet and reporting health across every WatchLog customer account.</p>
      </div></div>
      {error&&<div className="err">{error}</div>}
      {!data?<div className={styles.card}>Loading platform status...</div>:<>
        <div className={styles.grid4}>
          <div className={styles.metric}><div className={styles.metricLabel}>Tenants</div><div className={styles.metricValue}>{tenants.total||0}</div><div className={styles.metricHint}>{tenants.active||0} active, {tenants.trialing||0} trialing</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Sites</div><div className={styles.metricValue}>{data.sites||0}</div><div className={styles.metricHint}>{data.cameras||0} cameras discovered</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Agents online</div><div className={styles.metricValue}>{agents.online||0}/{agents.total||0}</div><div className={styles.metricHint}>{agents.offline||0} currently offline</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Reports today</div><div className={styles.metricValue}>{last.reports_sent||0}</div><div className={styles.metricHint}>{last.report_failures||0} failures in 24 hours</div></div>
        </div>
        <div className={styles.grid4}>
          <div className={styles.metric}><div className={styles.metricLabel}>Incidents, 24h</div><div className={styles.metricValue}>{last.incidents||0}</div><div className={styles.metricHint}>Across all tenants</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Analytics, 24h</div><div className={styles.metricValue}>{last.analytics||0}</div><div className={styles.metricHint}>Measurements, not incident count</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Trials expiring</div><div className={styles.metricValue}>{data.trials_expiring_3d||0}</div><div className={styles.metricHint}>Within the next 3 days</div></div>
          <div className={styles.metric}><div className={styles.metricLabel}>Past due</div><div className={styles.metricValue}>{tenants.past_due||0}</div><div className={styles.metricHint}>{tenants.inactive||0} cancelled or expired</div></div>
        </div>
        <section className={styles.card}>
          <div className={styles.actions} style={{justifyContent:"space-between"}}><div><h2 style={{marginBottom:4}}>Recent tenants</h2><p className="muted" style={{margin:0}}>Newest accounts and their latest Site Agent contact.</p></div><a className={styles.link} href="/admin/tenants/">View all tenants</a></div>
          <div className={styles.tableWrap} style={{marginTop:18}}><table className={styles.table}><thead><tr><th>Tenant</th><th>Plan</th><th>Status</th><th>Sites</th><th>Last agent contact</th></tr></thead><tbody>
            {(data.recent_tenants||[]).map(t=><tr key={t.id}><td><a className={styles.link} href={`/admin/tenants/?tenant=${t.id}`}>{t.name}</a></td><td>{t.plan}</td><td><span className={`${styles.badge} ${t.subscription_status==="active"?styles.ok:t.subscription_status==="trialing"?styles.warn:styles.muted}`}>{t.subscription_status}</span></td><td>{t.sites}</td><td>{when(t.last_seen_at)}</td></tr>)}
          </tbody></table></div>
        </section>
      </>}
    </main>
  </div>;
}
