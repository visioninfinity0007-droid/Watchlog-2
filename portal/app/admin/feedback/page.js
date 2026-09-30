"use client";

import {useCallback,useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../../lib/supabase";
import {AdminNav,requirePlatformAdmin} from "../admin-shell";
import styles from "../admin.module.css";

const RESPONSE_LABELS={
  accepted:"We'll do this",
  need_help:"Need help",
  not_now:"Not now",
  not_relevant:"Not relevant",
};
const STATUS_LABELS={
  new:"New",
  in_progress:"In progress",
  resolved:"Resolved",
  closed:"Closed",
};
const fmt=v=>v?new Date(v).toLocaleString():"-";

export default function RecommendationFeedback(){
  const[admin,setAdmin]=useState(null);
  const[items,setItems]=useState([]);
  const[status,setStatus]=useState("new");
  const[selected,setSelected]=useState("");
  const[teamNote,setTeamNote]=useState("");
  const[loading,setLoading]=useState(true);
  const[busy,setBusy]=useState(false);
  const[error,setError]=useState("");
  const[note,setNote]=useState("");

  const load=useCallback(async(nextStatus=status)=>{
    setLoading(true);setError("");
    const{data,error}=await supabase().rpc("wl_platform_recommendation_feedback",{
      p_tenant_id:null,
      p_status:nextStatus||null,
      p_limit:250,
    });
    setLoading(false);
    if(error){setError(say(error));return;}
    const rows=data?.items||[];
    setItems(rows);
    if(selected&&!rows.some(x=>x.id===selected))setSelected("");
  },[status,selected]);

  useEffect(()=>{(async()=>{
    const guard=await requirePlatformAdmin();if(!guard)return;
    setAdmin(guard.admin);
    await load("new");
  })();},[]); // eslint-disable-line react-hooks/exhaustive-deps

  const current=useMemo(()=>items.find(x=>x.id===selected)||null,[items,selected]);

  function choose(item){
    setSelected(item.id);
    setTeamNote(item.team_note||"");
    setNote("");
  }

  async function updateStatus(next){
    if(!current)return;
    setBusy(true);setError("");setNote("");
    const{error}=await supabase().rpc("wl_platform_set_recommendation_feedback_status",{
      p_feedback_id:current.id,
      p_status:next,
      p_team_note:teamNote.trim()||null,
      p_reason:`Recommendation feedback moved to ${next}`,
    });
    setBusy(false);
    if(error){setError(say(error));return;}
    setNote(`Feedback marked ${STATUS_LABELS[next]||next}.`);
    await load(status);
  }

  async function applyStatus(e){
    const next=e.target.value;
    setStatus(next);
    setSelected("");
    setTeamNote("");
    await load(next);
  }

  return <div className="shell"><AdminNav active="Feedback" admin={admin}/><main className="main">
    <div className={styles.head}><div><div className={styles.role}>Customer feedback</div><h1>Recommendation follow-up</h1><p>See how customers respond to WatchLog recommendations, capture their context, and turn accepted ideas or help requests into follow-up work.</p></div></div>
    {error&&<div className="err">{error}</div>}
    {note&&<div className={styles.feedbackNotice}>{note}</div>}

    <section className={styles.card} style={{marginBottom:18}}>
      <div className={styles.toolbar}>
        <label htmlFor="feedback-status" className="muted">Show</label>
        <select id="feedback-status" value={status} onChange={applyStatus}>
          <option value="new">New</option>
          <option value="in_progress">In progress</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
          <option value="">All feedback</option>
        </select>
        <span className="muted">{items.length} response{items.length===1?"":"s"}</span>
      </div>
    </section>

    <div className={styles.feedbackWorkspace}>
      <section className={styles.card}>
        <div className={styles.actions} style={{justifyContent:"space-between"}}><div><h2>Client responses</h2><p className="muted">Newest feedback first.</p></div><button className="ghost small" onClick={()=>load(status)} disabled={loading}>{loading?"Loading…":"Refresh"}</button></div>
        <div className={styles.feedbackQueue}>
          {items.map(item=><button type="button" key={item.id} className={styles.feedbackQueueItem+" "+(selected===item.id?styles.feedbackQueueItemActive:"")} onClick={()=>choose(item)}>
            <div className={styles.feedbackQueueTop}><strong>{item.recommendation_title}</strong><span className={styles.badge}>{STATUS_LABELS[item.team_status]||item.team_status}</span></div>
            <div className={styles.feedbackQueueMeta}>{item.tenant_name} · {item.site_name}</div>
            <div className={styles.feedbackQueueResponse}><b>{RESPONSE_LABELS[item.response_code]||item.response_code}</b>{item.client_note&&<span>“{item.client_note}”</span>}</div>
            <small>{item.client_email} · {fmt(item.updated_at)}</small>
          </button>)}
          {!loading&&!items.length&&<div className={styles.empty}>No recommendation feedback in this state.</div>}
        </div>
      </section>

      <section className={styles.card}>
        {!current?<div className={styles.empty}>Select a client response to review it.</div>:<>
          <div className={styles.feedbackDetailHead}><div><div className={styles.role}>{current.tenant_name} · {current.site_name}</div><h2>{current.recommendation_title}</h2></div><span className={styles.badge}>{RESPONSE_LABELS[current.response_code]||current.response_code}</span></div>
          <div className={styles.feedbackRecommendation}><small>WatchLog recommendation</small><p>{current.recommendation_body}</p></div>
          <div className={styles.feedbackClientResponse}><small>Client response</small><strong>{RESPONSE_LABELS[current.response_code]||current.response_code}</strong>{current.client_note?<p>{current.client_note}</p>:<p className="muted">No additional comment.</p>}</div>
          <div className={styles.detailGrid}>
            <div className={styles.kv}><small>Customer user</small><strong>{current.client_email}</strong></div>
            <div className={styles.kv}><small>Report date</small><strong>{current.report_date}</strong></div>
            <div className={styles.kv}><small>Response received</small><strong>{fmt(current.responded_at)}</strong></div>
          </div>
          <label>Internal follow-up note</label>
          <textarea className={styles.feedbackTeamNote} maxLength={4000} value={teamNote} onChange={e=>setTeamNote(e.target.value)} placeholder="Record what your team will do, what needs clarification, or how the client's feedback should change the recommendation."/>
          <div className={styles.feedbackStatusActions}>
            <button disabled={busy} onClick={()=>updateStatus("in_progress")}>Start follow-up</button>
            <button disabled={busy} onClick={()=>updateStatus("resolved")}>Resolve</button>
            <button className="ghost" disabled={busy} onClick={()=>updateStatus("closed")}>Close</button>
            {current.team_status!=="new"&&<button className="ghost" disabled={busy} onClick={()=>updateStatus("new")}>Move back to new</button>}
          </div>
          {current.team_note&&<div className={styles.feedbackExistingTeamNote}><small>Current internal note</small><p>{current.team_note}</p></div>}
        </>}
      </section>
    </div>
  </main></div>;
}
