"use client";
import {useCallback,useEffect,useState} from "react";
import {supabase,say} from "../../../../lib/supabase";
import {AdminNav,requirePlatformAdmin} from "../../admin-shell";
import styles from "../../admin.module.css";

const fmt=v=>v?new Date(v).toLocaleString():"—";

function AdminAttachment({attachment}){
  const[url,setUrl]=useState("");
  useEffect(()=>{
    let live=true;
    (async()=>{
      const r=await supabase().functions.invoke("watchlog-ai",{body:{op:"attachment_url",attachment_id:attachment.id}});
      if(live&&!r.error&&r.data?.url)setUrl(r.data.url);
    })();
    return()=>{live=false};
  },[attachment.id]);
  return <div style={{width:180,border:"1px solid var(--color-line-dark)",borderRadius:10,overflow:"hidden"}}>
    {url?<img src={url} alt={attachment.name||"Customer attachment"} style={{display:"block",width:"100%",height:120,objectFit:"cover"}}/>:
      <div style={{height:120,display:"grid",placeItems:"center"}} className="muted">Loading image…</div>}
    <div style={{padding:7,fontSize:11}}>{attachment.name||"Image"}</div>
  </div>;
}

export default function AiConversations(){
  const[admin,setAdmin]=useState(null);
  const[items,setItems]=useState([]);
  const[total,setTotal]=useState(0);
  const[selected,setSelected]=useState(null);
  const[search,setSearch]=useState("");
  const[error,setError]=useState("");
  const[loading,setLoading]=useState(true);

  const load=useCallback(async(q="")=>{
    setLoading(true);setError("");
    const r=await supabase().rpc("wl_platform_ai_conversations",{
      p_tenant_id:null,p_search:q.trim()||null,p_limit:100,p_offset:0
    });
    if(r.error)setError(say(r.error));
    else{setItems(r.data?.items||[]);setTotal(Number(r.data?.total||0))}
    setLoading(false);
  },[]);

  const openConversation=useCallback(async id=>{
    setError("");
    const r=await supabase().rpc("wl_platform_ai_conversation",{p_conversation_id:id});
    if(r.error){setError(say(r.error));return}
    setSelected(r.data||null);
    history.replaceState(null,"","/admin/ai/conversations/?conversation="+encodeURIComponent(id));
  },[]);

  useEffect(()=>{(async()=>{
    const guard=await requirePlatformAdmin();if(!guard)return;
    setAdmin(guard.admin);
    await load("");
    const id=new URLSearchParams(location.search).get("conversation");
    if(id)await openConversation(id);
  })()},[load,openConversation]);

  function doSearch(e){e.preventDefault();load(search)}
  const conv=selected?.conversation;
  return <div className="shell"><AdminNav active="AI Conversations" admin={admin}/><main className="main">
    <div className={styles.head}><div><div className={styles.role}>WatchLog AI</div><h1>Customer conversations</h1>
      <p>Read-only support and quality view across customer AI conversations, including attached images and internal route diagnostics.</p></div>
      {selected&&<button className="ghost" onClick={()=>{setSelected(null);history.replaceState(null,"","/admin/ai/conversations/")}}>Back to conversations</button>}
    </div>
    {error&&<div className="err">{error}</div>}
    {!selected?<>
      <section className={styles.card} style={{marginBottom:18}}>
        <form className={styles.toolbar} onSubmit={doSearch}>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search customer, site, email or message"/>
          <button type="submit">Search</button><span className="muted">{total} conversations</span>
        </form>
      </section>
      <section className={styles.card}>
        {loading?<div className={styles.empty}>Loading conversations…</div>:<div className={styles.stack}>
          {items.map(c=><button key={c.id} type="button" className={styles.row} onClick={()=>openConversation(c.id)} style={{width:"100%",textAlign:"left"}}>
            <div style={{minWidth:0}}><strong>{c.tenant_name+" · "+(c.site_name||"No site")}</strong>
              <small>{(c.user_email||"Unknown user")+" · "+fmt(c.updated_at)}</small>
              <small style={{maxWidth:760,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{c.last_user_message||c.title}</small>
            </div>
            <div style={{textAlign:"right",flex:"0 0 auto"}}><strong>{c.message_count} messages</strong><small>{c.attachment_count||0} images</small></div>
          </button>)}
          {!items.length&&<div className={styles.empty}>No conversations match this search.</div>}
        </div>}
      </section>
    </>:<>
      <section className={styles.card} style={{marginBottom:18}}>
        <div className={styles.detailGrid}>
          <div className={styles.kv}><small>Customer</small><strong>{conv?.tenant_name||"—"}</strong></div>
          <div className={styles.kv}><small>Site</small><strong>{conv?.site_name||"—"}</strong></div>
          <div className={styles.kv}><small>User</small><strong>{conv?.user_email||"—"}</strong></div>
        </div>
        <h2 style={{marginBottom:6}}>{conv?.title||"Conversation"}</h2><p className="muted" style={{marginTop:0}}>Updated {fmt(conv?.updated_at)}</p>
      </section>
      <div className={styles.twoCol}>
        <section className={styles.card}><h2>Conversation</h2><div className={styles.stack}>
          {(selected.messages||[]).map(m=><div key={m.id} style={{border:"1px solid var(--color-line-dark)",borderRadius:12,padding:14,background:"var(--color-canvas)"}}>
            <div className={styles.actions} style={{justifyContent:"space-between"}}><span className={styles.badge}>{m.role}</span><small className="muted">{fmt(m.created_at)}</small></div>
            <div style={{whiteSpace:"pre-wrap",lineHeight:1.6,marginTop:10}}>{m.content}</div>
            {!!m.attachments?.length&&<div style={{display:"flex",gap:9,flexWrap:"wrap",marginTop:12}}>{m.attachments.map(a=><AdminAttachment attachment={a} key={a.id}/>)}</div>}
          </div>)}
        </div></section>
        <section className={styles.card}><h2>Route diagnostics</h2><div className={styles.stack}>
          {(selected.routes||[]).map(r=><div className={styles.kv} key={r.id}>
            <small>{fmt(r.created_at)}</small><strong>{(r.mode||"—")+" · "+(r.route||"—")}</strong>
            <div className="muted" style={{fontSize:12,marginTop:7}}>{(r.provider_name||"No provider")+" · "+(r.model||"—")}</div>
            <div className="muted" style={{fontSize:12,marginTop:3}}>{(r.outcome||"—")+" · "+(r.latency_ms||0)+" ms · egress "+(r.egress||"n/a")}</div>
            {!!r.tool_calls?.length&&<div className="muted" style={{fontSize:11,marginTop:5}}>Context: {r.tool_calls.join(", ")}</div>}
          </div>)}
          {!(selected.routes||[]).length&&<div className={styles.empty}>No route diagnostics recorded.</div>}
        </div></section>
      </div>
    </>}
  </main></div>;
}
