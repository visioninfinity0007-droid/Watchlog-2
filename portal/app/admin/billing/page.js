"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { AdminNav, requirePlatformAdmin } from "../admin-shell";
import styles from "../admin.module.css";

const fmt=v=>v?new Date(v).toLocaleString():"Never";
const money=(n,c="PKR")=>n==null?"-":`${c} ${(Number(n)/100).toLocaleString()}`;

export default function BillingAdmin(){
  const [admin,setAdmin]=useState(null);const [data,setData]=useState(null);const [error,setError]=useState("");
  useEffect(()=>{(async()=>{const guard=await requirePlatformAdmin();if(!guard)return;setAdmin(guard.admin);const {data,error}=await supabase().rpc("wl_platform_billing");if(error){setError(say(error));return;}setData(data);})();},[]);
  const byPlan=Object.fromEntries((data?.by_plan||[]).map(x=>[x.plan,Number(x.tenants)]));const byStatus=Object.fromEntries((data?.by_status||[]).map(x=>[x.status,Number(x.tenants)]));
  return <div className="shell"><AdminNav active="Billing" admin={admin}/><main className="main">
    <div className={styles.head}><div><h1>Billing and trials</h1><p>Commercial status across WatchLog. Customer self-service still cannot grant paid state.</p></div></div>
    {error&&<div className="err">{error}</div>}
    {!data?<div className={styles.card}>Loading billing status...</div>:<>
      <div className={styles.grid4}><div className={styles.metric}><div className={styles.metricLabel}>Starter</div><div className={styles.metricValue}>{byPlan.starter||0}</div><div className={styles.metricHint}>Paid or manual Starter tenants</div></div><div className={styles.metric}><div className={styles.metricLabel}>Growth</div><div className={styles.metricValue}>{byPlan.growth||0}</div><div className={styles.metricHint}>Growth tenants</div></div><div className={styles.metric}><div className={styles.metricLabel}>Enterprise</div><div className={styles.metricValue}>{byPlan.enterprise||0}</div><div className={styles.metricHint}>Contact-led accounts</div></div><div className={styles.metric}><div className={styles.metricLabel}>Past due</div><div className={styles.metricValue}>{byStatus.past_due||0}</div><div className={styles.metricHint}>{byStatus.trialing||0} trialing</div></div></div>
      <div className={styles.twoCol}>
        <section className={styles.card}><h2>Trials expiring within 7 days</h2><div className={styles.stack}>{(data.trials_expiring||[]).map(t=><a className={styles.row} style={{textDecoration:"none"}} href={`/admin/tenants/?tenant=${t.tenant_id}`} key={t.tenant_id}><div><strong>{t.tenant}</strong><small>Ends {fmt(t.ends_at)}</small></div><span className={`${styles.badge} ${styles.warn}`}>Review</span></a>)}{!(data.trials_expiring||[]).length&&<div className={styles.empty}>No trials expire in the next 7 days.</div>}</div></section>
        <section className={styles.card}><h2>Commercial safeguards</h2><div className={styles.stack}><div className={styles.row}><div><strong>Tenant self-pay escalation</strong><small>Authoritative subscription writer remains unavailable to tenant roles.</small></div><span className={`${styles.badge} ${styles.ok}`}>Blocked</span></div><div className={styles.row}><div><strong>Manual overrides</strong><small>Platform owner only, with required reason and audit record.</small></div><span className={`${styles.badge} ${styles.ok}`}>Audited</span></div></div></section>
      </div>
      <section className={styles.card}><h2>Recent transactions</h2><div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Tenant</th><th>Plan</th><th>Amount</th><th>Provider</th><th>Status</th><th>Created</th></tr></thead><tbody>{(data.transactions||[]).map((p,i)=><tr key={`${p.tenant_id}-${p.created_at}-${i}`}><td><a className={styles.link} href={`/admin/tenants/?tenant=${p.tenant_id}`}>{p.tenant}</a></td><td>{p.plan||"-"}</td><td>{money(p.amount_minor,p.currency)}</td><td>{p.provider}</td><td><span className={`${styles.badge} ${p.status==="succeeded"?styles.ok:p.status==="failed"?styles.bad:styles.muted}`}>{p.status}</span></td><td>{fmt(p.created_at)}</td></tr>)}{!(data.transactions||[]).length&&<tr><td colSpan="6"><div className={styles.empty}>No transactions in the last 90 days.</div></td></tr>}</tbody></table></div></section>
    </>}
  </main></div>;
}
