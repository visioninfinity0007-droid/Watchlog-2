"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";

const ROLES = ["owner", "admin", "viewer"];

function fmt(ts) {
  if (!ts) return "—";
  return new Date(ts).toLocaleDateString();
}

export default function Team() {
  const [email, setEmail] = useState("");
  const [members, setMembers] = useState(null);
  const [invites, setInvites] = useState([]);
  const [myRole, setMyRole] = useState("viewer");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("viewer");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const g = await requireTenant();
    if (!g) return;
    setEmail(g.session.user.email || "");
    const sb = supabase();
    const [m, i] = await Promise.all([sb.rpc("wl_members"), sb.rpc("wl_invitations")]);
    if (m.error) { setErr(say(m.error)); return; }
    const list = m.data || [];
    setMembers(list);
    setInvites(i.data || []);
    const me = list.find((x) => x.is_you);
    if (me) setMyRole(me.role);
  }, []);

  useEffect(() => { load(); }, [load]);

  const canManage = myRole === "owner" || myRole === "admin";

  async function invite(e) {
    e.preventDefault();
    setBusy(true); setErr(""); setNote("");
    const { data, error } = await supabase().rpc("wl_invite_member",
      { p_email: inviteEmail.trim(), p_role: inviteRole });
    setBusy(false);
    if (error) { setErr(say(error)); return; }
    if (data && data.ok === false) { setNote(data.note || "Already on the team."); }
    else {
      setNote(`Invitation created for ${inviteEmail.trim()}. Send them this link: ` +
              `${location.origin}/invite/?token=${data.token}`);
      setInviteEmail("");
    }
    load();
  }

  async function changeRole(uid, role) {
    setErr("");
    const { error } = await supabase().rpc("wl_set_member_role",
      { p_user_id: uid, p_role: role });
    if (error) setErr(say(error));
    load();
  }
  async function remove(uid) {
    setErr("");
    const { error } = await supabase().rpc("wl_remove_member", { p_user_id: uid });
    if (error) setErr(say(error));
    load();
  }
  async function revoke(id) {
    await supabase().rpc("wl_revoke_invite", { p_id: id });
    load();
  }

  return (
    <div className="shell">
      <Nav active="Team" email={email} />
      <main className="main">
        {err && <div className="err">{err}</div>}
        {note && <div className="ok-note">{note}</div>}

        <h2>Invite someone</h2>
        <div className="card">
          {canManage ? (
            <form className="row" onSubmit={invite}>
              <div className="field">
                <label>Work email</label>
                <input type="email" required placeholder="name@company.com"
                       value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} />
              </div>
              <div className="field" style={{ maxWidth: 160 }}>
                <label>Role</label>
                <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                  <option value="viewer">Viewer — read only</option>
                  <option value="admin">Admin — operations</option>
                  {myRole === "owner" && <option value="owner">Owner — full access</option>}
                </select>
              </div>
              <button className="small" disabled={busy} style={{ width: "auto" }}>
                {busy ? "Inviting…" : "Send invite"}
              </button>
            </form>
          ) : (
            <div className="muted" style={{ fontSize: "var(--font-size-sm)" }}>
              Only an owner or admin can invite team members.
            </div>
          )}
        </div>

        <h2>Team members</h2>
        <div className="panel">
          {members === null ? (
            <div className="empty">Loading…</div>
          ) : members.length === 0 ? (
            <div className="empty">No members yet.</div>
          ) : (
            <table>
              <thead>
                <tr><th>Email</th><th>Role</th><th>Joined</th><th></th></tr>
              </thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.user_id}>
                    <td>{m.email}{m.is_you && <span className="muted"> (you)</span>}</td>
                    <td>
                      {myRole === "owner" && !m.is_you ? (
                        <select value={m.role} onChange={(e) => changeRole(m.user_id, e.target.value)}>
                          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                        </select>
                      ) : (
                        <span className="pill s-unk">{m.role}</span>
                      )}
                    </td>
                    <td className="mono">{fmt(m.joined)}</td>
                    <td style={{ textAlign: "right" }}>
                      {canManage && !m.is_you && (
                        <button className="btn-danger" onClick={() => remove(m.user_id)}>
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <h2>Pending invitations</h2>
        <div className="panel">
          {invites.length === 0 ? (
            <div className="empty">No pending invitations.</div>
          ) : (
            <table>
              <thead>
                <tr><th>Email</th><th>Role</th><th>Expires</th><th></th></tr>
              </thead>
              <tbody>
                {invites.map((i) => (
                  <tr key={i.id}>
                    <td>{i.email}</td>
                    <td><span className="pill s-unk">{i.role}</span></td>
                    <td className="mono">
                      {i.expired ? <span className="pill s-bad">expired</span> : fmt(i.expires_at)}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {canManage && (
                        <button className="btn-danger" onClick={() => revoke(i.id)}>Revoke</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
