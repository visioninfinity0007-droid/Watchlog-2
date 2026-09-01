"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant, setupPill } from "../shell";

const INSTALLER_URL = process.env.NEXT_PUBLIC_INSTALLER_URL || "";
const BILLING_URL = process.env.NEXT_PUBLIC_BILLING_URL || "";
const BILLING_PROVIDER = process.env.NEXT_PUBLIC_BILLING_PROVIDER || "mock";
const MARKETING_URL = process.env.NEXT_PUBLIC_MARKETING_URL || "";

function fmt(ts) { return ts ? new Date(ts).toLocaleString() : "—"; }
function money(minor, cur) {
  if (minor == null) return "—";
  return `${cur || "PKR"} ${(minor / 100).toLocaleString()}`;
}

export default function Settings() {
  const [email, setEmail] = useState("");
  const [trial, setTrial] = useState(null);
  const [sites, setSites] = useState(null);
  const [billing, setBilling] = useState(null);
  const [plans, setPlans] = useState([]);
  const [entitlement, setEntitlement] = useState(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [newSite, setNewSite] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const g = await requireTenant();
    if (!g) return;
    setEmail(g.session.user.email || "");
    const sb = supabase();
    const [t, s, b, p, e] = await Promise.all([
      sb.rpc("wl_trial_status"), sb.rpc("wl_sites"),
      sb.rpc("wl_billing_overview"), sb.rpc("wl_billing_plans"),
      sb.rpc("wl_entitlement"),
    ]);
    if (t.error) { setErr(say(t.error)); return; }
    setTrial(t.data || {});
    setSites(s.data || []);
    setBilling(b.data || {});
    setPlans(p.data || []);
    setEntitlement(e.data || {});
  }, []);

  useEffect(() => { load(); }, [load]);

  async function startCheckout(plan) {
    setErr(""); setNote("");
    const { data, error } = await supabase().rpc("wl_billing_start_checkout",
      { p_plan: plan, p_provider: BILLING_PROVIDER });
    if (error) { setErr(say(error)); return; }
    if (BILLING_URL && data?.checkout_path) {
      window.location.href = BILLING_URL + data.checkout_path;   // hosted checkout
      return;
    }
    setNote(data?.note || "Checkout created. Complete payment to activate.");
    load();
  }
  async function cancelSub() {
    setErr(""); setNote("");
    const { data, error } = await supabase().rpc("wl_billing_cancel");
    if (error) { setErr(say(error)); return; }
    setNote(data?.note || "Cancellation requested.");
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
                {billing?.subscription?.current_period_end && (
                  <div><div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>Renews</div>
                    <b>{new Date(billing.subscription.current_period_end).toLocaleDateString()}</b>
                    {billing.subscription.cancel_at_period_end &&
                      <div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>cancels at period end</div>}</div>
                )}
              </div>

              {entitlement && (
                <div style={{ marginTop: "var(--space-4)" }}>
                  <span className={"pill " + (entitlement.reporting_enabled ? "s-ok" : "s-bad")}>
                    Daily reports {entitlement.reporting_enabled ? "active" : "paused"}
                  </span>
                  {!entitlement.reporting_enabled && (
                    <span className="muted" style={{ fontSize: "var(--font-size-sm)", marginLeft: 8 }}>
                      {entitlement.reason} — subscribe to resume. Your recorded events are kept.
                    </span>
                  )}
                </div>
              )}

              <div style={{ marginTop: "var(--space-5)" }}>
                <div className="muted" style={{ fontSize: "var(--font-size-sm)", marginBottom: 8 }}>
                  {t.status === "active" ? "Change your plan" : "Choose a plan"} — you pay on the
                  secure checkout; your plan activates when payment is confirmed.
                </div>
                <div className="row">
                  {plans.map((p) => (
                    <button key={p.plan} className="ghost small" style={{ width: "auto" }}
                            onClick={() => startCheckout(p.plan)}>
                      <span style={{ textTransform: "capitalize" }}>{p.plan}</span>
                      {" — "}{money(p.amount_minor, p.currency)}/mo
                    </button>
                  ))}
                  <span className="muted" style={{ fontSize: "var(--font-size-sm)", alignSelf: "center" }}>
                    Enterprise — <a href={MARKETING_URL ? MARKETING_URL + "/contact/" : "#"}>Talk to us</a>
                  </span>
                  {t.status === "active" && (
                    <button className="btn-danger" onClick={cancelSub}>Cancel subscription</button>
                  )}
                </div>
                {plans.some((p) => p.is_draft) && (
                  <p className="muted" style={{ fontSize: "var(--font-size-xs)", margin: "8px 0 0" }}>
                    Pricing shown is provisional pending final confirmation.
                  </p>
                )}
              </div>

              {(billing?.transactions || []).length > 0 && (
                <div style={{ marginTop: "var(--space-5)" }}>
                  <div className="muted" style={{ fontSize: "var(--font-size-xs)", textTransform: "uppercase",
                       letterSpacing: ".06em", fontWeight: 700, marginBottom: 6 }}>Payment history</div>
                  <table>
                    <tbody>
                      {billing.transactions.map((x, i) => (
                        <tr key={i}>
                          <td className="mono">{new Date(x.created_at).toLocaleDateString()}</td>
                          <td style={{ textTransform: "capitalize" }}>{x.plan || "—"}</td>
                          <td className="mono">{money(x.amount_minor, x.currency)}</td>
                          <td><span className={"pill " + (x.status === "succeeded" ? "s-ok" : x.status === "failed" ? "s-bad" : "s-unk")}>{x.status}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
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
