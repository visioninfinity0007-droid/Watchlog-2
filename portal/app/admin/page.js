"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { AdminNav, requirePlatformAdmin, roleLabel } from "./admin-shell";
import styles from "./admin.module.css";

function when(v){if(!v)return"Never";try{return new Date(v).toLocaleString();}catch{return String(v);}}

export default function PlatformOverview(){
  const[admin,setAdmin]=useState(null),[data,setData]=useState(null),[error,setError]=useState("");
  useEffect(()=>{(async()=>{const guard=await requirePlatformAdmin();if(!guard)return;setAdmin(guard.admin);const{data,error}=await supabase().rpc("wl_platform_overview");if(error)setError(say(error));else setData(data);})();},[]);
  const customers=data?.tenants||{},connections=data?.agents||{},last=data?.last_24h||{};
  return <div className="shell"><AdminNav active="Overview" admin={admin}/><main className="main">
    <div className={styles.head}><div><div className={styles.role}>{admin?roleLabel(admin.role):"Platform"}</div><h1>WatchLog operations</h1><p>Customer, commercial, service and reporting health across the SaaS business.</p></div><div className={styles.actions}><a className={styles.link} href="/admin/tenants/">Customers</a><a className={styles.link} href="/admin/billing/">Commercial</a><a className={styles.link} href="/admin/support/">Support</a></div></div>
    {error&&<div className="err">{error}</div>}
    {!data?<div className={styles.card}>Loading WatchLog operations...</div>:<>
      <div className={styles.grid4}><div className={styles.metric}><div className={styles.metricLabel}>Customers</div><div className={styles.metricValue}>{customers.total||0}</div><div className={styles.metricHint}>{customers.active||0} active, {customers.trialing||0} trialing</div></div><div className={styles.metric}><div className={styles.metricLabel}>Sites</div><div className={styles.metricValue}>{data.sites||0}</div><div className={styles.metricHint}>{data.cameras||0} cameras discovered</div></div><div className={styles.metric}><div className={styles.metricLabel}>Site connections online</div><div className={styles.metricValue}>{connections.online||0}/{connections.total||0}</div><div className={styles.metricHint}>{connections.offline||0} currently offline</div></div><div className={styles.metric}><div className={styles.metricLabel}>Reports today</div><div className={styles.metricValue}>{last.reports_sent||0}</div><div className={styles.metricHint}>{last.report_failures||0} failures in 24 hours</div></div></div>
      <div className={styles.grid4}><div className={styles.metric}><div className={styles.metricLabel}>Incidents, 24h</div><div className={styles.metricValue}>{last.incidents||0}</div><div className={styles.metricHint}>Across all customers</div></div><div className={styles.metric}><div className={styles.metricLabel}>Analytics insights, 24h</div><div className={styles.metricValue}>{last.analytics||0}</div><div className={styles.metricHint}>Operational analytics activity</div></div><div className={styles.metric}><div className={styles.metricLabel}>Trials expiring</div><div className={styles.metricValue}>{data.trials_expiring_3d||0}</div><div className={styles.metricHint}>Within the next 3 days</div></div><div className={styles.metric}><div className={styles.metricLabel}>Past due</div><div className={styles.metricValue}>{customers.past_due||0}</div><div className={styles.metricHint}>{customers.inactive||0} cancelled or expired</div></div></div>
      <section className={styles.card}><div className={styles.actions} style={{justifyContent:"space-between"}}><div><h2 style={{marginBottom:4}}>Recent customers</h2><p className="muted" style={{margin:0}}>Newest accounts and their latest site connection.</p></div><a className={styles.link} href="/admin/tenants/">View all customers</a></div><div className={styles.tableWrap} style={{marginTop:18}}><table className={styles.table}><thead><tr><th>Customer</th><th>Plan</th><th>Subscription</th><th>Sites</th><th>Last site contact</th></tr></thead><tbody>{(data.recent_tenants||[]).map(t=><tr key={t.id}><td><a className={styles.link} href={`/admin/tenants/?tenant=${t.id}`}>{t.name}</a></td><td>{t.plan}</td><td><span className={`${styles.badge} ${t.subscription_status==="active"?styles.ok:t.subscription_status==="trialing"?styles.warn:styles.muted}`}>{t.subscription_status}</span></td><td>{t.sites}</td><td>{when(t.last_seen_at)}</td></tr>)}</tbody></table></div></section>
    </>}
  </main></div>;
}
