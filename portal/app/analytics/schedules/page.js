"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { Nav, requireTenant } from "../../shell";
import styles from "../analytics.module.css";

const DAYS = [
  ["mon","Monday"],["tue","Tuesday"],["wed","Wednesday"],["thu","Thursday"],
  ["fri","Friday"],["sat","Saturday"],["sun","Sunday"],
];

function initialWindows() {
  return Object.fromEntries(DAYS.map(([k]) => [k, { enabled: !["sat","sun"].includes(k), start: "08:00", end: "18:00" }]));
}

function fromSchedule(schedule) {
  const out = initialWindows();
  const days = schedule?.days || {};
  for (const [key] of DAYS) {
    const first = days[key]?.[0];
    out[key] = first ? { enabled: true, start: first[0], end: first[1] } : { ...out[key], enabled: false };
  }
  return out;
}

function toSchedule(windows) {
  const days = {};
  for (const [key] of DAYS) {
    const w = windows[key];
    days[key] = w?.enabled ? [[w.start, w.end]] : [];
  }
  return { days };
}

export default function Schedules() {
  const [email,setEmail] = useState("");
  const [studio,setStudio] = useState(null);
  const [siteId,setSiteId] = useState("");
  const [editing,setEditing] = useState(null);
  const [name,setName] = useState("Business hours");
  const [windows,setWindows] = useState(initialWindows());
  const [error,setError] = useState("");
  const [note,setNote] = useState("");
  const [busy,setBusy] = useState(false);

  const load = useCallback(async () => {
    const guard = await requireTenant(); if (!guard) return;
    setEmail(guard.session.user.email || "");
    const {data,error} = await supabase().rpc("wl_analytics_studio");
    if (error) { setError(say(error)); return; }
    setStudio(data || {sites:[],can_manage:false});
    const sites=data?.sites||[];
    setSiteId((old)=>sites.some((s)=>s.id===old)?old:(sites[0]?.id||""));
  },[]);
  useEffect(()=>{load();},[load]);

  const sites=studio?.sites||[];
  const site=sites.find((s)=>s.id===siteId);
  const schedules=site?.schedules||[];
  const canManage=studio?.can_manage!==false;

  function newSchedule(preset="business") {
    if(!canManage)return;
    setEditing(null);
    if (preset === "after") {
      setName("Night shift");
      const w=initialWindows();
      for (const [key] of DAYS) w[key]={enabled:!["sat","sun"].includes(key),start:"18:00",end:"08:00"};
      setWindows(w);
    } else if (preset === "weekend") {
      setName("Weekend");
      const w=initialWindows();
      for (const [key] of DAYS) w[key]={enabled:["sat","sun"].includes(key),start:"00:00",end:"23:59"};
      setWindows(w);
    } else {
      setName("Business hours"); setWindows(initialWindows());
    }
  }

  function edit(s) {
    if(!canManage)return;
    setEditing(s.id); setName(s.name); setWindows(fromSchedule(s.schedule));
  }

  async function save() {
    if (!site || !canManage) return;
    if (!name.trim()) { setError("Give this schedule a name."); return; }
    setBusy(true); setError(""); setNote("");
    const {error}=await supabase().rpc("wl_upsert_monitoring_schedule",{
      p_id:editing,p_site_id:site.id,p_name:name.trim(),p_timezone:site.timezone||"Asia/Karachi",
      p_schedule:toSchedule(windows),p_enabled:true,
    });
    if(error)setError(say(error)); else {setNote("Schedule published. Rules using it will be picked up by the Site Agent automatically.");setEditing(null);await load();}
    setBusy(false);
  }

  async function remove(s) {
    if(!canManage)return;
    if(Number(s.rule_count||0)>0){setError(`This schedule is used by ${s.rule_count} monitoring rule${Number(s.rule_count)===1?"":"s"}. Reassign those rules first.`);return;}
    if(!confirm("Delete this schedule?"))return;
    setBusy(true);setError("");
    const {error}=await supabase().rpc("wl_delete_monitoring_schedule",{p_schedule_id:s.id});
    if(error)setError(say(error));else{setNote("Schedule deleted.");await load();}
    setBusy(false);
  }

  function setDay(key,patch){if(canManage)setWindows((w)=>({...w,[key]:{...w[key],...patch}}));}

  return <div className="shell">
    <Nav active="Analytics" email={email}/>
    <main className="main">
      <div className={styles.pageHead}>
        <div><div className={styles.subnav}><a href="/analytics/">Overview</a><a href="/analytics/studio/">Analytics Studio</a><a className={styles.current} href="/analytics/schedules/">Schedules</a></div>
          <h1 style={{marginTop:22}}>Monitoring schedules</h1>
          <p>Define business hours, shifts or weekends once, then reuse them across camera rules. Overnight windows such as 18:00 to 08:00 are supported.</p></div>
        <div className={styles.actions}><select value={siteId} onChange={(e)=>setSiteId(e.target.value)} style={{width:"auto",margin:0}} aria-label="Site">
          {sites.map((s)=><option key={s.id} value={s.id}>{s.name}</option>)}</select></div>
      </div>
      {error&&<div className="err">{error}</div>}{note&&<div className="ok-note">{note}</div>}
      {!canManage&&<div className={styles.readOnly}>You have read-only access. An Owner or Admin can publish or delete monitoring schedules.</div>}
      {!site?<div className={styles.card}>No site available.</div>:<div className={styles.twoCol}>
        <section className={styles.card}>
          <h2>Published schedules</h2>
          <div className={styles.ruleList}>{schedules.length?schedules.map((s)=><div className={styles.rule} key={s.id}>
            <div><div className={styles.ruleTitle}>{s.name}</div><div className={styles.ruleMeta}>{s.timezone} · weekly schedule · {Number(s.rule_count||0)} rule{Number(s.rule_count||0)===1?"":"s"}</div></div>
            {canManage&&<div className={styles.ruleActions}><button className="ghost small" onClick={()=>edit(s)}>Edit</button><button className="btn-danger" onClick={()=>remove(s)} disabled={busy||Number(s.rule_count||0)>0}>Delete</button></div>}
          </div>):<div className={styles.empty}>No reusable schedules yet.</div>}</div>
          {canManage&&<><h2>Quick starts</h2><div className={styles.packRow}>
            <button className={styles.pack} onClick={()=>newSchedule("business")}><strong>Business hours</strong><small>Mon to Fri, 08:00 to 18:00</small></button>
            <button className={styles.pack} onClick={()=>newSchedule("after")}><strong>Night shift</strong><small>Mon to Fri, 18:00 to 08:00</small></button>
            <button className={styles.pack} onClick={()=>newSchedule("weekend")}><strong>Weekend</strong><small>Saturday and Sunday</small></button>
          </div></>}
        </section>
        <section className={styles.card}>
          <h2>{canManage?(editing?"Edit schedule":"Schedule editor"):"Schedule details"}</h2>
          <div className={styles.field}><label>Name</label><input disabled={!canManage} value={name} onChange={(e)=>setName(e.target.value)}/></div>
          <div className={styles.scheduleBox}>{DAYS.map(([key,label])=>{const w=windows[key];return <div className={styles.day} key={key}>
            <label className={styles.check}><input type="checkbox" disabled={!canManage} checked={w.enabled} onChange={(e)=>setDay(key,{enabled:e.target.checked})}/>{label}</label>
            <input type="time" disabled={!canManage||!w.enabled} value={w.start} onChange={(e)=>setDay(key,{start:e.target.value})}/>
            <input type="time" disabled={!canManage||!w.enabled} value={w.end} onChange={(e)=>setDay(key,{end:e.target.value})}/>
          </div>})}</div>
          <p className="muted" style={{fontSize:"var(--font-size-xs)"}}>WatchLog v1 uses one monitoring window per day. If the end time is earlier than the start time, the window crosses midnight.</p>
          {canManage&&<div className={styles.actions}><button disabled={busy} onClick={save}>{busy?"Saving...":"Publish schedule"}</button><button className="ghost" onClick={()=>{setEditing(null);newSchedule("business")}}>Reset</button></div>}
        </section>
      </div>}
    </main>
  </div>;
}