"use client";
// WatchLog owner portal - shared Signal Ledger components.
// Every owner surface composes these; page files should not restyle them. Styling lives in owner.css.
import {Nav} from "../shell";
import {withSite} from "../site-context";

export function ratioPct(v){
  if(v===null||v===undefined||v==="")return null;
  const n=Number(v);
  return Number.isFinite(n)?Math.round(Math.max(0,Math.min(1,n))*100):null;
}
export function num(v){
  if(v===null||v===undefined||v==="")return null;
  const n=Number(v);
  return Number.isFinite(n)?n:null;
}
export function fmt(v){const n=num(v);return n===null?"—":n.toLocaleString("en-PK")}
export function tone(sev){
  const s=String(sev||"").toLowerCase();
  if(s==="critical"||s==="high"||s==="bad"||s==="fault")return "bad";
  if(s==="warning"||s==="attention"||s==="warn"||s==="medium")return "warn";
  if(s==="ok"||s==="good"||s==="none"||s==="clear"||s==="healthy")return "ok";
  if(s==="info")return "info";
  return "unknown";
}

export function OwnerPage({active,email,siteId,kicker,title,actions,rail,summary,children,label}){
  return <div className="ow">
    <Nav active={active} email={email} currentSiteId={siteId}/>
    <main className="ow-main" aria-label={label||title}>
      <header className="ow-head">
        <div className="ow-head-text">
          {kicker&&kicker.length>0&&<div className="ow-kicker">{kicker.filter(Boolean).map((k,i)=><span key={i}>{k}</span>)}</div>}
          <h1>{title}</h1>
        </div>
        {actions&&<div className="ow-head-actions">{actions}</div>}
      </header>
      {summary}
      <div className={"ow-body"+(rail?" has-rail":"")}>
        <div className="ow-work">{children}</div>
        {rail&&<aside className="ow-rail" aria-label="Site intelligence">{rail}</aside>}
      </div>
    </main>
  </div>;
}

export function SiteSelect({sites,value,onChange}){
  if(!sites||sites.length<2)return null;
  return <select aria-label="Choose site" value={value} onChange={e=>onChange(e.target.value)}>{sites.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select>;
}

export function Lead({tone:t="unknown",title,body,action}){
  return <section className="ow-lead" aria-live="polite">
    <i className={"ow-lead-mark "+t} aria-hidden="true"/>
    <div><h2>{title}</h2>{body&&<p>{body}</p>}</div>
    {action||null}
  </section>;
}

export function Section({title,count,note,action,first,children,id,className=""}){
  return <section className={"ow-sec"+(first?" first":"")+(className?" "+className:"")} id={id} aria-label={typeof title==="string"?title:undefined}>
    {(title||action)&&<div className="ow-sec-head">
      <div><h2>{title}{count!==undefined&&count!==null&&<em>{count}</em>}</h2>{note&&<p>{note}</p>}</div>
      {action||null}
    </div>}
    {children}
  </section>;
}

export function Row({tone:t="unknown",title,body,meta,action,compact}){
  const m=(meta||[]).filter(Boolean);
  return <article className={"ow-row"+(compact?" compact":"")}>
    <i className={"ow-row-tick "+t} aria-hidden="true"/>
    <div className="ow-row-main">
      <h3>{title}</h3>
      {body&&<p>{body}</p>}
      {m.length>0&&<div className="ow-row-meta">{m.map((x,i)=><span key={i}>{x}</span>)}</div>}
    </div>
    {action&&<div className="ow-row-act">{action}</div>}
  </article>;
}

export function Metrics({items}){
  return <div className="ow-metrics">{items.map((m,i)=>{
    const unknown=m.value===null||m.value===undefined||m.value==="";
    return <div className={"ow-metric"+(m.primary?" primary":"")} key={(m.label||"")+i}>
      <b className={unknown?"unknown":""}>{unknown?(m.unknown||"Not available"):m.value}</b>
      <span>{m.label}</span>
      {m.note&&<small>{m.note}</small>}
    </div>;
  })}</div>;
}

export function Status({tone:t="unknown",children}){return <span className={"ow-status "+t}>{children}</span>}

// Coverage ledger: the Signal Ledger coverage component ("23h 41m verified · 19 min could not be verified").
// Durations come from the governed coverage classes (live / recovered / unverified seconds) when present;
// otherwise only the verified share is known. Either way the strip is a proportion (verified first, then
// could-not-be-verified) - never a time axis that would imply WHEN coverage was missing.
export function duration(sec){
  const m=Math.round(Math.max(0,Number(sec)||0)/60);
  if(m<60)return m+" min";
  const h=Math.floor(m/60),r=m%60;
  return h+"h "+String(r).padStart(2,"0")+"m";
}
function coverageClasses(classes){
  if(!classes||typeof classes!=="object")return null;
  const c=classes.classes||classes;
  const live=num(c.live_seconds??c.live),rec=num(c.recovered_seconds??c.recovered),unv=num(c.unverified_seconds??c.unverified);
  if(live===null&&rec===null&&unv===null)return null;
  const L=live||0,R=rec||0,U=unv||0,T=L+R+U;
  return T>0?{live:L,rec:R,unv:U,total:T}:null;
}
// One coverage truth for a window, so every statement on a page agrees. Classes (live / recovered /
// unverified seconds) win over the ratio; any unverified time means "partial" and the verified share is
// never rounded up to 100%. fullyVerified is true only when the governed data says nothing is unverified.
export function coverageTruth(coverage){
  const empty={known:false,partial:false,fullyVerified:false,pct:null,unverifiedSeconds:null,recoveredSeconds:null,verifiedSeconds:null};
  if(!coverage||typeof coverage!=="object")return empty;
  const k=coverageClasses(coverage.classes||coverage);
  const ratio=num(coverage.coverage_ratio);
  if(k){
    const share=(k.live+k.rec)/k.total;
    const pct=k.unv>0?Math.min(99,Math.round(share*100)):100;
    return{known:true,partial:k.unv>0,fullyVerified:k.unv===0,pct,unverifiedSeconds:k.unv,recoveredSeconds:k.rec,verifiedSeconds:k.live+k.rec};
  }
  if(ratio===null)return empty;
  const r=Math.max(0,Math.min(1,ratio));
  const unv=num(coverage.unverified_seconds);
  const partial=r<1||(unv!==null&&unv>0);
  return{known:true,partial,fullyVerified:!partial,pct:partial?Math.min(99,Math.round(r*100)):100,unverifiedSeconds:unv!==null&&unv>0?unv:null,recoveredSeconds:null,verifiedSeconds:null};
}
export function coverageText(ratio,classes){
  const k=coverageClasses(classes);
  if(k)return duration(k.live+k.rec)+" verified"+(k.unv>0?" · "+duration(k.unv)+" could not be verified":"");
  const p=ratioPct(ratio);
  if(p===null)return "Not verified yet";
  return p+"% verified"+(p<100?" · "+(100-p)+"% could not be verified":"");
}
export function Ledger({ratio,label,recovered,classes}){
  const k=coverageClasses(classes);
  const p=k?(k.unv>0?Math.min(99,Math.round(((k.live+k.rec)/k.total)*100)):100):ratioPct(ratio);
  if(p===null)return <div className="ow-ledger">
    <div className="ow-ledger-strip none" role="img" aria-label="Monitoring coverage not verified yet"/>
    {label!==false&&<div className="ow-ledger-key"><span className="unk">Not verified yet</span></div>}
  </div>;
  const liveW=k?(k.live/k.total)*100:(ratioPct(recovered)!==null?Math.max(0,p-ratioPct(recovered)):p);
  const recW=k?(k.rec/k.total)*100:(ratioPct(recovered)||0);
  const text=coverageText(ratio,classes);
  return <div className="ow-ledger">
    <div className="ow-ledger-strip" role="img" aria-label={text}>
      <i style={{width:liveW+"%"}}/>{recW>0&&<i className="recovered" style={{width:recW+"%"}}/>}
    </div>
    {label!==false&&<div className="ow-ledger-key">
      <span>{k?duration(k.live+k.rec):p+"%"} verified{k&&k.rec>0?" (incl. "+duration(k.rec)+" recovered)":""}</span>
      {(k?k.unv>0:p<100)&&<span className="unk">{k?duration(k.unv):(100-p)+"%"} could not be verified</span>}
    </div>}
  </div>;
}

export function RailSection({label,action,children}){
  return <section className="ow-rail-sec">
    {(label||action)&&<div className="ow-rail-head"><h2>{label}</h2>{action||null}</div>}
    {children}
  </section>;
}
export function Stat({label,note,value,muted}){
  const empty=value===null||value===undefined||value==="";
  return <div className="ow-stat"><span>{label}{note&&<small>{note}</small>}</span><b className={muted||empty?"muted":""}>{empty?"Not available":value}</b></div>;
}
export function Figure({value,unit}){
  return <div className="ow-figure"><b>{value}</b>{unit&&<span>{unit}</span>}</div>;
}

export function Summary({items}){
  const list=(items||[]).filter(Boolean);
  if(!list.length)return null;
  return <div className="ow-summary" aria-label="Owner summary">{list.map((x,i)=><div key={i}>
    <b className={x.muted?"muted":""}>{x.value}</b><span>{x.label}</span>
    {x.ledger!==undefined&&<Ledger ratio={x.ledger} classes={x.classes} label={false}/>}
  </div>)}</div>;
}

export function Bars({series,question,legend,showValues=true,height}){
  const vals=series.filter(s=>!s.gap).map(s=>Number(s.value||0));
  const max=Math.max(1,...vals);
  return <figure className="ow-chart" style={{margin:0}}>
    {question&&<figcaption className="ow-chart-q">{question}</figcaption>}
    <div className="ow-bars" style={height?{height}:undefined} role="img" aria-label={(question||"Chart")+": "+series.map(s=>s.label+" "+(s.gap?"not verified":s.value)).join(", ")}>
      {series.map((s,i)=><div key={i} title={s.title||(s.label+": "+(s.gap?"not verified":s.value))}>
        {showValues&&!s.gap&&s.showValue!==false&&Number(s.value)>0&&<span className="ow-bar-val" style={{bottom:Math.max(2,(Number(s.value)/max)*100)+"%"}}>{s.display??s.value}</span>}
        <i className={"ow-bar"+(s.gap?" gap":s.prev?" prev":s.peak?" peak":"")} style={s.gap?undefined:{height:Math.max(1.5,(Number(s.value||0)/max)*100)+"%"}}/>
      </div>)}
    </div>
    <div className="ow-bars-labels" aria-hidden="true">{series.map((s,i)=><span key={i}>{s.label}</span>)}</div>
    {legend&&<div className="ow-legend">{legend}</div>}
  </figure>;
}

export function HBars({items,question}){
  const max=Math.max(1,...items.map(x=>Number(x.value||0)));
  return <figure className="ow-chart" style={{margin:0}}>
    {question&&<figcaption className="ow-chart-q">{question}</figcaption>}
    <div className="ow-hbars">{items.map((x,i)=><div className="ow-hbar" key={x.key||i}>
      <span>{x.label}{x.note&&<small>{x.note}</small>}</span>
      <i aria-hidden="true"><i style={{width:Math.max(2,(Number(x.value||0)/max)*100)+"%"}}/></i>
      <b>{fmt(x.value)}</b>
    </div>)}</div>
  </figure>;
}

// Current vs previous governed period. A row is drawn only when both values exist.
export function Compare({rows}){
  return <div className="ow-compare">{rows.map((r,i)=>{
    const max=Math.max(1,Number(r.current||0),Number(r.previous||0));
    return <div className="ow-compare-row" key={r.label||i}>
      <span>{r.label}{r.note&&<small>{r.note}</small>}</span>
      <div className="ow-compare-bars" aria-hidden="true">
        <i style={{width:Math.max(2,(Number(r.current||0)/max)*100)+"%"}}/>
        <i className="prev" style={{width:Math.max(2,(Number(r.previous||0)/max)*100)+"%"}}/>
      </div>
      <Delta value={r.delta} suffix={r.suffix} label={r.deltaLabel}/>
    </div>;
  })}</div>;
}

export function Delta({value,suffix="",label}){
  const n=num(value);
  if(n===null)return <span className="ow-delta na">{label||"No comparison"}</span>;
  const dir=n>0?"up":n<0?"down":"flat";
  const v=Math.abs(n);
  return <span className={"ow-delta "+dir}>{label||((Number.isInteger(v)?v:v.toFixed(1))+suffix)}</span>;
}

export function Findings({items}){
  return <div className="ow-findings">{items.map((x,i)=><div className="ow-finding" key={i}><div><h3>{x.title}</h3>{x.body&&<p>{x.body}</p>}</div></div>)}</div>;
}

export function Empty({title,children,action}){
  return <div className="ow-empty" role="status"><h3>{title}</h3>{children&&<div>{children}</div>}{action||null}</div>;
}
export function Loading({label="Loading"}){
  return <div className="ow-loading" role="status" aria-label={label}><i/><i/><i/></div>;
}
export function Notice({tone:t="",children,role}){
  return <div className={"ow-note "+t} role={role||(t==="bad"?"alert":"status")}>{children}</div>;
}

export function Tabs({items,value,onChange,label,hrefFor}){
  return <div className="ow-tabs" role="tablist" aria-label={label}>{items.map(([key,text,count])=>hrefFor
    ?<a key={key} role="tab" aria-selected={value===key} className="ow-tab" href={hrefFor(key)}>{text}{count!==undefined&&<em>{count}</em>}</a>
    :<button key={key} type="button" role="tab" aria-selected={value===key} className="ow-tab" onClick={()=>onChange(key)}>{text}{count!==undefined&&<em>{count}</em>}</button>)}</div>;
}

// A slim, secondary Ask WatchLog entry (question first). It opens Ask WatchLog with the question ready.
export function AskBar({siteId,placeholder="Ask WatchLog about this site…"}){
  function submit(e){
    e.preventDefault();
    const q=String(new FormData(e.currentTarget).get("q")||"").trim();
    location.href=withSite("/ai/"+(q?"?prompt="+encodeURIComponent(q):""),siteId);
  }
  return <form className="ow-askbar" role="search" onSubmit={submit}>
    <input name="q" type="search" aria-label="Ask WatchLog" placeholder={placeholder} autoComplete="off"/>
    <button type="submit" aria-label="Ask WatchLog">Ask</button>
  </form>;
}

export function AskLinks({siteId,prompts}){
  return <div className="ow-ask">{prompts.map(p=><a key={p} href={withSite("/ai/?prompt="+encodeURIComponent(p),siteId)}>{p}</a>)}</div>;
}

// Events on a horizontal axis (the Signal Ledger motif). Positions are real timestamps within the window.
export function Timeline({items,from,to,ticks}){
  const a=new Date(from).getTime(),b=new Date(to).getTime(),span=Math.max(1,b-a);
  const place=t=>Math.max(0,Math.min(100,((new Date(t).getTime()-a)/span)*100));
  return <div className="ow-timeline" role="img" aria-label={items.length+" item"+(items.length===1?"":"s")+" on the timeline"}>
    <div className="ow-timeline-axis"/>
    {(ticks||[]).map((k,i)=><div className="ow-timeline-tick" key={i} style={{left:place(k.at)+"%"}}><span>{k.label}</span></div>)}
    {items.map((x,i)=><a key={i} href={x.href} className={"ow-timeline-mark "+(x.tone||"info")} style={{left:place(x.at)+"%"}} title={x.label} aria-label={x.label}/>)}
  </div>;
}
