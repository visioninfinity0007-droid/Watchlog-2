"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { requireTenant, setupPill } from "../shell";
import { OwnerPage, Lead, Section, Row, Status, RailSection, Stat, Summary, Empty, Loading, Notice } from "../owner/ui";

const INSTALLER_URL=process.env.NEXT_PUBLIC_INSTALLER_URL||"";
const BILLING_URL=process.env.NEXT_PUBLIC_BILLING_URL||"";
const BILLING_PROVIDER=process.env.NEXT_PUBLIC_BILLING_PROVIDER||"";
const MARKETING_URL=process.env.NEXT_PUBLIC_MARKETING_URL||"";
function fmt(ts){return ts?new Date(ts).toLocaleString():"No activity reported";}
function money(minor,cur){return minor==null?"Not available":`${cur||"PKR"} ${(minor/100).toLocaleString()}`;}
function day(d){return new Date(`${d}T00:00:00`).toLocaleDateString();}
function cap(v){return v?String(v).charAt(0).toUpperCase()+String(v).slice(1).replaceAll("_"," "):"";}
// Setup state classes from the shared setupPill map onto Signal Ledger tones.
function pillTone(cls){return cls==="s-ok"?"ok":cls==="s-warn"?"warn":cls==="s-bad"?"bad":"unknown";}
function accountStatus(s){
  if(s==="active")return["ok","Active"];
  if(s==="trialing")return["warn","Trial"];
  if(s==="past_due")return["bad","Payment overdue"];
  if(!s)return["unknown","Not confirmed"];
  return["unknown",cap(s)];
}

export default function Settings(){
  const[email,setEmail]=useState(""),[trial,setTrial]=useState(null),[sites,setSites]=useState(null),[billing,setBilling]=useState(null),[plans,setPlans]=useState([]),[entitlement,setEntitlement]=useState(null),[documents,setDocuments]=useState(null),[role,setRole]=useState("viewer"),[tab,setTab]=useState("account"),[err,setErr]=useState(""),[note,setNote]=useState(""),[newSite,setNewSite]=useState(""),[busy,setBusy]=useState(false);
  const load=useCallback(async()=>{const g=await requireTenant();if(!g)return;setEmail(g.session.user.email||"");const sb=supabase();const[t,s,p,e,r]=await Promise.all([sb.rpc("wl_trial_status"),sb.rpc("wl_sites"),sb.rpc("wl_billing_plans"),sb.rpc("wl_entitlement"),sb.rpc("wl_my_role")]);const failure=t.error||s.error||p.error||e.error||r.error;if(failure){setErr(say(failure));return;}const nextRole=r.data||"viewer";let billingData={},documentData=null;if(nextRole==="owner"){const b=await sb.rpc("wl_billing_overview");if(b.error){setErr(say(b.error));return;}billingData=b.data||{};const d=await sb.rpc("wl_customer_documents");if(!d.error)documentData=d.data||{};}setErr("");setTrial(t.data||{});setSites(s.data||[]);setBilling(billingData);setPlans(p.data||[]);setEntitlement(e.data||{});setDocuments(documentData);setRole(nextRole);},[]);
  useEffect(()=>{load();},[load]);
  const canBill=role==="owner",canOperate=role==="owner"||role==="admin",billingConfigured=Boolean(BILLING_PROVIDER&&BILLING_URL),t=trial||{};
  async function startCheckout(plan){setErr("");setNote("");if(!billingConfigured){setErr("Online plan changes are not available for this account yet. Contact WatchLog Support and we will help with the change.");return;}const{data,error}=await supabase().rpc("wl_billing_start_checkout",{p_plan:plan,p_provider:BILLING_PROVIDER});if(error){setErr(say(error));return;}if(data?.checkout_path){window.location.href=BILLING_URL+data.checkout_path;return;}setErr("We could not start the plan change. Your current access has not been changed. Please contact WatchLog Support.");}
  async function cancelSub(){if(!confirm("Cancel this subscription at the end of the current billing period?"))return;setErr("");setNote("");const{data,error}=await supabase().rpc("wl_billing_cancel");if(error){setErr(say(error));return;}setNote(data?.note||"Cancellation requested.");load();}
  async function addSite(e){e.preventDefault();setBusy(true);setErr("");setNote("");const{error}=await supabase().rpc("wl_add_site",{p_name:newSite.trim()});setBusy(false);if(error){setErr(say(error));return;}setNote("Site added. Create a setup code when you are ready to connect its cameras.");setNewSite("");load();}
  async function issueCode(siteId){setErr("");setNote("");const{data,error}=await supabase().rpc("wl_issue_code",{p_site_id:siteId,p_days:14});if(error){setErr(say(error));return;}setNote(`Setup code ${data.code} created. It can be used once and is valid for 14 days.`);load();}
  async function copyCode(code){if(!code)return;try{await navigator.clipboard.writeText(code);setErr("");setNote("Setup code copied.");}catch{setErr("Could not copy the setup code automatically. Select the code and copy it manually.");}}

  const loading=trial===null;
  const[statusTone,statusLabel]=accountStatus(t.status);
  const planName=cap(t.plan||"trial");
  const trialDays=t.status==="trialing"&&t.days_left!=null?String(t.days_left):null;
  const reporting=entitlement?.reporting_enabled;
  const reportingStatus=reporting===true?<Status tone="ok">Active</Status>:reporting===false?<Status tone="warn">Paused</Status>:<Status tone="unknown">Not confirmed</Status>;
  const siteList=sites||[];
  const connected=siteList.filter(s=>s.setup_state==="ready"&&s.online).length;

  let lead={tone:statusTone,title:`${planName} plan · ${statusLabel.toLowerCase()}`,body:""};
  if(t.status==="trialing")lead={tone:"warn",title:trialDays!=null?`Trial · ${trialDays} day${trialDays==="1"?"":"s"} left`:"Trial in progress",body:t.trial_ends_at?`Trial ends ${new Date(t.trial_ends_at).toLocaleDateString()}.`:""};
  else if(t.status==="past_due")lead={tone:"bad",title:"Payment is overdue",body:canBill?"Contact WatchLog Support about the outstanding payment.":"The account Owner manages billing."};
  else if(t.status==="active")lead={tone:reporting===false?"warn":"ok",title:`${planName} plan is active`,body:reporting===false?"Daily reporting is paused.":""};
  else if(!t.status)lead={tone:"unknown",title:"Account status could not be confirmed",body:"Contact WatchLog Support if this continues."};

  let siteLead=null;
  if(sites!==null){
    if(!siteList.length)siteLead={tone:"unknown",title:"No sites yet",body:canOperate?"Add your first site, then create a setup code to connect it.":"An Owner or Admin can add the first site."};
    else siteLead={tone:connected===siteList.length?"ok":"warn",title:`${connected} of ${siteList.length} site${siteList.length===1?"":"s"} connected`,body:connected===siteList.length?"":"Sites that are not connected show their setup step below."};
  }

  const rail=loading?null:<>
    <RailSection label="Account">
      <Stat label="Plan" value={planName}/>
      <Stat label="Status" value={<Status tone={statusTone}>{statusLabel}</Status>}/>
      {t.status==="trialing"&&<Stat label="Trial days left" value={trialDays}/>}
      <Stat label="Daily reporting" value={reportingStatus}/>
      <Stat label="Your role" value={cap(role)}/>
    </RailSection>
    <RailSection label="Sites">
      <Stat label="Sites" value={sites===null?null:String(siteList.length)}/>
      <Stat label="Connected" value={sites===null?null:String(connected)}/>
    </RailSection>
  </>;

  return <OwnerPage active="Settings" email={email}
    kicker={["Account",cap(role)]}
    title="Account and plan"
    actions={<a className="ow-btn quiet" href="/settings/">All settings</a>}
    rail={rail}
    summary={loading?null:<Summary items={[{value:planName,label:"Plan"},{value:statusLabel,label:"Account status",muted:statusTone==="unknown"},{value:sites===null?"—":`${connected} of ${siteList.length}`,label:"Sites connected"}]}/>}>
    {err&&<Notice tone="bad">{err}</Notice>}{note&&<Notice tone="ok">{note}</Notice>}
    <div className="ow-tabs" role="tablist" aria-label="Settings sections"><button type="button" role="tab" aria-selected={tab==="account"} className="ow-tab" onClick={()=>setTab("account")}>Account &amp; Plan</button><button type="button" role="tab" aria-selected={tab==="sites"} className="ow-tab" onClick={()=>setTab("sites")}>Sites &amp; Setup</button></div>

    {loading?<Loading label="Loading your account"/>:tab==="account"?<>
      <Lead tone={lead.tone} title={lead.title} body={lead.body||undefined}/>
      {!canBill&&<Notice><span><strong>Plan changes are owner-managed.</strong> Your {role} role can see the plan and account status; the account Owner manages billing.</span></Notice>}
      {canBill&&!billingConfigured&&<Notice><span><strong>Need to change your plan?</strong> Online plan changes are not enabled for this account yet. Contact WatchLog Support and we will handle the change with you.</span></Notice>}

      <Section first title={t.status==="active"?"Plan options":"Choose a plan"} note="Your current package stays unchanged until a plan change is confirmed.">
        {plans.length?<table className="ow-table"><thead><tr><th>Plan</th><th>Price</th><th></th></tr></thead><tbody>{plans.map((p)=><tr key={p.plan}><td><b>{cap(p.plan)}</b>{p.plan===t.plan&&<> <Status tone="verified">Current</Status></>}</td><td className="ow-mono">{money(p.amount_minor,p.currency)} / month</td><td style={{textAlign:"right"}}>{canBill&&billingConfigured&&<button type="button" className="ow-btn small quiet" onClick={()=>startCheckout(p.plan)}>Choose {p.plan}</button>}</td></tr>)}</tbody></table>:<Empty title="No plans are listed for this account yet.">Contact WatchLog Support to review your package.</Empty>}
        {MARKETING_URL&&<p className="ow-muted" style={{marginTop:10,fontSize:13}}>Need a larger or custom rollout? <a href={MARKETING_URL+"/contact/"}>Talk to WatchLog</a>.</p>}
      </Section>

      <Section title="Account state" action={canBill&&t.status==="active"?<button type="button" className="ow-btn small danger" onClick={cancelSub}>Cancel at period end</button>:null}>
        <div className="ow-rows">
          <Row tone={statusTone} title={statusLabel} body={t.status==="trialing"?`Trial ends ${t.trial_ends_at?new Date(t.trial_ends_at).toLocaleDateString():"soon"}`:"Current account state"} compact/>
          {canBill&&billing?.subscription?.current_period_end&&<Row tone={billing.subscription.cancel_at_period_end?"warn":"ok"} title={new Date(billing.subscription.current_period_end).toLocaleDateString()} body={billing.subscription.cancel_at_period_end?"Access ends at this period boundary":"Current period renews"} compact/>}
          <Row tone="verified" title="Your history stays available" body="If service pauses, your incident and report history remains in your account." compact/>
        </div>
      </Section>

      {canBill&&<Section title="Payment history" count={(billing?.transactions||[]).length||null}>
        {(billing?.transactions||[]).length?<table className="ow-table"><thead><tr><th>Date</th><th>Plan</th><th>Amount</th><th>Status</th></tr></thead><tbody>{billing.transactions.map((x,i)=><tr key={i}><td className="ow-mono">{new Date(x.created_at).toLocaleDateString()}</td><td>{cap(x.plan)||"Not recorded"}</td><td className="ow-mono">{money(x.amount_minor,x.currency)}</td><td><Status tone={x.status==="succeeded"?"ok":x.status==="failed"?"bad":"unknown"}>{cap(x.status)||"Not confirmed"}</Status></td></tr>)}</tbody></table>:<Empty title="No payment history yet."/>}
      </Section>}

      {canBill&&documents&&<Section title={<>Invoices &amp; agreements</>}>
        <div className="ow-grid2">
          <div>
            <div className="ow-label" style={{marginBottom:8}}>Invoices</div>
            {(documents.invoices||[]).length?<div className="ow-rows">{(documents.invoices||[]).map(i=><Row key={i.id} compact tone={i.status==="paid"?"ok":i.status==="overdue"?"bad":"unknown"} title={i.invoice_number} meta={[`${day(i.issue_date)} · due ${day(i.due_date)}`,money(i.total_minor,i.currency)]} action={<Status tone={i.status==="paid"?"ok":i.status==="overdue"?"bad":"unknown"}>{cap(i.status)}</Status>}/>)}</div>:<Empty title="No invoices have been added to your account yet."/>}
          </div>
          <div>
            <div className="ow-label" style={{marginBottom:8}}>Agreements</div>
            {(documents.contracts||[]).length?<div className="ow-rows">{(documents.contracts||[]).map(c=><Row key={c.id} compact tone={c.status==="signed"?"ok":"unknown"} title={c.contract_number} meta={[c.title,c.ends_on?`through ${day(c.ends_on)}`:null]} action={<Status tone={c.status==="signed"?"ok":"unknown"}>{cap(c.status)}</Status>}/>)}</div>:<Empty title="No agreements have been added to your account yet."/>}
          </div>
        </div>
      </Section>}
    </>:<>
      {siteLead&&<Lead tone={siteLead.tone} title={siteLead.title} body={siteLead.body||undefined}/>}

      <Section first title="Your sites" count={sites===null?null:siteList.length}>
        {sites===null?<Loading label="Loading sites"/>:sites.length===0?<Empty title="No sites yet."/>:<table className="ow-table"><thead><tr><th>Site</th><th>Setup</th><th>Cameras</th><th>Last activity</th><th>Setup code</th></tr></thead><tbody>{sites.map((s)=>{const[slabel,scls]=setupPill(s.setup_state,s.online);return <tr key={s.id}><td><b>{s.name}</b><small>{s.timezone}</small></td><td><Status tone={pillTone(scls)}>{slabel}</Status></td><td className="ow-mono">{s.cameras??"Not reported"}</td><td className="ow-muted">{fmt(s.last_event)}</td><td><div style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:8}}>{canOperate&&s.open_code?<><span className="ow-mono">{s.open_code}</span><button type="button" className="ow-btn small quiet" onClick={()=>copyCode(s.open_code)}>Copy</button></>:s.has_open_code?<span className="ow-muted">Code ready</span>:<span className="ow-muted">No active code</span>}{canOperate&&<button type="button" className="ow-linkbtn" onClick={()=>issueCode(s.id)}>New code</button>}</div></td></tr>;})}</tbody></table>}
        {canOperate?<form onSubmit={addSite} style={{display:"flex",flexWrap:"wrap",alignItems:"flex-end",gap:10,marginTop:16}}><label className="ow-field" style={{flex:"1 1 240px"}}>Add a site<input required placeholder="Warehouse, Head Office, Branch 2" value={newSite} onChange={(e)=>setNewSite(e.target.value)}/></label><button type="submit" className="ow-btn" disabled={busy} aria-busy={busy}>{busy?"Adding...":"Add site"}</button></form>
          :<div style={{marginTop:14}}><Notice><span><strong>Read-only site access.</strong> Owners and Admins manage sites and setup codes.</span></Notice></div>}
      </Section>

      <Section title="Connect a site" note="Enter the setup code on a Windows computer at the location. One code per site, usable once, valid for 14 days.">
        {INSTALLER_URL?<a className="ow-btn quiet" href={INSTALLER_URL}>Download WatchLog for Windows</a>:<Empty title="Windows setup is not available for download yet.">Contact WatchLog Support if you are preparing a site now.</Empty>}
        <details className="ow-details" style={{marginTop:16}}><summary>What you need</summary><div className="ow-rows">
          <Row compact tone="neutral" title="Your existing CCTV recorder" body="A supported NVR/DVR connected to the site network."/>
          <Row compact tone="neutral" title="A Windows site computer" body="On the same network and normally kept switched on."/>
          <Row compact tone="neutral" title="Your CCTV login" body="Used during guided setup to confirm the camera system. It stays at your site."/>
          <Row compact tone="neutral" title="Internet access" body="A normal internet connection for WatchLog updates and reports. Your recorder is not exposed to the internet."/>
        </div></details>
      </Section>
    </>}
  </OwnerPage>;
}
