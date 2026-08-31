"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";

const CHANNELS = [
  ["whatsapp", "WhatsApp"],
  ["email", "Email"],
  ["both", "WhatsApp + Email"],
];

function statusPill(s) {
  const cls = s === "sent" ? "s-ok" : s === "failed" ? "s-bad" : "s-unk";
  return <span className={"pill " + cls}>{s}</span>;
}

export default function Reports() {
  const [email, setEmail] = useState("");
  const [recips, setRecips] = useState(null);
  const [sites, setSites] = useState([]);
  const [deliveries, setDeliveries] = useState([]);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ destination: "", channel: "whatsapp", name: "", site_id: "" });

  const load = useCallback(async () => {
    const g = await requireTenant();
    if (!g) return;
    setEmail(g.session.user.email || "");
    const sb = supabase();
    const [r, s, d] = await Promise.all([
      sb.rpc("wl_recipients"), sb.rpc("wl_sites"), sb.rpc("wl_deliveries", { p_days: 30 }),
    ]);
    if (r.error) { setErr(say(r.error)); return; }
    setRecips(r.data || []);
    setSites(s.data || []);
    setDeliveries(d.data || []);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function add(e) {
    e.preventDefault();
    setBusy(true); setErr(""); setNote("");
    const { data, error } = await supabase().rpc("wl_add_recipient", {
      p_destination: form.destination.trim(),
      p_channel: form.channel,
      p_name: form.name.trim() || null,
      p_site_id: form.site_id || null,
    });
    setBusy(false);
    if (error) { setErr(say(error)); return; }
    if (data && data.created === false) setNote(data.note || "That recipient already exists.");
    else { setNote("Recipient added."); setForm({ destination: "", channel: "whatsapp", name: "", site_id: "" }); }
    load();
  }
  async function toggle(id, enabled) {
    await supabase().rpc("wl_set_recipient", { p_id: id, p_enabled: !enabled });
    load();
  }
  async function remove(id) {
    await supabase().rpc("wl_remove_recipient", { p_id: id });
    load();
  }

  return (
    <div className="shell">
      <Nav active="Reports" email={email} />
      <main className="main">
        {err && <div className="err">{err}</div>}
        {note && <div className="ok-note">{note}</div>}

        <h2>Add a recipient</h2>
        <div className="card">
          <form className="row" onSubmit={add}>
            <div className="field" style={{ maxWidth: 170 }}>
              <label>Channel</label>
              <select value={form.channel}
                      onChange={(e) => setForm({ ...form, channel: e.target.value })}>
                {CHANNELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div className="field">
              <label>{form.channel === "email" ? "Email address" :
                      form.channel === "both" ? "Number, then edit for email" : "WhatsApp number"}</label>
              <input required value={form.destination}
                     placeholder={form.channel === "email" ? "name@company.com" : "923001234567"}
                     onChange={(e) => setForm({ ...form, destination: e.target.value })} />
            </div>
            <div className="field" style={{ maxWidth: 150 }}>
              <label>Name (optional)</label>
              <input value={form.name} placeholder="Control room"
                     onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="field" style={{ maxWidth: 170 }}>
              <label>Site</label>
              <select value={form.site_id}
                      onChange={(e) => setForm({ ...form, site_id: e.target.value })}>
                <option value="">All sites</option>
                {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <button className="small" disabled={busy} style={{ width: "auto" }}>
              {busy ? "Adding…" : "Add"}
            </button>
          </form>
          <p className="muted" style={{ fontSize: "var(--font-size-xs)", margin: "8px 0 0" }}>
            WhatsApp numbers are international, digits only (e.g. 923001234567). The daily summary
            goes out each morning per site timezone.
          </p>
        </div>

        <h2>Recipients</h2>
        <div className="panel">
          {recips === null ? <div className="empty">Loading…</div> :
           recips.length === 0 ? <div className="empty">No recipients yet — add one above to start the daily report.</div> : (
            <table>
              <thead>
                <tr><th>Destination</th><th>Channel</th><th>Site</th><th>Status</th><th></th></tr>
              </thead>
              <tbody>
                {recips.map((r) => (
                  <tr key={r.id}>
                    <td>{r.destination}{r.name && <div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>{r.name}</div>}</td>
                    <td>{CHANNELS.find(([v]) => v === r.channel)?.[1] || r.channel}</td>
                    <td className="muted">{r.site || "All sites"}</td>
                    <td>
                      <span className={"pill " + (r.enabled ? "s-ok" : "s-unk")}>
                        {r.enabled ? "active" : "paused"}
                      </span>
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <button className="ghost small" onClick={() => toggle(r.id, r.enabled)}>
                        {r.enabled ? "Pause" : "Resume"}
                      </button>{" "}
                      <button className="btn-danger" onClick={() => remove(r.id)}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <h2>Delivery history</h2>
        <div className="panel">
          {deliveries.length === 0 ? (
            <div className="empty">No reports sent yet. Once a recipient is set, the daily summary
              is logged here every morning.</div>
          ) : (
            <table>
              <thead>
                <tr><th>Date</th><th>Site</th><th>Channel</th><th>To</th><th>Status</th><th>Events</th></tr>
              </thead>
              <tbody>
                {deliveries.map((d, i) => (
                  <tr key={i}>
                    <td className="mono">{d.date}</td>
                    <td>{d.site}</td>
                    <td>{d.channel}</td>
                    <td className="muted">{d.destination}</td>
                    <td>{statusPill(d.status)}{d.error && <div className="muted" style={{ fontSize: "var(--font-size-xs)" }}>{d.error}</div>}</td>
                    <td className="mono">{d.events ?? "—"}</td>
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
