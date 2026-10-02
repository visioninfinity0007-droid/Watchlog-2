"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { requireTenant } from "../shell";
import { selectedSiteId } from "../site-context";
import { OwnerPage, Section, Metrics, Status, Empty, Loading, Notice } from "../owner/ui";

const ROLES=["owner","admin","viewer"];
const ROLE_COPY={owner:["Owner","Full account control","Account, billing, team roles and all WatchLog settings"],admin:["Admin","Day-to-day management","Sites, insights, reports and team access"],viewer:["Viewer","Read-only access","Home, Attention, Insights, Reports and supporting evidence"]};
function roleName(r){return ROLE_COPY[r]?.[0]||String(r||"Member").replace(/^./,c=>c.toUpperCase());}
function fmt(ts){if(!ts)return"—";try{return new Intl.DateTimeFormat("en-PK",{day:"numeric",month:"short",year:"numeric"}).format(new Date(ts));}catch{return String(ts);}}

export default function Team(){
  const[email,setEmail]=useState(""),[members,setMembers]=useState(null),[invites,setInvites]=useState([]),[myRole,setMyRole]=useState("viewer"),[err,setErr]=useState(""),[note,setNote]=useState(""),[inviteEmail,setInviteEmail]=useState(""),[inviteRole,setInviteRole]=useState("viewer"),[busy,setBusy]=useState(false),[latestLink,setLatestLink]=useState(""),[siteId,setSiteId]=useState("");
  const load=useCallback(async()=>{const g=await requireTenant();if(!g)return;setEmail(g.session.user.email||"");const sb=supabase();const[m,i]=await Promise.all([sb.rpc("wl_members"),sb.rpc("wl_invitations")]);if(m.error||i.error){setErr(say(m.error||i.error));return;}setErr("");const list=m.data||[];setMembers(list);setInvites(i.data||[]);const me=list.find((x)=>x.is_you);if(me)setMyRole(me.role);},[]);
  useEffect(()=>{load();},[load]);
  useEffect(()=>{setSiteId(selectedSiteId());},[]);
  const canManage=myRole==="owner"||myRole==="admin";
  async function invite(e){e.preventDefault();setBusy(true);setErr("");setNote("");setLatestLink("");const{data,error}=await supabase().rpc("wl_invite_member",{p_email:inviteEmail.trim(),p_role:inviteRole});setBusy(false);if(error){setErr(say(error));return;}if(data?.ok===false)setNote(data.note||"This person already has access.");else{const link=`${location.origin}/invite/?token=${data.token}`;setLatestLink(link);setNote(`Invitation created for ${inviteEmail.trim()}. Copy the invitation link below and send it to them.`);setInviteEmail("");}load();}
  async function copyLink(){if(!latestLink)return;try{await navigator.clipboard.writeText(latestLink);setNote("Invitation link copied.");}catch{setErr("Could not copy automatically. Select the link and copy it manually.");}}
  async function changeRole(uid,role){setErr("");const{error}=await supabase().rpc("wl_set_member_role",{p_user_id:uid,p_role:role});if(error)setErr(say(error));else setNote("Role updated.");load();}
  async function remove(uid){if(!confirm("Remove this person from your WatchLog account?"))return;setErr("");const{error}=await supabase().rpc("wl_remove_member",{p_user_id:uid});if(error)setErr(say(error));else setNote("Team member removed.");load();}
  async function revoke(id){if(!confirm("Revoke this pending invitation?"))return;const{error}=await supabase().rpc("wl_revoke_invite",{p_id:id});if(error)setErr(say(error));else setNote("Invitation revoked.");load();}

  const open=invites.filter((i)=>!i.expired);
  const expired=invites.filter((i)=>i.expired);

  return <OwnerPage active="Team" email={email} siteId={siteId}
    kicker={["Team"]}
    title="Who has access"
    actions={canManage?<a className="ow-btn quiet" href="#invite">Invite someone</a>:null}>
    {err&&<Notice tone="bad">{err}</Notice>}
    {note&&<Notice tone="ok">{note}</Notice>}

    {members===null?(err?null:<Loading label="Loading team"/>):<>
      <Metrics items={[
        {value:String(members.length),label:"Team members"},
        {value:String(open.length),label:"Open invitations",note:expired.length?expired.length+" expired":null},
        {value:roleName(myRole),label:"Your role",note:ROLE_COPY[myRole]?.[1]||null},
      ]}/>

      <Section title="People" count={members.length+invites.length||null} note="Access covers every site on this account.">
        {members.length===0&&invites.length===0?<Empty title="No members yet."/>:<table className="ow-table">
          <thead><tr><th>Person</th><th>Role</th><th>Site access</th><th>Status</th><th>Action</th></tr></thead>
          <tbody>
            {members.map((m)=><tr key={m.user_id}>
              <td><b>{m.email}</b>{m.is_you&&<small>Signed in as you</small>}</td>
              <td>{myRole==="owner"&&!m.is_you?<select aria-label={"Role for "+m.email} value={m.role} onChange={(e)=>changeRole(m.user_id,e.target.value)}>{ROLES.map((r)=><option key={r} value={r}>{ROLE_COPY[r][0]}</option>)}</select>:<span className="ow-pill">{roleName(m.role)}</span>}</td>
              <td>All sites</td>
              <td>Active<small>Joined {fmt(m.joined)}</small></td>
              <td>{canManage&&!m.is_you?<button type="button" className="ow-btn danger small" onClick={()=>remove(m.user_id)}>Remove</button>:<span className="ow-muted">—</span>}</td>
            </tr>)}
            {invites.map((i)=><tr key={i.id}>
              <td><b>{i.email}</b><small>Invited {fmt(i.created_at)}</small></td>
              <td><span className="ow-pill">{roleName(i.role)}</span></td>
              <td>All sites</td>
              <td>{i.expired?<Status tone="unknown">Invitation expired</Status>:<Status tone="warn">Invitation pending</Status>}{!i.expired&&<small>Expires {fmt(i.expires_at)}</small>}</td>
              <td>{canManage?<button type="button" className="ow-btn danger small" onClick={()=>revoke(i.id)}>Revoke</button>:<span className="ow-muted">—</span>}</td>
            </tr>)}
          </tbody>
        </table>}
      </Section>

      <Section id="invite" title="Invite someone" note="Invitations expire after seven days.">
        {canManage?<>
          <form onSubmit={invite} style={{display:"flex",flexWrap:"wrap",alignItems:"flex-end",gap:12}}>
            <label className="ow-field" style={{flex:"1 1 260px"}}>Work email<input type="email" required placeholder="name@company.com" value={inviteEmail} onChange={(e)=>setInviteEmail(e.target.value)}/></label>
            <label className="ow-field" style={{flex:"0 1 230px"}}>Role<select value={inviteRole} onChange={(e)=>setInviteRole(e.target.value)}><option value="viewer">Viewer (read only)</option><option value="admin">Admin (operations)</option>{myRole==="owner"&&<option value="owner">Owner (full access)</option>}</select></label>
            <button type="submit" className="ow-btn" disabled={busy} aria-busy={busy}>{busy?"Creating invite...":"Create invite"}</button>
          </form>
          {latestLink&&<div className="ow-panel" style={{marginTop:14,display:"flex",flexWrap:"wrap",alignItems:"center",gap:10}}>
            <span className="ow-label" style={{flex:"1 0 100%"}}>Invitation link</span>
            <span className="ow-mono" style={{flex:"1 1 240px",minWidth:0,overflowWrap:"anywhere"}}>{latestLink}</span>
            <button type="button" className="ow-btn quiet small" onClick={copyLink}>Copy</button>
          </div>}
        </>:<Empty title="Read-only team access">Only an Owner or Admin can invite people. You can still see who has access to this account.</Empty>}
      </Section>

      <Section title="What each role can do">
        <div className="ow-rows">{ROLES.map((r)=><article className="ow-row compact" key={r}>
          <i className="ow-row-tick" aria-hidden="true"/>
          <div className="ow-row-main"><h3>{ROLE_COPY[r][0]} · {ROLE_COPY[r][1]}</h3><p>{ROLE_COPY[r][2]}</p></div>
          {r===myRole&&<div className="ow-row-act"><span className="ow-pill">Your role</span></div>}
        </article>)}</div>
      </Section>
    </>}
  </OwnerPage>;
}
