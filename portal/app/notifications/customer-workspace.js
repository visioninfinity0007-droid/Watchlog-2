"use client";
import {useCallback,useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {Nav,requireTenant} from "../shell";
import {rememberSite,selectedSiteId} from "../site-context";
import s from "./notifications.module.css";

function when(ts){
  if(!ts)return "";
  try{return new Date(ts).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"})}
  catch{return String(ts)}
}
function severityLabel(v){
  const x=String(v||"info").toLowerCase();
  if(x==="critical")return "Urgent";
  if(x==="warning"||x==="attention")return "Attention";
  return "Update";
}
function kindLabel(v){
  if(v==="health")return "Monitoring";
  if(v==="report")return "Report";
  if(v==="incident")return "Security";
  return "Update";
}
function category(v){
  if(v==="health")return "monitoring";
  if(v==="report")return "reports";
  if(v==="incident")return "security";
  return "other";
}
function actionLabel(item){
  if(item.kind==="report")return "Open report";
  if(item.kind==="health")return "Check monitoring";
  if(item.kind==="incident")return "Review evidence";
  return "Open";
}

export default function CustomerNotifications(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[items,setItems]=useState([]);
  const[busy,setBusy]=useState(false);
  const[error,setError]=useState("");
  const[filter,setFilter]=useState(()=>{try{const f=new URLSearchParams(location.search).get("filter");return ["security","monitoring","reports"].includes(f)?f:"all"}catch{return "all"}});

  const load=useCallback(async(id)=>{
    setBusy(true);setError("");
    const r=await supabase().rpc("wl_notifications",{p_site_id:id||null,p_limit:100});
    setBusy(false);
    if(r.error){setError(say(r.error));return}
    setItems(r.data||[]);
  },[]);

  useEffect(()=>{(async()=>{
    const g=await requireTenant();if(!g)return;
    setEmail(g.session.user.email||"");
    const r=await supabase().rpc("wl_sites");
    if(r.error){setError(say(r.error));return}
    const list=r.data||[];setSites(list);
    const requested=new URLSearchParams(location.search).get("site")||selectedSiteId()||list[0]?.id||"";
    const id=list.some(x=>String(x.id)===String(requested))?requested:(list[0]?.id||"");
    setSiteId(id);
    if(id)rememberSite(id,list.find(x=>String(x.id)===String(id))?.name||"");
    await load(id);
  })()},[load]);

  async function choose(id){
    setSiteId(id);
    const row=sites.find(x=>String(x.id)===String(id));
    if(id)rememberSite(id,row?.name||"");
    history.replaceState(null,"",id?"/notifications/?site="+encodeURIComponent(id):"/notifications/");
    await load(id);
  }
  async function mark(item){
    if(item.read)return;
    const r=await supabase().rpc("wl_notification_mark_read",{p_source_kind:item.kind,p_source_id:String(item.id)});
    if(r.error){setError(say(r.error));return}
    setItems(x=>x.map(n=>n.kind===item.kind&&String(n.id)===String(item.id)?{...n,read:true}:n));
  }
  async function open(item){
    await mark(item);
    location.href=item.href||"/notifications/";
  }
  async function markAll(){
    const r=await supabase().rpc("wl_notifications_mark_all_read",{p_site_id:siteId||null});
    if(r.error){setError(say(r.error));return}
    setItems(x=>x.map(n=>({...n,read:true})));
  }

  const unread=useMemo(()=>items.filter(x=>!x.read),[items]);
  const urgent=useMemo(()=>unread.filter(x=>String(x.severity||"").toLowerCase()==="critical"),[unread]);
  const attention=useMemo(()=>unread.filter(x=>["critical","warning","attention"].includes(String(x.severity||"").toLowerCase())),[unread]);
  const counts=useMemo(()=>({
    all:items.length,
    security:items.filter(x=>category(x.kind)==="security").length,
    monitoring:items.filter(x=>category(x.kind)==="monitoring").length,
    reports:items.filter(x=>category(x.kind)==="reports").length
  }),[items]);
  const visible=useMemo(()=>items.filter(x=>filter==="all"||category(x.kind)===filter),[items,filter]);
  const site=sites.find(x=>String(x.id)===String(siteId));

  let heroTitle="Nothing needs your attention right now.";
  let heroCopy="No unread security or monitoring item currently requires review.";
  let heroTone="clear";
  if(urgent.length){
    heroTitle=String(urgent.length)+" urgent "+(urgent.length===1?"item needs":"items need")+" review.";
    heroCopy="Review urgent items first, then work through the remaining attention list.";
    heroTone="urgent";
  }else if(attention.length){
    heroTitle=String(attention.length)+" "+(attention.length===1?"item needs":"items need")+" your attention.";
    heroCopy="WatchLog has separated the items worth reviewing from routine updates.";
    heroTone="attention";
  }else if(unread.length){
    heroTitle=String(unread.length)+" unread "+(unread.length===1?"update":"updates")+".";
    heroCopy="These are informational updates. No unread item is currently marked urgent or attention.";
    heroTone="neutral";
  }

  return <div className="shell">
    <Nav active="Notifications" email={email} currentSiteId={siteId}/>
    <main className="main">
      <header className={s.head}>
        <div><div className={s.eyebrow}>Attention</div><h1>What needs your attention</h1><p>{site?.name||"This site"} · security, monitoring and management items worth reviewing.</p></div>
        <div className={s.actions}>
          {sites.length>1&&<select value={siteId} onChange={e=>choose(e.target.value)}>{sites.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select>}
          <button type="button" className={s.markAll} onClick={markAll} disabled={!unread.length}>Mark all read</button>
        </div>
      </header>

      {error&&<div className="err">{error}</div>}

      <section className={s.priority+" "+s[heroTone]}>
        <i/>
        <div><span>Current priority</span><h2>{heroTitle}</h2><p>{heroCopy}</p></div>
        <div className={s.priorityCounts}><div><strong>{urgent.length}</strong><small>urgent</small></div><div><strong>{attention.length}</strong><small>attention</small></div><div><strong>{unread.length}</strong><small>unread</small></div></div>
      </section>

      <div className={s.filterBar} role="tablist" aria-label="Attention categories">
        {[
          ["all","All"],
          ["security","Security"],
          ["monitoring","Monitoring"],
          ["reports","Reports"]
        ].map(([key,name])=><button key={key} type="button" role="tab" aria-selected={filter===key} className={filter===key?s.activeFilter:""} onClick={()=>{setFilter(key);const q=new URLSearchParams(location.search);if(key==="all")q.delete("filter");else q.set("filter",key);history.replaceState(null,"","/notifications/?"+q.toString())}}><span>{name}</span><b>{counts[key]||0}</b></button>)}
      </div>

      {busy?<div className={s.empty}>Checking for updates…</div>:visible.length?<div className={s.list}>
        {visible.map(item=>{
          const level=String(item.severity||"info").toLowerCase();
          return <article className={s.item+" "+(item.read?s.read:s.unread)} key={String(item.kind)+":"+String(item.id)}>
            <div className={s.itemTop}>
              <div><span className={s.kind}>{kindLabel(item.kind)}</span><span className={s.severity+" "+(s[level]||"")}>{severityLabel(item.severity)}</span><small>{item.site_name} · {when(item.created_at)}</small></div>
              {!item.read&&<span className={s.dot} aria-label="Unread"/>}
            </div>
            <div className={s.itemBody}><div><h2>{item.title}</h2><p>{item.body}</p></div><button type="button" className={s.openAction} onClick={()=>open(item)}>{actionLabel(item)}</button></div>
            {!item.read&&<button type="button" className={s.readAction} onClick={()=>mark(item)}>Mark read</button>}
          </article>
        })}
      </div>:<div className={s.empty}>{filter==="all"?"Nothing needs your attention right now.":"No "+filter+" items are available for this site."}</div>}
    </main>
  </div>;
}
