"use client";

import { useCallback, useEffect, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { AdminNav, requirePlatformAdmin } from "../admin-shell";
import styles from "../admin.module.css";

const PROVIDER_TYPES = [
  { value: "ollama", label: "ollama (local)" },
  { value: "openai_compat", label: "openai_compat" },
  { value: "openai", label: "openai" },
  { value: "anthropic", label: "anthropic (via OpenAI-compatible gateway)" },
  { value: "gemini", label: "gemini (via OpenAI-compatible gateway)" },
];
const COST_CLASSES = ["free", "local", "standard", "premium"];
const MODES = ["instant", "thinking", "hive"];
const MODE_LABEL = { instant: "WatchLog Instant", thinking: "WatchLog Thinking", hive: "WatchLog Hive" };
const fmt = (v) => (v ? new Date(v).toLocaleString() : "Never");
const blankProvider = () => ({
  id: null, name: "", type: "ollama", endpoint: "", default_model: "",
  supports_text: true, supports_vision: false, supports_tools: false, supports_json: true,
  privacy: "LOCAL", external_egress: false, timeout_ms: 90000, max_output: 900, priority: 100,
  cost_class: "local", api_key: "", reason: "",
});
const box = { border: "1px solid var(--color-line-dark)", borderRadius: 10, padding: 14, background: "var(--color-canvas)" };

export default function AiModelsAdmin() {
  const [admin, setAdmin] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [audit, setAudit] = useState([]);
  const [form, setForm] = useState(blankProvider());
  const [modeDraft, setModeDraft] = useState({});
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const { data, error } = await supabase().rpc("wl_ai_admin_config");
    if (error) { setError(say(error)); return; }
    setCfg(data);
    const md = {};
    (data?.modes || []).forEach((m) => {
      md[m.mode] = {
        primary_provider_id: m.primary_provider_id || "", primary_model: m.primary_model || "",
        fallback_provider_id: m.fallback_provider_id || "", fallback_model: m.fallback_model || "",
        tertiary_provider_id: m.tertiary_provider_id || "", tertiary_model: m.tertiary_model || "",
        vision_provider_id: m.vision_provider_id || "", vision_model: m.vision_model || "",
        external_egress_allowed: !!m.external_egress_allowed,
      };
    });
    setModeDraft(md);
    const a = await supabase().rpc("wl_ai_route_audit", { p_limit: 25, p_site_id: null });
    if (!a.error) setAudit(a.data || []);
  }, []);

  useEffect(() => { (async () => {
    const guard = await requirePlatformAdmin();
    if (!guard) return;
    setAdmin(guard.admin);
    if (guard.admin.role !== "platform_owner") { location.replace("/admin/"); return; }
    await load();
  })(); }, [load]);

  const providers = cfg?.providers || [];
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const setMode = (mode, k, v) => setModeDraft((md) => ({ ...md, [mode]: { ...md[mode], [k]: v } }));

  async function saveProvider(e) {
    e.preventDefault();
    if (form.reason.trim().length < 4) { setError("A reason (4+ characters) is required for AI configuration changes."); return; }
    setBusy(true); setError(""); setNote("");
    const { error } = await supabase().rpc("wl_ai_provider_upsert", {
      p_id: form.id, p_name: form.name.trim(), p_type: form.type, p_endpoint: form.endpoint.trim(),
      p_default_model: form.default_model.trim() || null,
      p_supports_text: form.supports_text, p_supports_vision: form.supports_vision,
      p_supports_tools: form.supports_tools, p_supports_json: form.supports_json,
      p_privacy: form.privacy, p_external_egress: form.external_egress,
      p_timeout_ms: Number(form.timeout_ms) || 35000, p_max_output: Number(form.max_output) || 900,
      p_priority: Number(form.priority) || 100, p_cost_class: form.cost_class,
      p_api_key: form.api_key.trim() || null, p_reason: form.reason.trim(),
    });
    if (error) setError(say(error));
    else { setNote(form.id ? "Provider updated." : "Provider added."); setForm(blankProvider()); await load(); }
    setBusy(false);
  }
  function editProvider(p) {
    setForm({
      id: p.id, name: p.name, type: p.type, endpoint: p.endpoint, default_model: p.default_model || "",
      supports_text: p.supports?.text !== false, supports_vision: !!p.supports?.vision,
      supports_tools: !!p.supports?.tools, supports_json: p.supports?.json !== false,
      privacy: p.privacy, external_egress: !!p.external_egress, timeout_ms: p.timeout_ms,
      max_output: p.max_output, priority: p.priority, cost_class: p.cost_class, api_key: "", reason: "",
    });
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }
  async function toggleEnabled(p) {
    setBusy(true); setError("");
    const { error } = await supabase().rpc("wl_ai_provider_set_enabled", { p_id: p.id, p_enabled: !p.enabled, p_reason: p.enabled ? "disable" : "enable" });
    if (error) setError(say(error)); else { setNote(`${p.name} ${p.enabled ? "disabled" : "enabled"}.`); await load(); }
    setBusy(false);
  }
  async function rotateKey(p) {
    const key = prompt(`New API key for ${p.name} (stored in Vault, never shown again). Leave blank to clear:`);
    if (key === null) return;
    const why = prompt("Reason (4+ characters):") || "";
    if (why.trim().length < 4) { setError("A reason (4+ characters) is required."); return; }
    setBusy(true); setError("");
    const { error } = await supabase().rpc("wl_ai_provider_rotate_key", { p_id: p.id, p_api_key: key.trim() || null, p_reason: why.trim() });
    if (error) setError(say(error)); else { setNote("Key rotated."); await load(); }
    setBusy(false);
  }
  async function removeProvider(p) {
    const why = prompt(`Reason for deleting provider ${p.name}:`) || "";
    if (why.trim().length < 4) return;
    if (!confirm(`Delete ${p.name}? Any mode using it will fall back to deterministic guidance.`)) return;
    setBusy(true); setError("");
    const { error } = await supabase().rpc("wl_ai_provider_delete", { p_id: p.id, p_reason: why.trim() });
    if (error) setError(say(error)); else { setNote("Provider deleted."); await load(); }
    setBusy(false);
  }
  async function saveMode(mode) {
    const d = modeDraft[mode] || {};
    setBusy(true); setError(""); setNote("");
    const main = await supabase().rpc("wl_ai_mode_set", {
      p_mode: mode,
      p_primary_provider_id: d.primary_provider_id || null, p_primary_model: (d.primary_model || "").trim() || null,
      p_fallback_provider_id: d.fallback_provider_id || null, p_fallback_model: (d.fallback_model || "").trim() || null,
      p_vision_provider_id: d.vision_provider_id || null, p_vision_model: (d.vision_model || "").trim() || null,
      p_external_egress_allowed: !!d.external_egress_allowed, p_reason: "set "+mode+" routing",
    });
    if (main.error) { setError(say(main.error)); setBusy(false); return; }
    const tertiary = await supabase().rpc("wl_ai_mode_set_tertiary", {
      p_mode: mode, p_provider_id: d.tertiary_provider_id || null,
      p_model: (d.tertiary_model || "").trim() || null, p_reason: "set "+mode+" tertiary routing",
    });
    if (tertiary.error) setError(say(tertiary.error));
    else { setNote(MODE_LABEL[mode]+" routing saved."); await load(); }
    setBusy(false);
  }

  if (!admin) return <div className="shell"><main className="main"><p className="muted">Loading…</p></main></div>;

  return <div className="shell"><AdminNav active="AI & Models" admin={admin} /><main className="main">
    <div className={styles.head}><div>
      <h1>AI &amp; Models</h1>
      <p>Configure AI providers and the WatchLog modes they power. Changes take effect with no deployment. Provider keys live server-side in Vault — they are never shown here and never sent to a customer.</p>
    </div></div>
    {error && <div className="err">{error}</div>}
    {note && <div className="ok-note">{note}</div>}
    {cfg && !cfg.vault_available &&
      <div className="err">Secure secret storage (Vault) is unavailable in this environment. Providers can be defined but API keys cannot be saved until Vault is enabled.</div>}

    <section className={styles.card}>
      <h2>Providers</h2>
      <div className={styles.stack}>
        {providers.map((p) => <div className={styles.row} key={p.id}>
          <div>
            <strong>{p.name}</strong>
            <small>{p.type} · {p.endpoint} · {p.default_model || "no default model"} · {p.privacy}{p.external_egress ? " · egress" : ""} · {p.has_key ? `key ${p.key_hint || "set"}` : "no key"} · {(p.models || []).length} model(s)</small>
          </div>
          <div className={styles.actions}>
            <span className={styles.badge}>{p.health_status}</span>
            <span className={styles.badge}>{p.enabled ? "enabled" : "disabled"}</span>
            <button className="ghost small" disabled={busy} onClick={() => editProvider(p)}>Edit</button>
            <button className="ghost small" disabled={busy} onClick={() => toggleEnabled(p)}>{p.enabled ? "Disable" : "Enable"}</button>
            <button className="ghost small" disabled={busy} onClick={() => rotateKey(p)}>Rotate key</button>
            <button className="btn-danger" disabled={busy} onClick={() => removeProvider(p)}>Delete</button>
          </div>
        </div>)}
        {!providers.length && <div className={styles.empty}>No providers configured. Add one below. Until a mode has an enabled provider, WatchLog answers from verified data only (guided fallback).</div>}
      </div>
    </section>

    <section className={styles.card}>
      <h2>{form.id ? "Edit provider" : "Add provider"}</h2>
      <form className={styles.form} onSubmit={saveProvider}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <div><label>Name</label><input required value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="VI Ollama" /></div>
          <div><label>Type</label><select value={form.type} onChange={(e) => set("type", e.target.value)}>{PROVIDER_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></div>
        </div>
        <div><label>Endpoint</label><input required value={form.endpoint} onChange={(e) => set("endpoint", e.target.value)} placeholder="http://host:11434  or  https://api.host/v1" /></div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <div><label>Default model</label><input value={form.default_model} onChange={(e) => set("default_model", e.target.value)} placeholder="qwen3:8b" /></div>
          <div><label>Privacy</label><select value={form.privacy} onChange={(e) => set("privacy", e.target.value)}><option value="LOCAL">LOCAL (on our infrastructure)</option><option value="EXTERNAL">EXTERNAL (prompt leaves our control)</option></select></div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 14 }}>
          <div><label>Timeout (ms)</label><input type="number" value={form.timeout_ms} onChange={(e) => set("timeout_ms", e.target.value)} /></div>
          <div><label>Max output</label><input type="number" value={form.max_output} onChange={(e) => set("max_output", e.target.value)} /></div>
          <div><label>Priority</label><input type="number" value={form.priority} onChange={(e) => set("priority", e.target.value)} /></div>
          <div><label>Cost class</label><select value={form.cost_class} onChange={(e) => set("cost_class", e.target.value)}>{COST_CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}</select></div>
        </div>
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
          <label style={{ display: "flex", gap: 7, alignItems: "center", textTransform: "none" }}><input type="checkbox" checked={form.external_egress} onChange={(e) => set("external_egress", e.target.checked)} style={{ width: "auto" }} /> External egress</label>
          <label style={{ display: "flex", gap: 7, alignItems: "center", textTransform: "none" }}><input type="checkbox" checked={form.supports_vision} onChange={(e) => set("supports_vision", e.target.checked)} style={{ width: "auto" }} /> Vision</label>
          <label style={{ display: "flex", gap: 7, alignItems: "center", textTransform: "none" }}><input type="checkbox" checked={form.supports_tools} onChange={(e) => set("supports_tools", e.target.checked)} style={{ width: "auto" }} /> Tools</label>
          <label style={{ display: "flex", gap: 7, alignItems: "center", textTransform: "none" }}><input type="checkbox" checked={form.supports_json} onChange={(e) => set("supports_json", e.target.checked)} style={{ width: "auto" }} /> JSON</label>
        </div>
        <div><label>API key {form.id ? "(leave blank to keep the current key)" : "(optional; blank for a local no-auth endpoint)"}</label><input type="password" autoComplete="new-password" value={form.api_key} onChange={(e) => set("api_key", e.target.value)} placeholder={cfg?.vault_available ? "stored in Vault, never shown again" : "Vault unavailable"} /></div>
        <div><label>Reason (audited)</label><input required minLength="4" value={form.reason} onChange={(e) => set("reason", e.target.value)} placeholder="Why this change?" /></div>
        <div style={{ display: "flex", gap: 10 }}>
          <button disabled={busy}>{busy ? "Saving…" : form.id ? "Update provider" : "Add provider"}</button>
          {form.id && <button type="button" className="ghost" onClick={() => setForm(blankProvider())}>Cancel edit</button>}
        </div>
      </form>
    </section>

    <section className={styles.card}>
      <h2>WatchLog modes</h2>
      <p className="muted">Each mode resolves to a provider and model at request time — customers never see provider or model names. A mode&apos;s external egress still cannot override a site set to local-only.</p>
      <div className={styles.stack}>
        {MODES.map((mode) => { const d = modeDraft[mode] || {}; return <div style={box} key={mode}>
          <strong>{MODE_LABEL[mode]}</strong>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, margin: "12px 0" }}>
            <label style={{ fontSize: 12 }}>Primary provider
              <select value={d.primary_provider_id || ""} onChange={(e) => setMode(mode, "primary_provider_id", e.target.value)}>
                <option value="">— none (guided fallback) —</option>
                {providers.map((p) => <option key={p.id} value={p.id}>{p.name}{p.enabled ? "" : " (disabled)"}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>Primary model
              <input value={d.primary_model || ""} onChange={(e) => setMode(mode, "primary_model", e.target.value)} placeholder="defaults to the provider model" />
            </label>
            <label style={{ fontSize: 12 }}>Fallback provider
              <select value={d.fallback_provider_id || ""} onChange={(e) => setMode(mode, "fallback_provider_id", e.target.value)}>
                <option value="">— none —</option>
                {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>Fallback model
              <input value={d.fallback_model || ""} onChange={(e) => setMode(mode, "fallback_model", e.target.value)} placeholder="optional" />
            </label>
            <label style={{ fontSize: 12 }}>Tertiary provider
              <select value={d.tertiary_provider_id || ""} onChange={(e) => setMode(mode, "tertiary_provider_id", e.target.value)}>
                <option value="">— none —</option>{providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>Tertiary model
              <input value={d.tertiary_model || ""} onChange={(e) => setMode(mode, "tertiary_model", e.target.value)} placeholder="optional" />
            </label>
            <label style={{ fontSize: 12 }}>Image provider
              <select value={d.vision_provider_id || ""} onChange={(e) => setMode(mode, "vision_provider_id", e.target.value)}>
                <option value="">— image analysis disabled —</option>
                {providers.filter((p) => p.supports?.vision || (p.models || []).some((m) => m.supports?.vision)).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>Image model
              <input value={d.vision_model || ""} onChange={(e) => setMode(mode, "vision_model", e.target.value)} placeholder="vision-capable model id" />
            </label>
          </div>
          <label style={{ display: "flex", gap: 7, alignItems: "center", fontSize: 12 }}>
            <input type="checkbox" checked={!!d.external_egress_allowed} onChange={(e) => setMode(mode, "external_egress_allowed", e.target.checked)} style={{ width: "auto" }} /> Allow external egress for this mode
          </label>
          <div style={{ marginTop: 12 }}><button className="ghost small" disabled={busy} onClick={() => saveMode(mode)}>Save {MODE_LABEL[mode]}</button></div>
        </div>; })}
      </div>
    </section>

    <section className={styles.card}>
      <h2>Recent routing decisions</h2>
      <p className="muted">Route audit is visible to platform administrators only. Provider and model identities appear here and in the audit log, never to a customer.</p>
      <div className={styles.stack}>
        {audit.map((r) => <div className={styles.row} key={r.id}>
          <div>
            <strong>{(r.mode || "—")} · {r.route}</strong>
            <small>{fmt(r.created_at)} · {r.provider_name || "no provider"} {r.model || ""} · egress {r.egress || "n/a"} · {r.latency_ms != null ? `${r.latency_ms} ms` : "—"} · {r.outcome}</small>
          </div>
          {r.used_fallback && <span className={styles.badge}>fallback</span>}
        </div>)}
        {!audit.length && <div className={styles.empty}>No routing decisions recorded yet.</div>}
      </div>
    </section>
  </main></div>;
}
