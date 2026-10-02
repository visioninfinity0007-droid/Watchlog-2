"use client";
import {useCallback,useEffect,useMemo,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {requireTenant} from "../shell";
import {rememberSite,selectedSiteId,withSite} from "../site-context";
import {OwnerPage,SiteSelect,Lead,Section,Row,RailSection,Stat,Summary,Empty,Loading,Notice,Timeline} from "../owner/ui";

function when(ts,timeZone){
  if(!ts)return "";
  try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(ts))+" · site time"}
  catch{return String(ts)}
}
function hourLabel(ts,timeZone){try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",hour:"numeric",hour12:true}).format(new Date(ts))}catch{return ""}}
function level(v){return String(v||"info").toLowerCase()}
function severityLabel(v){
  const x=level(v);
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
function rowTone(item){const x=level(item.severity);return x==="critical"?"bad":x==="warning"||x==="attention"?"warn":item.kind==="report"?"verified":"info"}
const isAttention=x=>["critical","warning","attention"].includes(level(x.severity));
const rank=x=>level(x.severity)==="critical"?0:isAttention(x)?1:2;

export default function CustomerNotifications(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[items,setItems]=useState([]);
  const[busy,setBusy]=useState(true);
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
    if(r.error){setError(say(r.error));setBusy(false);return}
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
  function pick(key){
    setFilter(key);
    const q=new URLSearchParams(location.search);
    if(key==="all")q.delete("filter");else q.set("filter",key);
    history.replaceState(null,"","/notifications/?"+q.toString());
  }

  const unread=useMemo(()=>items.filter(x=>!x.read),[items]);
  const urgent=useMemo(()=>unread.filter(x=>level(x.severity)==="critical"),[unread]);
  const attention=useMemo(()=>unread.filter(isAttention),[unread]);
  const counts=useMemo(()=>({
    all:unread.length,
    security:unread.filter(x=>category(x.kind)==="security").length,
    monitoring:unread.filter(x=>category(x.kind)==="monitoring").length,
    reports:unread.filter(x=>category(x.kind)==="reports").length
  }),[unread]);
  const inFilter=x=>filter==="all"||category(x.kind)===filter;
  const queue=useMemo(()=>unread.filter(x=>isAttention(x)&&inFilter(x)).sort((a,b)=>rank(a)-rank(b)||new Date(b.created_at)-new Date(a.created_at)),[unread,filter]);
  const updates=useMemo(()=>unread.filter(x=>!isAttention(x)&&inFilter(x)),[unread,filter]);
  const earlier=useMemo(()=>items.filter(x=>x.read&&inFilter(x)),[items,filter]);
  const site=sites.find(x=>String(x.id)===String(siteId));
  const tz=site?.timezone;

  let leadTitle="Nothing needs your attention right now.";
  let leadBody="No unread security or monitoring item requires review.";
  let leadTone="ok";
  if(urgent.length){
    leadTitle=urgent.length+" urgent "+(urgent.length===1?"item needs":"items need")+" review";
    leadBody="Start with "+(urgent[0].title||"the urgent item")+".";
    leadTone="bad";
  }else if(attention.length){
    leadTitle=attention.length+" "+(attention.length===1?"item needs":"items need")+" your attention";
    leadBody="Start with "+([...attention].sort((a,b)=>new Date(b.created_at)-new Date(a.created_at))[0].title||"the latest item")+".";
    leadTone="warn";
  }else if(unread.length){
    leadTitle=unread.length+" unread "+(unread.length===1?"update":"updates");
    leadBody="Informational only. Nothing is marked urgent or attention.";
    leadTone="unknown";
  }

  const now=new Date();
  const dayAgo=new Date(now.getTime()-24*3600*1000);
  const recent=unread.filter(x=>x.created_at&&new Date(x.created_at)>=dayAgo);
  const ticks=[0,6,12,18,24].map(h=>{const at=new Date(dayAgo.getTime()+h*3600*1000);return{at,label:h===24?"Now":hourLabel(at,tz)}});

  function ItemRow({item}){
    return <Row tone={rowTone(item)} title={item.title} body={item.body}
      meta={[kindLabel(item.kind)+" · "+severityLabel(item.severity),sites.length>1?item.site_name:null,when(item.created_at,tz)]}
      action={<><button type="button" className="ow-btn small quiet" onClick={()=>open(item)}>{actionLabel(item)}</button>{!item.read&&<button type="button" className="ow-linkbtn" onClick={()=>mark(item)}>Mark read</button>}</>}/>;
  }

  const rail=busy?null:<>
    <RailSection label="Priority">
      <Stat label="Urgent" value={String(urgent.length)}/>
      <Stat label="Needs attention" value={String(attention.length)}/>
      <Stat label="Unread updates" value={String(unread.length-attention.length)}/>
    </RailSection>
    <RailSection label="By category">
      <Stat label="Security" value={String(counts.security)}/>
      <Stat label="Monitoring" value={String(counts.monitoring)}/>
      <Stat label="Reports" value={String(counts.reports)}/>
    </RailSection>
    <RailSection label="Ask WatchLog">
      <div className="ow-ask">
        <a href={withSite("/ai/?prompt="+encodeURIComponent("What needs my attention right now, and what should I do first?"),siteId)}>What should I do first?</a>
        <a href={withSite("/ai/?prompt="+encodeURIComponent("Is anything in my attention list connected, or a repeat of an earlier issue?"),siteId)}>Are these items connected?</a>
      </div>
    </RailSection>
  </>;

  return <OwnerPage active="Notifications" email={email} siteId={siteId}
    kicker={["Attention",site?.name]}
    title="What needs your attention"
    actions={<><SiteSelect sites={sites} value={siteId} onChange={choose}/><button type="button" className="ow-btn quiet" onClick={markAll} disabled={!unread.length}>Mark all read</button></>}
    rail={rail}
    summary={busy?null:<Summary items={[{value:String(urgent.length),label:"Urgent"},{value:String(attention.length),label:"Needs attention"}]}/>}>
    {error&&<Notice tone="bad">{error}</Notice>}
    {busy?<Loading label="Checking for updates"/>:<>
      <Lead tone={leadTone} title={leadTitle} body={leadBody}/>
      <div className="ow-tabs" role="tablist" aria-label="Attention categories">
        {[["all","All"],["security","Security"],["monitoring","Monitoring"],["reports","Reports"]].map(([key,name])=><button key={key} type="button" role="tab" aria-selected={filter===key} className="ow-tab" onClick={()=>pick(key)}>{name}<em>{counts[key]||0}</em></button>)}
      </div>
      {filter==="all"&&recent.length>0&&<div style={{marginBottom:6}}><div className="ow-label">Last 24 hours</div><Timeline from={dayAgo} to={now} ticks={ticks} items={recent.map(x=>({at:x.created_at,tone:rowTone(x)==="bad"?"bad":rowTone(x)==="warn"?"warn":"info",label:(x.title||"Item")+" · "+when(x.created_at,tz),href:x.href||"#"}))}/></div>}

      <Section first title="Needs review" count={queue.length||null}>
        {queue.length?<div className="ow-rows">{queue.map(item=><ItemRow key={String(item.kind)+":"+String(item.id)} item={item}/>)}</div>
          :<Empty title={filter==="all"?"Nothing needs your attention right now.":"No "+filter+" item needs review."}>Anything WatchLog cannot verify stays marked as not verified.</Empty>}
      </Section>

      {updates.length>0&&<Section title="Updates" count={updates.length}>
        <div className="ow-rows">{updates.map(item=><ItemRow key={String(item.kind)+":"+String(item.id)} item={item}/>)}</div>
      </Section>}

      {earlier.length>0&&<details className="ow-details" style={{marginTop:18}}><summary>Earlier · {earlier.length} read</summary><div className="ow-rows">{earlier.slice(0,30).map(item=><ItemRow key={String(item.kind)+":"+String(item.id)} item={item}/>)}</div></details>}
    </>}
  </OwnerPage>;
}
