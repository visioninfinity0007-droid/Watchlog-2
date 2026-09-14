"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";
import styles from "./ai.module.css";

const STARTERS = [
  "What happened overnight?",
  "Check my cameras",
  "Configure this site",
  "Show today's incidents",
];

function ago(ts) {
  if (!ts) return "never";
  const sec = Math.max(0, Math.floor((Date.now() - Date.parse(ts)) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function human(v) {
  if (v === null || v === undefined || v === "") return "Not available";
  return String(v).replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function coverageParts(cov) {
  if (!cov || typeof cov !== "object") return [];
  const get = (key) => Number(cov?.[key] ?? cov?.seconds?.[key] ?? cov?.durations?.[key] ?? 0);
  return [
    ["Live", get("live_seconds") || get("live")],
    ["Recovered", get("recovered_seconds") || get("recovered")],
    ["Unverified", get("unverified_seconds") || get("unverified")],
  ];
}

function StatusDot({ state }) {
  const cls = state === "online" || state === "ok" || state === "healthy" ? styles.good : state === "offline" || state === "failed" ? styles.bad : styles.warn;
  return <span className={`${styles.dot} ${cls}`} />;
}

function Card({ card }) {
  const data = card?.data || {};
  if (card.type === "coverage") {
    const parts = coverageParts(data);
    const total = parts.reduce((s, [, n]) => s + Number(n || 0), 0) || 1;
    return <div className={styles.richCard}><div className={styles.richTitle}>{card.title || "Monitoring coverage"}</div>
      <div className={styles.coverageBar}>{parts.map(([label, n]) => <span key={label} className={styles[`cov${label}`]} style={{width:`${Math.max(0,(n/total)*100)}%`}} title={`${label}: ${Math.round(n/60)}m`} />)}</div>
      <div className={styles.legend}>{parts.map(([label,n])=><span key={label}><b>{label}</b> {Math.round(n/60)}m</span>)}</div>
    </div>;
  }
  if (card.type === "recorder" || card.type === "capabilities") {
    const recorder = data.recorder || data;
    return <div className={styles.richCard}><div className={styles.richTitle}>{card.title || "Recorder"}</div>
      <div className={styles.keyGrid}><div><span>Device</span><b>{[recorder.vendor,recorder.model].filter(Boolean).join(" ") || "Not identified"}</b></div><div><span>Capability profile</span><b>{data.capability_known === false ? "Unconfirmed" : "Evidence graded"}</b></div></div>
    </div>;
  }
  if (card.type === "cameras" || card.type === "health") {
    const cameras = data.cameras || [];
    return <div className={styles.richCard}><div className={styles.richTitle}>{card.title || "Site health"}</div>
      {cameras.length ? <div className={styles.cameraMiniGrid}>{cameras.slice(0,8).map((c)=><div key={c.id || c.channel} className={styles.cameraMini}><StatusDot state={c.health_state}/><div><b>{c.name || `Camera ${c.channel}`}</b><span>{c.monitor === false ? "Ignored" : human(c.recording_state || c.health_state)}</span></div></div>)}</div> : <div className={styles.muted}>No camera detail available in this response.</div>}
    </div>;
  }
  if (card.type === "setup") {
    const steps = data.steps || [];
    return <div className={styles.richCard}><div className={styles.richTitle}>{card.title || "Setup"}</div>
      <div className={styles.setupSteps}>{steps.map((s)=><div key={s.key} className={styles.setupStep}><span className={s.done?styles.stepDone:styles.stepOpen}>{s.done?"✓":steps.find((x)=>!x.done)?.key===s.key?"→":"○"}</span><span>{s.label}</span></div>)}</div>
      <a className={styles.cardAction} href="/setup/">Open guided setup</a>
    </div>;
  }
  if (card.type === "incident") {
    return <div className={styles.richCard}><div className={styles.richTitle}>{card.title || "Incident"}</div><pre className={styles.dataText}>{JSON.stringify(data,null,2)}</pre><a className={styles.cardAction} href="/incidents/">Open incidents</a></div>;
  }
  return <div className={styles.richCard}><div className={styles.richTitle}>{card.title || human(card.type)}</div><pre className={styles.dataText}>{JSON.stringify(data,null,2)}</pre></div>;
}

export default function AIWorkspace() {
  const [email,setEmail]=useState("");
  const [sites,setSites]=useState([]);
  const [siteId,setSiteId]=useState("");
  const [context,setContext]=useState(null);
  const [conversations,setConversations]=useState([]);
  const [conversationId,setConversationId]=useState("");
  const [messages,setMessages]=useState([]);
  const [draft,setDraft]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [leftOpen,setLeftOpen]=useState(false);
  const [rightOpen,setRightOpen]=useState(false);
  const endRef=useRef(null);

  const loadBase = useCallback(async()=>{
    const sb=supabase();
    const {data:{session}}=await sb.auth.getSession();
    if(!session){location.replace("/login/");return;}
    setEmail(session.user.email||"");
    const [siteRes,convRes]=await Promise.all([sb.rpc("wl_sites"),sb.rpc("wl_ai_conversations",{p_limit:40})]);
    if(siteRes.error){setError(say(siteRes.error));return;}
    if(convRes.error && !/wl_ai_conversations|schema cache|function/i.test(convRes.error.message||"")){setError(say(convRes.error));return;}
    const nextSites=siteRes.data||[];
    setSites(nextSites);
    setConversations(convRes.data||[]);
    if(!siteId && nextSites.length)setSiteId(nextSites[0].id);
  },[siteId]);

  const loadContext=useCallback(async(id)=>{
    if(!id)return;
    const {data,error}=await supabase().rpc("wl_ai_context",{p_site_id:id});
    if(error){ if(/wl_ai_context|schema cache|function/i.test(error.message||"")){setContext(null);return;} setError(say(error));return; }
    setContext(data||null);
  },[]);

  const loadMessages=useCallback(async(id)=>{
    if(!id){setMessages([]);return;}
    const {data,error}=await supabase().rpc("wl_ai_messages",{p_conversation_id:id,p_limit:100});
    if(error){setError(say(error));return;}
    setMessages((data||[]).map((m)=>({role:m.role,content:m.content,payload:m.payload||{},id:m.id})));
  },[]);

  useEffect(()=>{loadBase();},[loadBase]);
  useEffect(()=>{if(siteId)loadContext(siteId);},[siteId,loadContext]);
  useEffect(()=>{loadMessages(conversationId);},[conversationId,loadMessages]);
  useEffect(()=>{endRef.current?.scrollIntoView({behavior:"smooth"});},[messages,busy]);

  const selectedSite=sites.find((s)=>s.id===siteId);
  const monitored=(context?.cameras||[]).filter((c)=>c.monitor).length;
  const healthy=(context?.cameras||[]).filter((c)=>c.monitor&&c.health_state!=="offline").length;
  const recorder=[context?.recorder?.vendor,context?.recorder?.model].filter(Boolean).join(" ");

  async function newChat(){setConversationId("");setMessages([]);setDraft("");setLeftOpen(false);}
  async function signOut(){await supabase().auth.signOut();location.replace("/login/");}

  async function send(text=draft){
    const prompt=String(text||"").trim();
    if(!prompt||busy||!siteId)return;
    setDraft("");setError("");setBusy(true);
    const optimistic={role:"user",content:prompt,payload:{}};
    setMessages((m)=>[...m,optimistic]);
    const {data,error}=await supabase().functions.invoke("watchlog-ai",{body:{prompt,site_id:siteId,conversation_id:conversationId||null}});
    setBusy(false);
    if(error||data?.error){setError(data?.message||say(error)||"WatchLog AI could not complete that request.");return;}
    if(!conversationId&&data?.conversation_id)setConversationId(data.conversation_id);
    setMessages((m)=>[...m,{role:"assistant",content:data.answer,payload:{cards:data.cards||[],suggestions:data.suggestions||[],proposed_actions:data.proposed_actions||[],mode:data.mode}}]);
    await loadBase();
    await loadContext(siteId);
  }

  const suggestions=useMemo(()=>{
    const last=[...messages].reverse().find((m)=>m.role==="assistant");
    return last?.payload?.suggestions?.length?last.payload.suggestions:STARTERS;
  },[messages]);

  return <div className={styles.app}>
    <aside className={`${styles.sidebar} ${leftOpen?styles.open:""}`}>
      <div className={styles.brand}><Mark size={28}/><b>WatchLog</b><button className={styles.iconBtn} onClick={()=>setLeftOpen(false)} aria-label="Close sidebar">×</button></div>
      <button className={styles.newChat} onClick={newChat}><span>＋</span> New chat</button>
      <div className={styles.sideLabel}>Sites</div>
      <div className={styles.siteList}>{sites.map((s)=><button key={s.id} className={`${styles.siteBtn} ${s.id===siteId?styles.active:""}`} onClick={()=>{setSiteId(s.id);setConversationId("");setMessages([]);setLeftOpen(false);}}><span>{s.name}</span><small>{s.online?"Online":human(s.setup_state)}</small></button>)}</div>
      <div className={styles.sideLabel}>Recent</div>
      <div className={styles.chatList}>{conversations.map((c)=><button key={c.id} className={`${styles.chatBtn} ${c.id===conversationId?styles.active:""}`} onClick={()=>{setSiteId(c.site_id||siteId);setConversationId(c.id);setLeftOpen(false);}}><span>{c.title}</span><small>{c.site_name||"All context"}</small></button>)}</div>
      <div className={styles.sideBottom}><a href="/reports/">Reports</a><a href="/setup/">Setup</a><a href="/settings/">Settings</a><button onClick={signOut}>Sign out</button></div>
    </aside>

    <main className={styles.main}>
      <header className={styles.topbar}><button className={styles.mobileBtn} onClick={()=>setLeftOpen(true)}>☰</button><div><b>WatchLog AI</b><span>{selectedSite?.name||"Select a site"}</span></div><div className={styles.topActions}><span className={styles.statusChip}><StatusDot state={context?.connectivity?.agent_online?"online":"offline"}/>{context?.connectivity?.agent_online?"Monitoring":"Needs attention"}</span><button className={styles.mobileBtn} onClick={()=>setRightOpen(true)}>◫</button></div></header>
      <section className={styles.chat}>
        {messages.length===0?<div className={styles.welcome}><div className={styles.aiMark}><Mark size={42}/></div><h1>How can I help with {selectedSite?.name||"your site"}?</h1><p>Ask WatchLog what happened, check your cameras, understand your recorder or configure monitoring in plain language.</p><div className={styles.starters}>{STARTERS.map((x)=><button key={x} onClick={()=>send(x)} disabled={!siteId}>{x}<span>↗</span></button>)}</div>{!sites.length&&<a className={styles.setupCta} href="/setup/">Set up your first site</a>}</div>:<div className={styles.thread}>{messages.map((m,i)=><div key={m.id||i} className={`${styles.message} ${m.role==="user"?styles.user:styles.assistant}`}><div className={styles.avatar}>{m.role==="user"?"You":<Mark size={24}/>}</div><div className={styles.messageBody}><div className={styles.messageText}>{m.content}</div>{(m.payload?.cards||[]).map((c,j)=><Card key={j} card={c}/>)}</div></div>)}{busy&&<div className={`${styles.message} ${styles.assistant}`}><div className={styles.avatar}><Mark size={24}/></div><div className={styles.thinking}><i/><i/><i/></div></div>}<div ref={endRef}/></div>}
      </section>
      {error&&<div className={styles.error}>{error}</div>}
      <footer className={styles.composerWrap}>{messages.length>0&&<div className={styles.suggestionRow}>{suggestions.slice(0,4).map((s)=><button key={s} onClick={()=>send(s)} disabled={busy}>{s}</button>)}</div>}<form className={styles.composer} onSubmit={(e)=>{e.preventDefault();send();}}><textarea value={draft} onChange={(e)=>setDraft(e.target.value)} onKeyDown={(e)=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send();}}} placeholder={siteId?"Ask WatchLog about this site...":"Select a site to start"} disabled={!siteId||busy} rows={1}/><button disabled={!draft.trim()||busy||!siteId} aria-label="Send">↑</button></form><small>WatchLog uses verified site and recorder data. Device changes always require the appropriate approval.</small></footer>
    </main>

    <aside className={`${styles.context} ${rightOpen?styles.open:""}`}><div className={styles.contextHead}><div><span>Site context</span><b>{selectedSite?.name||"No site"}</b></div><button className={styles.iconBtn} onClick={()=>setRightOpen(false)}>×</button></div>
      {!siteId?<div className={styles.emptyContext}>Add or select a site to give WatchLog operational context.</div>:<>
        <div className={styles.contextCard}><div className={styles.contextTitle}>Monitoring</div><div className={styles.contextRow}><span>Agent</span><b><StatusDot state={context?.connectivity?.agent_online?"online":"offline"}/>{context?.connectivity?.agent_online?"Online":"Offline"}</b></div><div className={styles.contextRow}><span>Last contact</span><b>{ago(context?.connectivity?.last_seen)}</b></div></div>
        <div className={styles.contextCard}><div className={styles.contextTitle}>Camera system</div><div className={styles.contextRow}><span>Recorder</span><b>{recorder||"Not identified"}</b></div><div className={styles.contextRow}><span>Cameras</span><b>{healthy}/{monitored||0} healthy</b></div><div className={styles.contextRow}><span>Capability profile</span><b>{context?.capability_known?"Loaded":"Unconfirmed"}</b></div></div>
        <div className={styles.contextCard}><div className={styles.contextTitle}>Attention</div>{(context?.faults||[]).length?<>{(context.faults||[]).slice(0,4).map((f,i)=><div className={styles.alertRow} key={i}><StatusDot state="offline"/><div><b>{f.camera||"Camera"}</b><span>{human(f.reason)}</span></div></div>)}</>:<div className={styles.muted}>No current camera fault is reported.</div>}</div>
        <div className={styles.contextCard}><div className={styles.contextTitle}>Setup</div>{(context?.onboarding?.steps||[]).map((s)=><div className={styles.contextStep} key={s.key}><span className={s.done?styles.doneCheck:""}>{s.done?"✓":"○"}</span>{s.label}</div>)}<a className={styles.contextLink} href="/setup/">Open guided setup →</a></div>
        <div className={styles.contextLinks}><a href="/site-health/">Site health</a><a href="/incidents/">Incidents</a><a href="/control-room/">Camera view</a></div>
      </>}
      <div className={styles.userBox}><span>{email}</span></div>
    </aside>
    {(leftOpen||rightOpen)&&<button className={styles.scrim} onClick={()=>{setLeftOpen(false);setRightOpen(false);}} aria-label="Close panel"/>}
  </div>;
}
