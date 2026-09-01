"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { AdminNav, canOperate, isOwner, requirePlatformAdmin } from "../admin-shell";
import styles from "../admin.module.css";

const fmt=(v)=>v?new Date(v).toLocaleString():"Never";
const money=(n,c="PKR")=>n==null?"-":`${c} ${(Number(n)/100).toLocaleString()}`;

function Status({value}){
  const cls=value==="active"?styles.ok:value==="trialing"?styles.warn:value==="past_due"?styles.bad:styles.muted;
  return <span className={`${styles.badge} ${cls}`}>{value||"unknown"}</span>;
}

export default function Tenants(){
  const [admin,setAdmin]=useState(null); const [items,setItems]=useState([]); const [total,setTotal]=useState(0);
  const [search,setSearch]=useState(""); const [status,setStatus]=useState(""); const [selected,setSelected]=useState(null);
  const [detail,setDetail]=useState(null); const [error,setError]=useState(""); const [note,setNote]=useState(""); const [busy,setBusy]=useState(false);
  const [trialDays,setTrialDays]=useState(14); const [trialReason,setTrialReason]=useState("");
  const [plan,setPlan]=useState("starter"); const [subStatus,setSubStatus]=useState("active"); const [billingReason,setBillingReason]=useState("");

  const loadList=useCallback(async(q=search,st=status)=>{
    const {data,error}=await supabase().rpc("wl_platform_tenants",{p_search:q||null,p_status:st||null,p_limit:200,p_offset:0});
    if(error){setError(say(error));return;} setItems(data?.items||[]);setTotal(data?.total||0);setError("");
  },[search,status]);

  const loadDetail=useCallback(async(id)=>{
    if(!id){setDetail(null);return;}
    const {data,error}=await supabase().rpc("wl_platform_tenant",{p_tenant_id:id});
    if(error){setError(say(error));return;} setDetail(data);setSelected(id);setError("");
  },[]);

  useEffect(()=>{(async()=>{
    const guard=await requirePlatformAdmin(); if(!guard)return; setAdmin(guard.admin);
    const id=new URLSearchParams(location.search).get("tenant");
    await loadList("",""); if(id)await loadDetail(id);
  })();},[]); // eslint-disable-line react-hooks/exhaustive-deps

  async function choose(id){history.replaceState({},"",`/admin/tenants/?tenant=${id}`);await loadDetail(id);}
  async function applyFilters(e){e?.preventDefault();await loadList();}

  async function grantTrial(e){
    e.preventDefault(); if(!selected)return; setBusy(true);setNote("");setError("");
    const {error}=await supabase().rpc("wl_platform_grant_trial",{p_tenant_id:selected,p_days:Number(trialDays),p_reason:trialReason.trim()});
    if(error)setError(say(error));else{setNote(`Trial granted for ${trialDays} days from today.`);setTrialReason("");await Promise.all([loadDetail(selected),loadList()]);}
    setBusy(false);
  }

  async function overrideSubscription(e){
    e.preventDefault(); if(!selected)return;
    if(!confirm(`Set this tenant to ${plan} / ${subStatus}? This is an audited platform-owner override.`))return;
    setBusy(true);setNote("");setError("");
    const {error}=await supabase().rpc("wl_platform_set_subscription",{p_tenant_id:selected,p_plan:plan,p_status:subStatus,p_reason:billingReason.trim()});
    if(error)setError(say(error));else{setNote(`Subscription set to ${plan} / ${subStatus}.`);setBillingReason("");await Promise.all([loadDetail(selected),loadList()]);}
    setBusy(false);
  }

  const t=detail?.tenant; const sites=detail?.sites||[]; const members=detail?.members||[];
  const selectedSummary=useMemo(()=>items.find(x=>x.id===selected),[items,selected]);

  return <div className="shell"><AdminNav active="Tenants" admin={admin}/><main className="main">
    <div className={styles.head}><div><h1>Tenants</h1><p>Search every WatchLog customer, inspect operational health and perform explicitly audited support or commercial actions.</p></div></div>
    {error&&<div className="err">{error}</div>}{note&&<div className="ok-note">{note}</div>}
    <form className={styles.toolbar} onSubmit={applyFilters}>
      <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search tenant or owner email" aria-label="Search tenants"/>
      <select value={status} onChange={e=>setStatus(e.target.value)} aria-label="Subscription status"><option value="">All statuses</option><option value="trialing">Trialing</option><option value="active">Active</option><option value="past_due">Past due</option><option value="cancelled">Cancelled</option><option value="expired">Expired</option></select>
      <button type="submit">Search</button><span className="muted">{total} tenant{total===1?"":"s"}</span>
    </form>

    <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Tenant</th><th>Owner</th><th>Plan</th><th>Estate</th><th>Agents</th><th>24h activity</th><th>Last report</th></tr></thead><tbody>
      {items.map(x=><tr key={x.id} onClick={()=>choose(x.id)} style={{cursor:"pointer",background:selected===x.id?"rgba(139,107,255,.06)":undefined}}>
        <td><a className={styles.link} href={`/admin/tenants/?tenant=${x.id}`} onClick={e=>{e.preventDefault();choose(x.id);}}>{x.name}</a><div style={{marginTop:6}}><Status value={x.subscription_status}/></div></td>
        <td>{x.owner_email||"No owner"}</td><td>{x.plan}{x.requested_plan&&<div className="muted">Requested: {x.requested_plan}</div>}</td>
        <td>{x.sites} sites<br/><span className="muted">{x.cameras} cameras</span></td>
        <td>{x.agents_online}/{x.agents_total} online<br/><span className="muted">{fmt(x.last_seen_at)}</span></td>
        <td>{x.incidents_24h} incidents<br/><span className="muted">{x.analytics_24h} analytics</span></td><td>{fmt(x.last_report_at)}</td>
      </tr>)}
      {!items.length&&<tr><td colSpan="7"><div className={styles.empty}>No tenants match these filters.</div></td></tr>}
    </tbody></table></div>

    {t&&<div style={{marginTop:"var(--space-6)"}}>
      <div className={styles.head}><div><div className={styles.role}>Tenant support view</div><h1 style={{fontSize:"var(--font-size-2xl)"}}>{t.name}</h1><p>{t.id}</p></div><div className={styles.actions}><Status value={t.subscription_status}/><span className={styles.badge}>{t.plan}</span></div></div>
      <div className={styles.detailGrid}>
        <div className={styles.kv}><small>Created</small><strong>{fmt(t.created_at)}</strong></div>
        <div className={styles.kv}><small>Trial ends</small><strong>{fmt(t.trial_ends_at)}</strong></div>
        <div className={styles.kv}><small>Estate</small><strong>{sites.length} sites, {selectedSummary?.cameras||0} cameras</strong></div>
      </div>
      <div className={styles.twoCol}>
        <section className={styles.card}><h2>Sites and hardware</h2><div className={styles.stack}>
          {sites.map(s=><div className={styles.row} key={s.id}><div><strong>{s.name}</strong><small>{s.site_type||"custom"} · {s.cameras} cameras · {s.analytics_rules||0} analytics rules</small><small>{(s.device_vendors||[]).filter(Boolean).join(", ")||"Recorder not identified"} · {(s.agent_versions||[]).filter(Boolean).join(", ")||"No agent version"}</small></div><div style={{textAlign:"right"}}><span className={`${styles.badge} ${s.agents_online?styles.ok:styles.bad}`}>{s.agents_online?"Agent online":"Agent offline"}</span><small style={{display:"block",marginTop:6,color:"var(--color-muted-dark)"}}>Last seen {fmt(s.last_seen_at)}</small></div></div>)}
          {!sites.length&&<div className={styles.empty}>No sites yet.</div>}
        </div></section>
        <section className={styles.card}><h2>Team</h2><div className={styles.stack}>{members.map(m=><div className={styles.row} key={m.user_id}><div><strong>{m.email}</strong><small>Joined {fmt(m.joined_at)}</small></div><span className={styles.badge}>{m.role}</span></div>)}{!members.length&&<div className={styles.empty}>No members.</div>}</div></section>
      </div>

      <div className={styles.twoCol}>
        <section className={styles.card}><h2>Reporting</h2><div className={styles.detailGrid}>
          <div className={styles.kv}><small>Recipients</small><strong>{(detail.report_recipients||[]).filter(r=>r.enabled).length}</strong></div>
          <div className={styles.kv}><small>Analytics rules</small><strong>{detail.analytics?.rules||0}</strong></div>
          <div className={styles.kv}><small>Measurements 30d</small><strong>{detail.analytics?.measurements_30d||0}</strong></div>
        </div><div className={styles.stack}>{(detail.deliveries||[]).slice(0,6).map((d,i)=><div className={styles.row} key={`${d.sent_at}-${i}`}><div><strong>{d.channel} · {d.report_date}</strong><small>{d.events||0} incidents · {fmt(d.sent_at)}</small>{d.error&&<small style={{color:"#ff8e8e"}}>{d.error}</small>}</div><span className={`${styles.badge} ${d.status==="sent"?styles.ok:d.status==="failed"?styles.bad:styles.muted}`}>{d.status}</span></div>)}</div></section>
        <section className={styles.card}><h2>Billing history</h2><div className={styles.stack}>{(detail.transactions||[]).map(p=><div className={styles.row} key={p.id}><div><strong>{p.plan||"Payment"} · {money(p.amount_minor,p.currency)}</strong><small>{p.provider} · {fmt(p.created_at)}</small></div><span className={`${styles.badge} ${p.status==="succeeded"?styles.ok:p.status==="failed"?styles.bad:styles.muted}`}>{p.status}</span></div>)}{!(detail.transactions||[]).length&&<div className={styles.empty}>No payment transactions.</div>}</div></section>
      </div>

      {canOperate(admin?.role)&&<section className={styles.card} style={{marginBottom:"var(--space-5)"}}><h2>Tenant controls</h2><p className="muted">Every change below requires a reason and writes to the platform audit log.</p><div className={styles.formGrid}>
        <form className={styles.form} onSubmit={grantTrial}><div className={styles.dangerBox}><h3>Grant or restart trial</h3><p>Starts a fresh trial from today. Existing customer data is preserved.</p><label>Days</label><input type="number" min="1" max="90" value={trialDays} onChange={e=>setTrialDays(e.target.value)}/><label>Reason</label><textarea required minLength="4" value={trialReason} onChange={e=>setTrialReason(e.target.value)} placeholder="Why is this trial being granted?"/><button disabled={busy}>Grant trial</button></div></form>
        {isOwner(admin?.role)?<form className={styles.form} onSubmit={overrideSubscription}><div className={styles.dangerBox}><h3>Manual subscription override</h3><p>Platform owner only. Creates a manual subscription record and cannot be triggered by the customer.</p><div className={styles.formGrid}><div><label>Plan</label><select value={plan} onChange={e=>setPlan(e.target.value)}><option value="starter">Starter</option><option value="growth">Growth</option><option value="enterprise">Enterprise</option></select></div><div><label>Status</label><select value={subStatus} onChange={e=>setSubStatus(e.target.value)}><option value="active">Active</option><option value="past_due">Past due</option><option value="cancelled">Cancelled</option><option value="expired">Expired</option></select></div></div><label>Reason</label><textarea required minLength="4" value={billingReason} onChange={e=>setBillingReason(e.target.value)} placeholder="Reference payment, commercial approval or support decision."/><button disabled={busy}>Apply override</button></div></form>:<div className={styles.dangerBox}><h3>Paid-state override</h3><p>Only a platform owner can change authoritative paid state. Platform admins can manage trials and operations.</p></div>}
      </div></section>}

      <section className={styles.card}><h2>Recent platform actions</h2><div className={styles.audit}>{(detail.audit||[]).map((a,i)=><div className={styles.auditItem} key={`${a.created_at}-${i}`}><div className={styles.auditTop}><strong>{String(a.action).replaceAll("_"," ")}</strong><span className={styles.role}>{a.actor_role}</span></div><div className={styles.auditReason}>{a.reason} · {fmt(a.created_at)}</div></div>)}{!(detail.audit||[]).length&&<div className={styles.empty}>No platform actions for this tenant.</div>}</div></section>
    </div>}
  </main></div>;
}
