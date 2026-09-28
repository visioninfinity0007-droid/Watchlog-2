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
  if(x==="critical")return "Critical";
  if(x==="warning"||x==="attention")return "Attention";
  return "Update";
}

export default function CustomerNotifications(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[items,setItems]=useState([]);
  const[busy,setBusy]=useState(false);
  const[error,setError]=useState("");

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
    if(id)rememberSite(id,list.find(x=>x.id===id)?.name||"");
    await load(id);
  })()},[load]);

  async function choose(id){
    setSiteId(id);
    const row=sites.find(x=>x.id===id);
    if(id)rememberSite(id,row?.name||"");
    history.replaceState(null,"",id?`/notifications/?site=${encodeURIComponent(id)}`:"/notifications/");
    await load(id);
  }
  async function mark(item){
    if(item.read)return;
    const r=await supabase().rpc("wl_notification_mark_read",{
      p_source_kind:item.kind,p_source_id:String(item.id)
    });
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

  const unread=useMemo(()=>items.filter(x=>!x.read).length,[items]);
  return <div className="shell">
    <Nav active="Notifications" email={email} currentSiteId={siteId}/>
    <main className="main">
      <header className={s.head}>
        <div><div className={s.eyebrow}>Notifications</div><h1>What needs your attention</h1><p>Incidents, site-health issues, reports and report-backed recommended actions from your WatchLog sites.</p></div>
        <div className={s.actions}>
          {sites.length>1&&<select value={siteId} onChange={e=>choose(e.target.value)}>{sites.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select>}
          <button type="button" onClick={markAll} disabled={!unread}>Mark all read</button>
        </div>
      </header>
      {error&&<div className="err">{error}</div>}
      <div className={s.summary}><strong>{unread}</strong><span>{unread===1?"unread item":"unread items"}</span></div>
      {busy?<div className={s.empty}>Checking for updates…</div>:items.length?<div className={s.list}>
        {items.map(item=><article className={`${s.item} ${item.read?s.read:s.unread}`} key={`${item.kind}:${item.id}`}>
          <div className={s.itemTop}>
            <div><span className={`${s.severity} ${s[String(item.severity||"info").toLowerCase()]||""}`}>{severityLabel(item.severity)}</span><small>{item.site_name} · {when(item.created_at)}</small></div>
            {!item.read&&<span className={s.dot} aria-label="Unread"/>}
          </div>
          <h2>{item.title}</h2>
          <p>{item.body}</p>
          <div className={s.itemActions}>
            {!item.read&&<button type="button" className={s.secondary} onClick={()=>mark(item)}>Mark read</button>}
            <button type="button" onClick={()=>open(item)}>{item.kind==="report"?"Open report":item.kind==="health"?"Check site":"View incident"}</button>
          </div>
        </article>)}
      </div>:<div className={s.empty}>Nothing needs your attention right now.</div>}
    </main>
  </div>;
}
