"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {supabase,say} from "../../lib/supabase";
import {rememberSite,selectedSiteId} from "../site-context";

const IMAGE_TYPES=new Set(["image/jpeg","image/png","image/webp"]);
const MAX_IMAGES=3;
const MAX_IMAGE_BYTES=4*1024*1024;

function readImage(file){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve(String(reader.result||""));
    reader.onerror=()=>reject(new Error("Could not read that image."));
    reader.readAsDataURL(file);
  });
}

export default function useCustomerChat(){
  const[email,setEmail]=useState("");
  const[sites,setSites]=useState([]);
  const[siteId,setSiteId]=useState("");
  const[ctx,setCtx]=useState(null);
  const[conversationId,setConversationId]=useState("");
  const[messages,setMessages]=useState([]);
  const[draft,setDraft]=useState("");
  const[attachments,setAttachments]=useState([]);
  const[busy,setBusy]=useState(false);
  const[error,setError]=useState("");
  const inputRef=useRef(null);

  const loadContext=useCallback(async id=>{
    if(!id)return;
    const r=await supabase().rpc("wl_ai_context",{p_site_id:id});
    if(r.error){setError(say(r.error));return}
    setCtx(r.data||null);
  },[]);

  useEffect(()=>{(async()=>{
    const sb=supabase();
    const sessionResult=await sb.auth.getSession();
    const session=sessionResult.data.session;
    if(!session){location.replace("/login/");return}
    setEmail(session.user.email||"");
    const r=await sb.rpc("wl_sites");
    if(r.error){setError(say(r.error));return}
    const list=r.data||[];
    setSites(list);
    const p=new URLSearchParams(location.search);
    const id=p.get("site")||selectedSiteId()||list[0]?.id||"";
    const conv=p.get("conversation")||"";
    const prompt=p.get("prompt")||"";
    setSiteId(id);
    setConversationId(conv);
    if(prompt)setDraft(prompt);
    if(id)rememberSite(id,list.find(x=>x.id===id)?.name||"");
  })()},[]);

  useEffect(()=>{if(siteId)loadContext(siteId)},[siteId,loadContext]);

  useEffect(()=>{
    if(!conversationId){setMessages([]);return}
    (async()=>{
      const r=await supabase().rpc("wl_ai_messages",{p_conversation_id:conversationId,p_limit:100});
      if(!r.error)setMessages((r.data||[]).map(m=>({
        role:m.role,content:m.content,payload:m.payload||{},id:m.id
      })));
    })();
  },[conversationId]);

  useEffect(()=>{
    const el=inputRef.current;
    if(!el)return;
    el.style.height="0";
    el.style.height=Math.max(38,Math.min(el.scrollHeight,140))+"px";
    el.style.overflowY=el.scrollHeight>140?"auto":"hidden";
  },[draft]);

  async function addAttachments(fileList){
    setError("");
    const incoming=Array.from(fileList||[]);
    if(!incoming.length)return;
    if(attachments.length+incoming.length>MAX_IMAGES){
      setError("You can attach up to 3 images at a time.");
      return;
    }
    const next=[];
    for(const file of incoming){
      if(!IMAGE_TYPES.has(file.type)){
        setError("Use JPG, PNG or WebP images.");
        return;
      }
      if(file.size>MAX_IMAGE_BYTES){
        setError("Each image must be 4 MB or smaller.");
        return;
      }
      try{
        const dataUrl=await readImage(file);
        const comma=dataUrl.indexOf(",");
        if(comma<0)throw new Error("Could not read that image.");
        next.push({
          id:crypto.randomUUID(),
          name:file.name||"image",
          content_type:file.type,
          bytes:file.size,
          data_base64:dataUrl.slice(comma+1),
          preview_url:dataUrl
        });
      }catch(e){setError(e.message||"Could not read that image.");return}
    }
    setAttachments(current=>current.concat(next));
  }

  function removeAttachment(id){setAttachments(current=>current.filter(a=>a.id!==id))}

  async function send(text=draft){
    const typed=String(text||"").trim();
    if((!typed&&!attachments.length)||busy||!siteId)return;
    const visiblePrompt=typed||"Please review the attached image and tell me what matters and what I should do next.";
    const outgoing=attachments;
    setDraft("");
    setAttachments([]);
    setError("");
    setBusy(true);
    setMessages(m=>m.concat([{
      role:"user",
      content:visiblePrompt,
      payload:{attachments:outgoing.map(a=>({
        id:a.id,name:a.name,content_type:a.content_type,bytes:a.bytes,preview_url:a.preview_url
      }))}
    }]));
    const r=await supabase().functions.invoke("watchlog-ai",{body:{
      prompt:typed,
      site_id:siteId,
      conversation_id:conversationId||null,
      attachments:outgoing.map(a=>({name:a.name,content_type:a.content_type,data_base64:a.data_base64}))
    }});
    setBusy(false);
    if(r.error||r.data?.error){
      setError(r.data?.message||say(r.error)||"WatchLog could not complete that request.");
      return;
    }
    const id=r.data.conversation_id||conversationId;
    if(id&&!conversationId){
      setConversationId(id);
      history.replaceState(null,"","/ai/?site="+encodeURIComponent(siteId)+"&conversation="+encodeURIComponent(id));
    }
    setMessages(m=>m.concat([{role:"assistant",content:r.data.answer,payload:r.data||{}}]));
    loadContext(siteId);
  }

  return{
    email,sites,siteId,site:sites.find(s=>s.id===siteId),ctx,messages,
    draft,setDraft,attachments,addAttachments,removeAttachment,
    busy,error,inputRef,send
  };
}
