"use client";
import {useEffect,useState} from "react";
import {supabase} from "../../lib/supabase";
import Mark from "../mark";
import CustomerCard from "./customer-card";
import CustomerActions from "./customer-actions";

function AttachmentImage({attachment,styles}){
  const[url,setUrl]=useState(attachment.preview_url||"");
  useEffect(()=>{
    if(url||!attachment.id)return;
    let live=true;
    (async()=>{
      const r=await supabase().functions.invoke("watchlog-ai",{body:{op:"attachment_url",attachment_id:attachment.id}});
      if(live&&!r.error&&r.data?.url)setUrl(r.data.url);
    })();
    return()=>{live=false};
  },[attachment.id,url]);
  return <div className={styles.messageImage}>
    {url?<img src={url} alt={attachment.name||"Attached image"} />:<div className={styles.imageLoading}>Image</div>}
    <small>{attachment.name||"Attached image"}</small>
  </div>;
}

export default function CustomerMessage({message,siteId,styles}){
  const cards=message.payload?.cards||[];
  const actions=message.payload?.proposed_actions||[];
  const attachments=message.payload?.attachments||[];
  return <div className={styles.message+" "+(message.role==="user"?styles.user:styles.assistant)}>
    <div className={styles.avatar}>{message.role==="user"?"You":<Mark size={24}/>}</div>
    <div className={styles.messageBody}>
      {attachments.length>0&&<div className={styles.messageAttachments}>
        {attachments.map((a,i)=><AttachmentImage attachment={a} styles={styles} key={a.id||i}/>)}
      </div>}
      <div className={styles.messageText}>{message.content}</div>
      {cards.map((c,i)=><CustomerCard card={c} siteId={siteId} styles={styles} key={i}/>)}
      <CustomerActions actions={actions} siteId={siteId} styles={styles}/>
    </div>
  </div>;
}
