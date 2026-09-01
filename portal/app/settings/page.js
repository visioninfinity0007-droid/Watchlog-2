"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant, setupPill } from "../shell";

const PLANS = [
  ["starter", "Starter"],
  ["growth", "Growth"],
  ["enterprise", "Enterprise"],
];

const INSTALLER_URL = process.env.NEXT_PUBLIC_INSTALLER_URL || "";

function fmt(ts) { return ts ? new Date(ts).toLocaleString() : "—"; }

export default function Settings() {
  const [email, setEmail] = useState("");
  const [trial, setTrial] = useState(null);
  const [sites, setSites] = useState(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [newSite, setNewSite] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const g = await requireTenant();
    if (!g) return;
    setEmail(g.session.user.email || "");
    const sb = supabase();
    const [t, s] = await Promise.all([sb.rpc("wl_trial_status"), sb.rpc("wl_sites")]);
    if (t.error) { setErr(say(t.error)); return; }
    setTrial(t.data || {});
    setSites(s.data || []);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function choosePlan(plan) {
    setErr(""); setNote("");
    const { data, error } = await supabase().rpc("wl_set_plan", { p_plan: plan });
    if (error) { setErr(say(error)); return; }
    setNote(data?.note || "Updated.");
    load();
  }
  async function addSite(e) {
    e.preventDefault();
    setBusy(true); setErr(""); setNote("");
    const { error } = await supabase().rpc("wl_add_site", { p_name: newSite.trim() });
    setBusy(false);
    if (error) { setErr(say(error)); return; }
    setNote("Site added."); setNewSite("");
    load();
  }
  async function issueCode(siteId) {
    setErr(""); setNote("");
    const { data, error } = await supabase().rpc("wl_issue_code", { p_site_id: siteId, p_days: 14 });
    if (error) { setErr(say(error)); return; }
    setNote(`New enrollment code: ${data.code} (single use, valid 14 days).`);
    load();
  }

  const t = trial || {};
  const statusCls = t.status === "active" ? "s-ok"
    : t.status === "trialing" ? "s-warn"
    : t.status === "past_due" ? "s-bad" : "s-unk";

  return (
    <div className="shell">
      <Nav active="Settings" email={email} />
      <main className="main">
        {err && <div className="err">{err}</div>}
        {note && <div className="ok-note">{note}</div>}

        <h2>Plan &amp; billing</h2>
        <div className="card">
          {trial === null ? <span className="muted">Loading…</span> : (
            <>
              <div className="row" style={{ alignItems: "center" }}>
                <div><div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>Plan</div>
                  <b style={{ fontSize: "var(--font-size-lg)", textTransform: "capitalize" }}>{t.plan || "trial"}</b></div>
                <div><div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>Status</div>
                  <span className={"pill " + statusCls}>{t.status || "—"}</span></div>
                {t.status === "trialing" && (
                  <div><div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>Trial</div>
                    <b>{t.days_left ?? 0} days left</b>
                    <div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>ends {t.trial_ends_at ? new Date(t.trial_ends_at).toLocaleDateString() : "—"}</div></div>
                )}
              </div>
              <div style={{ marginTop: "var(--space-4)" }}>
                <div className="muted" style={{ fontSize: "var(--font-size-sm)", marginBottom: 8 }}>
                  Choose a plan (activates once payment is confirmed at checkout):
                </div>
                <div className="row">
                  {PLANS.map(([v, l]) => (
                    <button key={v} className="ghost small" style={{ width: "auto" }}
                            onClick={() => choosePlan(v)}>{l}</button>
                  ))}
                  <button className="btn-danger" onClick={() => choosePlan("trial")}>
                    Downgrade to free
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

        <h2>Sites</h2>
        <div className="card">
          <form className="row" onSubmit={addSite}>
            <div className="field"><label>Add a site</label>
              <input required placeholder="Warehouse, Branch 2…" value={newSite}
                     onChange={(e) => setNewSite(e.target.value)} /></div>
            <button className="small" disabled={busy} style={{ width: "auto" }}>
              {busy ? "Adding…" : "Add site"}
            </button>
          </form>
        </div>
        <div className="panel">
          {sites === null ? <div className="empty">Loading…</div> :
           sites.length === 0 ? <div className="empty">No sites yet.</div> : (
            <table>
              <thead>
                <tr><th>Site</th><th>Status</th><th>Cameras</th><th className="hide-sm">Last event</th><th>Enrollment code</th></tr>
              </thead>
              <tbody>
                {sites.map((s) => {
                  const [slabel, scls] = setupPill(s.setup_state, s.online);
                  return (
                  <tr key={s.id}>
                    <td>{s.name}<div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>{s.timezone}</div></td>
                    <td><span className={"pill " + scls}>{slabel}</span></td>
                    <td className="mono">{s.cameras}</td>
                    <td className="muted hide-sm">{fmt(s.last_event)}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {s.open_code
                        ? <span className="mono">{s.open_code}</span>
                        : <span className="muted">none</span>}
                      {" "}
                      <button className="ghost small" onClick={() => issueCode(s.id)}>New code</button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <h2>Install the agent</h2>
        <div className="card">
          <p style={{ marginTop: 0, fontSize: "var(--font-size-sm)" }}>
            WatchLog runs on a Windows PC on the same network as your recorder. Install it there, enter
            a site's enrollment code above, and it finds the recorder and starts reporting.
          </p>
          {INSTALLER_URL ? (
            <a className="brandlink" href={INSTALLER_URL}
               style={{ display: "inline-block" }}>
              <button style={{ width: "auto" }}>Download for Windows</button>
            </a>
          ) : (
            <div className="muted" style={{ fontSize: "var(--font-size-sm)" }}>
              The signed installer download will appear here once published. In the meantime your setup
              engineer will provide <span className="mono">WatchLog-Setup.exe</span>.
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
