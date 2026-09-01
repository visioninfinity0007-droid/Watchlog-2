"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import ui from "../portal.module.css";

const ROLES=["owner","admin","viewer"];
const ROLE_COPY={owner:["Owner","Full account control","Billing, team roles and all operations"],admin:["Admin","Day-to-day operations","Sites, analytics, recipients and team invites"],viewer:["Viewer","Read-only visibility","Overview, incidents, health, analytics and reports"]};
function fmt(ts){return ts?new Date(ts).toLocaleDateString():"-";}

export default function Team(){
  const[email,setEmail]=useState(""),[members,setMembers]=useState(null),[invites,setInvites]=useState([]),[myRole,setMyRole]=useState("viewer"),[err,setErr]=useState(""),[note,setNote]=useState(""),[inviteEmail,setInviteEmail]=useState(""),[inviteRole,setInviteRole]=useState("viewer"),[busy,setBusy]=useState(false),[latestLink,setLatestLink]=useState("");
  const load=useCallback(async()=>{const g=await requireTenant();if(!g)return;setEmail(g.session.user.email||"");const sb=supabase();const[m,i]=await Promise.all([sb.rpc("wl_members"),sb.rpc("wl_invitations")]);if(m.error||i.error){setErr(say(m.error||i.error));return;}setErr("");const list=m.data||[];setMembers(list);setInvites(i.data||[]);const me=list.find((x)=>x.is_you);if(me)setMyRole(me.role);},[]);
  useEffect(()=>{load();},[load]);
  const canManage=myRole==="owner"||myRole==="admin";
  async function invite(e){e.preventDefault();setBusy(true);setErr("");setNote("");setLatestLink("");const{data,error}=await supabase().rpc("wl_invite_member",{p_email:inviteEmail.trim(),p_role:inviteRole});setBusy(false);if(error){setErr(say(error));return;}if(data?.ok===false)setNote(data.note||"Already on the team.");else{const link=`${location.origin}/invite/?token=${data.token}`;setLatestLink(link);setNote(`Invitation created for ${inviteEmail.trim()}. Copy the secure link below and send it to that address.`);setInviteEmail("");}load();}
  async function copyLink(){if(!latestLink)return;try{await navigator.clipboard.writeText(latestLink);setNote("Invitation link copied.");}catch{setErr("Could not copy automatically. Select the link and copy it manually.");}}
  async function changeRole(uid,role){setErr("");const{error}=await supabase().rpc("wl_set_member_role",{p_user_id:uid,p_role:role});if(error)setErr(say(error));else setNote("Role updated.");load();}
  async function remove(uid){if(!confirm("Remove this person from the WatchLog team?"))return;setErr("");const{error}=await supabase().rpc("wl_remove_member",{p_user_id:uid});if(error)setErr(say(error));else setNote("Team member removed.");load();}
  async function revoke(id){if(!confirm("Revoke this pending invitation? The link will stop working immediately."))return;const{error}=await supabase().rpc("wl_revoke_invite",{p_id:id});if(error)setErr(say(error));else setNote("Invitation revoked.");load();}

  return <div className="shell"><Nav active="Team" email={email}/><main className="main">
    <header className={ui.pageHead}><div><div className={ui.eyebrow}>Multi-site and teams</div><h1>Give each person the access they need.</h1><p>Owners control the account, admins run operations, and viewers get read-only visibility without configuration or billing access.</p></div></header>
    {err&&<div className="err">{err}</div>}{note&&<div className="ok-note">{note}</div>}
    <section className={ui.metricGrid}><div className={ui.metric}><div className={ui.metricValue}>{members?.length??0}</div><div className={ui.metricLabel}>Team members</div></div><div className={ui.metric}><div className={ui.metricValue}>{invites.filter((i)=>!i.expired).length}</div><div className={ui.metricLabel}>Open invitations</div></div><div className={ui.metric}><div className={ui.metricValue}>{(members||[]).filter((m)=>m.role==="owner").length}</div><div className={ui.metricLabel}>Owners</div></div><div className={ui.metric}><div className={ui.metricValue} style={{textTransform:"capitalize"}}>{myRole}</div><div className={ui.metricLabel}>Your role</div></div></section>

    <div className={ui.sectionHead}><div><h2>Roles</h2><p>The permission model in plain language before you invite anyone.</p></div></div>
    <section className={ui.threeCol}>{ROLES.map((r)=>{const c=ROLE_COPY[r];return <div className={ui.roleCard} key={r}><strong>{c[0]}</strong><p>{c[1]}</p><span>{c[2]}</span></div>;})}</section>

    <div className={ui.sectionHead}><div><h2>Invite someone</h2><p>Invitation links are random, single-use, expire after seven days and can only be accepted by the invited email address.</p></div></div>
    {canManage?<div className={ui.card}><form className="row" onSubmit={invite}><div className="field"><label>Work email</label><input type="email" required placeholder="name@company.com" value={inviteEmail} onChange={(e)=>setInviteEmail(e.target.value)}/></div><div className="field" style={{maxWidth:230}}><label>Role</label><select value={inviteRole} onChange={(e)=>setInviteRole(e.target.value)}><option value="viewer">Viewer (read only)</option><option value="admin">Admin (operations)</option>{myRole==="owner"&&<option value="owner">Owner (full access)</option>}</select></div><button className="small" disabled={busy} style={{width:"auto"}}>{busy?"Creating invite...":"Create invite"}</button></form>{latestLink&&<div style={{marginTop:16}}><label>Secure invitation link</label><div className={ui.copyBox}><span>{latestLink}</span><button className="ghost small" type="button" onClick={copyLink}>Copy</button></div></div>}</div>:<div className={ui.callout}><span className={ui.statusDot}/><div><strong>Read-only team access</strong><p>Only an owner or admin can create invitations. Your viewer role can see who has access.</p></div></div>}

    <div className={ui.sectionHead}><div><h2>Team members</h2><p>Owners can change roles. Owners and admins can remove members within the server-enforced role rules.</p></div></div>
    <div className="panel"><div className={ui.tableWrap}>{members===null?<div className="empty">Loading...</div>:members.length===0?<div className="empty">No members yet.</div>:<table><thead><tr><th>Person</th><th>Role</th><th>Joined</th><th></th></tr></thead><tbody>{members.map((m)=><tr key={m.user_id}><td><b>{m.email}</b>{m.is_you&&<div className="muted" style={{fontSize:"var(--font-size-xs)"}}>Signed in as you</div>}</td><td>{myRole==="owner"&&!m.is_you?<select value={m.role} onChange={(e)=>changeRole(m.user_id,e.target.value)}>{ROLES.map((r)=><option key={r} value={r}>{ROLE_COPY[r][0]}</option>)}</select>:<span className="pill s-unk">{m.role}</span>}</td><td className="mono">{fmt(m.joined)}</td><td>{canManage&&!m.is_you&&<div className={ui.inlineActions}><button className="btn-danger" onClick={()=>remove(m.user_id)}>Remove</button></div>}</td></tr>)}</tbody></table>}</div></div>

    <div className={ui.sectionHead}><div><h2>Pending invitations</h2><p>Expired or no-longer-needed invitations can be revoked without affecting existing members.</p></div></div>
    <div className="panel"><div className={ui.tableWrap}>{invites.length===0?<div className="empty">No pending invitations.</div>:<table><thead><tr><th>Email</th><th>Role</th><th>Created</th><th>Expires</th><th></th></tr></thead><tbody>{invites.map((i)=><tr key={i.id}><td>{i.email}</td><td><span className="pill s-unk">{i.role}</span></td><td className="mono">{fmt(i.created_at)}</td><td className="mono">{i.expired?<span className="pill s-bad">expired</span>:fmt(i.expires_at)}</td><td>{canManage&&<div className={ui.inlineActions}><button className="btn-danger" onClick={()=>revoke(i.id)}>Revoke</button></div>}</td></tr>)}</tbody></table>}</div></div>
  </main></div>;
}
