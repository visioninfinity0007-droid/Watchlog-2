"use client";
import {useEffect,useState} from "react";
import {Section} from "../../owner/ui";

export default function RecipientForm({delivery}){
  const[form,setForm]=useState({name:"",email:"",whatsapp:"",channel:"email",site:""});
  const[busy,setBusy]=useState(false);
  const canManage=delivery.role==="owner"||delivery.role==="admin";

  useEffect(()=>{
    if(delivery.siteId&&!form.site)setForm(current=>({...current,site:current.site||delivery.siteId}));
  },[delivery.siteId]); // eslint-disable-line react-hooks/exhaustive-deps

  if(!canManage)return null;

  async function submit(e){
    e.preventDefault();setBusy(true);
    if(await delivery.add(form))setForm({name:"",email:"",whatsapp:"",channel:"email",site:delivery.siteId||""});
    setBusy(false);
  }

  const field={display:"grid",gap:5};
  return <Section title="Add a recipient">
    <form onSubmit={submit} aria-busy={busy}>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:12}}>
        <label className="ow-field" style={field}>Name<input value={form.name} onChange={e=>setForm({...form,name:e.target.value})} placeholder="Owner, Operations team"/></label>
        <label className="ow-field" style={field}>Send by<select value={form.channel} onChange={e=>setForm({...form,channel:e.target.value})}><option value="email">Email</option><option value="whatsapp">WhatsApp</option><option value="both">Email + WhatsApp</option></select></label>
        <label className="ow-field" style={field}>Site<select value={form.site} onChange={e=>setForm({...form,site:e.target.value})}><option value="">All sites</option>{delivery.sites.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        {form.channel!=="whatsapp"&&<label className="ow-field" style={field}>Email<input type="email" value={form.email} onChange={e=>setForm({...form,email:e.target.value})} placeholder="owner@business.com"/></label>}
        {form.channel!=="email"&&<label className="ow-field" style={field}>WhatsApp<input type="tel" value={form.whatsapp} onChange={e=>setForm({...form,whatsapp:e.target.value})} placeholder="+92 3XX XXXXXXX"/></label>}
      </div>
      <div style={{marginTop:14}}>
        <button type="submit" className="ow-btn" disabled={busy}>{busy?"Saving…":"Add recipient"}</button>
      </div>
    </form>
  </Section>;
}
