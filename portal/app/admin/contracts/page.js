"use client";

import { useEffect, useState } from "react";
import Mark from "../../mark";
import { supabase, say } from "../../../lib/supabase";
import { AdminNav, canOperate, requirePlatformAdmin } from "../admin-shell";
import styles from "../admin.module.css";

const money=(n,c="PKR")=>n==null?"-":`${c} ${(Number(n)/100).toLocaleString()}`;
const dateOnly=(v)=>v?new Date(`${v}T00:00:00`).toLocaleDateString():"-";

export default function ContractWorkspace(){
  const[admin,setAdmin]=useState(null),[data,setData]=useState(null),[error,setError]=useState(""),[reason,setReason]=useState("Contract status updated"),[documentUrl,setDocumentUrl]=useState(""),[busy,setBusy]=useState(false);
  const id=typeof window!=="undefined"?new URLSearchParams(location.search).get("contract"):null;
  async function load(contractId=id){if(!contractId)return;const{data,error}=await supabase().rpc("wl_platform_contract",{p_contract_id:Number(contractId)});if(error){setError(say(error));return;}setData(data);setDocumentUrl(data?.contract?.document_url||"");setError("");}
  useEffect(()=>{(async()=>{const guard=await requirePlatformAdmin();if(!guard)return;setAdmin(guard.admin);await load();})();},[]); // eslint-disable-line react-hooks/exhaustive-deps
  async function setStatus(status){if(!id)return;setBusy(true);const{error}=await supabase().rpc("wl_platform_set_contract_status",{p_contract_id:Number(id),p_status:status,p_document_url:documentUrl||null,p_reason:reason.trim()});setBusy(false);if(error){setError(say(error));return;}await load();}
  const contract=data?.contract||{},tenant=data?.tenant||{},profile=data?.billing_profile||{};
  return <div className="shell"><div className="no-print"><AdminNav active="Commercial" admin={admin}/></div><main className="main">
    {error&&<div className="err no-print">{error}</div>}
    {!data?<div className={styles.card}>Loading contract...</div>:<>
      <div className="no-print" style={{display:"flex",justifyContent:"space-between",gap:16,alignItems:"center",marginBottom:18}}><a className={styles.link} href={`/admin/tenants/?tenant=${tenant.id}`}>← Back to {tenant.name}</a><div className={styles.actions}><input aria-label="Audit reason" value={reason} onChange={e=>setReason(e.target.value)} style={{width:230}}/><input aria-label="Signed document URL" value={documentUrl} onChange={e=>setDocumentUrl(e.target.value)} placeholder="Signed document URL (optional)" style={{width:260}}/><button className="ghost small" onClick={()=>window.print()}>Print / Save PDF</button>{canOperate(admin?.role)&&contract.status!=="sent"&&contract.status!=="signed"&&<button className="small" disabled={busy||reason.trim().length<4} onClick={()=>setStatus("sent")}>Mark sent</button>}{canOperate(admin?.role)&&contract.status!=="signed"&&<button className="small" disabled={busy||reason.trim().length<4} onClick={()=>setStatus("signed")}>Mark signed</button>}{canOperate(admin?.role)&&!['terminated','expired'].includes(contract.status)&&<button className="btn-danger" disabled={busy||reason.trim().length<4} onClick={()=>setStatus("terminated")}>Terminate</button>}</div></div>
      <article className={styles.card} style={{maxWidth:900,margin:"0 auto",background:"#fff",color:"#162033",padding:42}}>
        <header style={{display:"flex",justifyContent:"space-between",gap:32,alignItems:"flex-start",borderBottom:"1px solid #dfe5ee",paddingBottom:24,marginBottom:30}}><div style={{display:"flex",gap:12,alignItems:"center"}}><Mark size={34}/><div><strong style={{fontSize:24}}>WatchLog</strong><div style={{fontSize:12,color:"#657083"}}>Video Analytics & CCTV Intelligence</div></div></div><div style={{textAlign:"right"}}><div style={{fontSize:13,color:"#657083",textTransform:"uppercase",letterSpacing:".08em"}}>Service agreement</div><div style={{fontSize:22,fontWeight:800}}>{contract.contract_number}</div><div style={{marginTop:6,textTransform:"capitalize"}}>{contract.status}</div></div></header>
        <h1 style={{fontSize:30,marginBottom:12}}>{contract.title}</h1><p style={{fontSize:15,lineHeight:1.7}}>This agreement records the approved WatchLog service arrangement for <strong>{profile.legal_name||tenant.name}</strong>.</p>
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:16,margin:"28px 0"}}><div style={{padding:16,border:"1px solid #dfe5ee",borderRadius:8}}><div style={{fontSize:11,textTransform:"uppercase",letterSpacing:".08em",color:"#657083"}}>Starts</div><strong>{dateOnly(contract.starts_on)}</strong></div><div style={{padding:16,border:"1px solid #dfe5ee",borderRadius:8}}><div style={{fontSize:11,textTransform:"uppercase",letterSpacing:".08em",color:"#657083"}}>Ends</div><strong>{dateOnly(contract.ends_on)}</strong></div><div style={{padding:16,border:"1px solid #dfe5ee",borderRadius:8}}><div style={{fontSize:11,textTransform:"uppercase",letterSpacing:".08em",color:"#657083"}}>Commercial value</div><strong>{money(contract.value_minor,contract.currency)}</strong></div></div>
        <section><h2 style={{fontSize:18,marginBottom:10}}>Commercial terms</h2><div style={{whiteSpace:"pre-wrap",lineHeight:1.75,minHeight:180}}>{contract.terms||"No additional written terms were entered for this agreement."}</div></section>
        <section style={{marginTop:34,display:"grid",gridTemplateColumns:"1fr 1fr",gap:40}}><div><div style={{borderTop:"1px solid #7f8998",paddingTop:10}}>For WatchLog</div><div style={{fontSize:12,color:"#657083",marginTop:3}}>Authorized representative</div></div><div><div style={{borderTop:"1px solid #7f8998",paddingTop:10}}>For {profile.legal_name||tenant.name}</div><div style={{fontSize:12,color:"#657083",marginTop:3}}>Authorized customer representative</div></div></section>
        {contract.document_url&&<p style={{marginTop:28,fontSize:12,color:"#657083"}}>Signed-document record: {contract.document_url}</p>}
        <footer style={{marginTop:38,paddingTop:18,borderTop:"1px solid #dfe5ee",fontSize:11,color:"#657083"}}>WatchLog contract record {contract.contract_number}. Keep the signed copy with the customer account record.</footer>
      </article>
    </>}
    <style jsx global>{`@media print {.no-print,.topbar{display:none!important}.main{padding:0!important}.shell{background:#fff!important}body{background:#fff!important}}`}</style>
  </main></div>;
}
