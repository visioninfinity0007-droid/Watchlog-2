"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {rememberSite,selectedSiteId} from "../site-context";

function allowedSite(list,id){return Boolean(id)&&list.some(s=>String(s.id)===String(id))}

export default function useCustomerChat(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[ctx,setCtx]=useState(null);
  const[conversationId,setConversationId]=useState("");
  const[messages,setMessages]=useState([]);
  const[draft,setDraft]=useState("");
  const[busy,setBusy]=useState(false);
  const[booting,setBooting]=useState(true);
  const[contextLoading,setContextLoading]=useState(false);
  const[error,setError]=useState("");
  const inputRef=useRef(null);

  const loadContext=useCallback(async id=>{
    if(!id){setCtx(null);return null}
    setContextLoading(true);
    const r=await supabase().rpc("wl_ai_context",{p_site_id:id});
    setContextLoading(false);
    if(r.error){setError(say(r.error));return null}
    setCtx(r.data||null);
    return r.data||null;
  },[]);

  useEffect(()=>{let live=true;(async()=>{
    setBooting(true);setError("");
    const sb=supabase();
    const{data:{session}}=await sb.auth.getSession();
    if(!session){location.replace("/login/");return}
    if(!live)return;
    setEmail(session.user.email||"");

    const sitesResult=await sb.rpc("wl_sites");
    if(!live)return;
    if(sitesResult.error){setError(say(sitesResult.error));setBooting(false);return}

    const list=sitesResult.data||[];
    const params=new URLSearchParams(location.search);
    const requestedSite=params.get("site")||selectedSiteId()||"";
    const id=allowedSite(list,requestedSite)?requestedSite:(list[0]?.id||"");
    if(!id){setSites(list);setError("No site is available for this account yet.");setBooting(false);return}
    const requestedConversation=params.get("conversation")||"";
    const conv=requestedSite&&requestedSite!==id?"":requestedConversation;
    const prompt=params.get("prompt")||"";
    setSites(list);setSiteId(id);setConversationId(conv);
    if(prompt)setDraft(prompt);
    rememberSite(id,list.find(x=>x.id===id)?.name||"");
    if(requestedSite!==id){
      params.set("site",id);
      params.delete("conversation");
      history.replaceState(null,"",`${location.pathname}?${params.toString()}${location.hash||""}`);
    }

    const contextPromise=sb.rpc("wl_ai_context",{p_site_id:id});
    const messagesPromise=conv?sb.rpc("wl_ai_messages",{p_conversation_id:conv,p_limit:100}):Promise.resolve({data:[],error:null});
    const[contextResult,messageResult]=await Promise.all([contextPromise,messagesPromise]);
    if(!live)return;

    if(contextResult.error)setError(say(contextResult.error));
    else setCtx(contextResult.data||null);
    if(messageResult.error)setError(say(messageResult.error));
    else setMessages((messageResult.data||[]).map(m=>({role:m.role,content:m.content,payload:m.payload||{},id:m.id})));
    setBooting(false);
  })().catch(e=>{if(live){setError(say(e)||"WatchLog could not load this workspace.");setBooting(false)}});return()=>{live=false}},[]);

  useEffect(()=>{
    const el=inputRef.current;if(!el)return;
    el.style.height="0";
    el.style.height=`${Math.max(38,Math.min(el.scrollHeight,150))}px`;
    el.style.overflowY=el.scrollHeight>150?"auto":"hidden";
  },[draft]);

  async function send(text=draft){
    const prompt=String(text||"").trim();
    if(!prompt||busy||booting||!siteId)return;
    setDraft("");setError("");setBusy(true);
    setMessages(m=>[...m,{role:"user",content:prompt,payload:{},id:`local-${Date.now()}`}]);
    const r=await supabase().functions.invoke("watchlog-ai",{body:{prompt,site_id:siteId,conversation_id:conversationId||null}});
    setBusy(false);
    if(r.error||r.data?.error){setError(r.data?.message||say(r.error)||"WatchLog could not complete that request.");return}
    const id=r.data.conversation_id||conversationId;
    if(id&&!conversationId){
      setConversationId(id);
      history.replaceState(null,"",`/ai/?site=${encodeURIComponent(siteId)}&conversation=${encodeURIComponent(id)}`);
    }
    setMessages(m=>[...m,{role:"assistant",content:r.data.answer,payload:r.data||{},id:`reply-${Date.now()}`}]);
    loadContext(siteId);
  }

  return{
    email,sites,siteId,site:sites.find(s=>s.id===siteId),ctx,messages,draft,setDraft,
    busy,booting,ready:!booting,contextLoading,error,inputRef,send
  };
}
