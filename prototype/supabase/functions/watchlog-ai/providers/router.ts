// WatchLog AI router — the deterministic decision layer between a tenant/site-authorized request and
// a model. It resolves a WatchLog mode (Instant/Thinking/Hive) to provider configs from the DB,
// applies the site's data-egress policy AFTER resolution (so an admin-configured cloud model can
// never override a site's local-only restriction), fails closed on invalid config, and always keeps
// the verified-data guided fallback as the floor. It never sees credentials-as-authority: the MODEL
// is never the boundary. These helpers are pure so the policy is testable in isolation.

import { ProviderConfig, ProviderType, PrivacyClass } from "./types.ts";

// A genuinely local/private endpoint — loopback, RFC1918 private ranges, or an internal hostname
// (docker service name, *.local, *.internal). A public IP or public hostname is NOT local, even on
// :11434 — network location is decided here, never inferred from a port or admin metadata.
export function isLocalEndpoint(endpoint: string): boolean {
  let host = "";
  try {
    host = new URL(/^[a-z]+:\/\//i.test(endpoint) ? endpoint : `http://${endpoint}`).hostname.toLowerCase();
  } catch { return false; }
  if (!host) return false;
  if (host === "localhost" || host === "::1" || host.endsWith(".local") || host.endsWith(".internal")) return true;
  if (/^127\./.test(host)) return true;                       // loopback
  if (/^10\./.test(host)) return true;                        // RFC1918
  if (/^192\.168\./.test(host)) return true;                  // RFC1918
  if (/^172\.(1[6-9]|2[0-9]|3[0-1])\./.test(host)) return true; // RFC1918
  if (!host.includes(".") && !/^\d/.test(host)) return true;  // bare internal service name (e.g. "ollama")
  return false;                                               // public IP or public hostname
}

export type AiMode = "instant" | "thinking" | "hive";
export type RouteKind = "no_model" | "ai_primary" | "ai_fallback" | "guided_fallback";
export type EgressDecision = "local" | "external" | "blocked_local_only" | "n/a";
export type RouteOutcome =
  | "ok" | "deterministic" | "customer_boundary" | "no_provider_configured" | "config_invalid"
  | "egress_blocked" | "all_providers_failed" | "router_error";

// The audit envelope — everything the platform admin / eval needs, and nothing the browser gets.
export interface RouteAudit {
  mode: AiMode;
  route: RouteKind;
  provider_id: string | null;
  provider_name: string | null;
  model: string | null;
  used_fallback: boolean;
  egress: EgressDecision;
  latency_ms: number;
  candidates_tried: number;
  tool_calls: string[];
  outcome: RouteOutcome;
}

const VALID_MODES = new Set<AiMode>(["instant", "thinking", "hive"]);
const VALID_TYPES = new Set<ProviderType>(["ollama", "openai_compat", "openai", "anthropic", "gemini"]);

export function normalizeMode(raw: unknown): AiMode {
  const m = String(raw ?? "").trim().toLowerCase();
  return VALID_MODES.has(m as AiMode) ? (m as AiMode) : "instant";
}

// Fail-closed: a structurally invalid resolved provider becomes null (skip it), NEVER a guess. A
// missing endpoint/model or an unknown type is a misconfiguration, not something to paper over.
export function dbProviderToConfig(db: unknown): ProviderConfig | null {
  if (!db || typeof db !== "object") return null;
  const o = db as Record<string, unknown>;
  const type = String(o.type ?? "");
  const endpoint = String(o.endpoint ?? "").trim();
  const model = String(o.model ?? "").trim();
  const id = String(o.id ?? "").trim();
  if (!id || !VALID_TYPES.has(type as ProviderType) || !endpoint || !model) return null;
  // Network-safe classification, independent of (and overriding) admin metadata:
  //  - a `:cloud` model is ALWAYS external egress;
  //  - a provider may be LOCAL only if its endpoint is a genuinely local/private host. A public
  //    endpoint claimed LOCAL is downgraded to EXTERNAL so it can never bypass a local-only site.
  const cloudModel = /:cloud$/i.test(model);
  const genuinelyLocal = o.privacy !== "EXTERNAL" && !cloudModel && isLocalEndpoint(endpoint);
  const privacy: PrivacyClass = genuinelyLocal ? "LOCAL" : "EXTERNAL";
  const externalEgress = genuinelyLocal ? !!o.external_egress : true;
  return {
    id,
    name: String(o.name ?? "provider"),
    type: type as ProviderType,
    endpoint,
    model,
    apiKey: o.api_key ? String(o.api_key) : undefined,
    supportsText: o.supports_text !== false,
    supportsVision: !!o.supports_vision,
    supportsTools: !!o.supports_tools,
    supportsJson: o.supports_json !== false,
    privacy,
    externalEgress,
    timeoutMs: Number(o.timeout_ms) > 0 ? Number(o.timeout_ms) : 35000,
    maxOutput: Number(o.max_output) > 0 ? Number(o.max_output) : 900,
  };
}

// A provider that sends prompt content off our infrastructure.
export function isExternal(cfg: ProviderConfig): boolean {
  return cfg.privacy === "EXTERNAL" || cfg.externalEgress === true;
}

// Egress gate — applied AFTER mode resolution. A LOCAL provider is always allowed. An EXTERNAL
// provider requires BOTH the site to allow external processing AND the mode to permit external
// egress. The site flag is tenant-owned and checked here, so provider/mode admin config can never
// bypass a local-only site.
export function egressAllowed(cfg: ProviderConfig, siteAllowsExternal: boolean, modeExternalAllowed: boolean): boolean {
  if (!isExternal(cfg)) return true;
  return siteAllowsExternal === true && modeExternalAllowed === true;
}

// NO_MODEL routing: WatchLog answers canonical health/status/coverage/count questions from verified
// data without spending an LLM call (faster, cheaper, and no prompt egress). Deliberately
// conservative — anything that asks to explain/recommend/configure keeps the model in the loop.
export function noModelIntent(prompt: string): boolean {
  const s = String(prompt || "").toLowerCase();
  if (!s) return false;
  if (/\b(explain|why|recommend|suggest|advise|should i|how do i|help me|set ?up|configure|what can|plan|draft|write|compare|summar)/.test(s)) {
    return false; // wants reasoning / generation — keep the model
  }
  return /\b(online|offline|not recording|are (my|the) cameras|which cameras|cameras (are )?(down|offline)|camera health|site health|health status|system status|is the site (online|up|healthy)|monitoring coverage|unverified time|recovered (time|footage)|how many (cameras|incidents|sites|events)|recorder model|what recorder|is .+ (online|offline|recording))\b/.test(s);
}

// ---------------------------------------------------------------------
// Evidence retrieval policy (Phase 6-7). The router loads scoped evidence only for evidence-intent
// prompts on a MODEL route — a NO_MODEL/status question never touches the evidence workspace. Image
// bytes are attached to a provider prompt only when that provider passes the egress gate, so a
// local-only site's images can never reach an external model.
// ---------------------------------------------------------------------
export function isEvidenceIntent(prompt: string): boolean {
  const s = String(prompt || "").toLowerCase();
  return /\b(what happened|happened|last night|overnight|this morning|yesterday|footage|clip|snapshot|show me|who was|any (activity|movement|one|body|intrusion)|intrud|break.?in|around the|near the|at the|armoury|armory|entrance|loading|perimeter|gate|door|camera \d)\b/.test(s);
}

// Remove decrypted image bytes from an evidence structure (keep all metadata). Used when the chosen
// provider must NOT receive images (external provider that the egress policy does not permit).
export function stripEvidenceImages(evidence: any): any {
  if (!evidence || typeof evidence !== "object") return evidence;
  const bundles = Array.isArray(evidence.bundles) ? evidence.bundles.map((b: any) => ({
    ...b,
    snapshots: Array.isArray(b.snapshots)
      ? b.snapshots.map((s: any) => ({ id: s.id, captured_at: s.captured_at, camera_id: s.camera_id,
          evidence_class: s.evidence_class, content_type: s.content_type, image_omitted: true }))
      : b.snapshots,
  })) : evidence.bundles;
  return { ...evidence, images_withheld: true, bundles };
}

// Resolve the time window from the prompt, in the site's timezone. Boundaries are the site-local day;
// exact DST-midnight edges are irrelevant for evidence windowing.
export function resolveEvidenceWindow(prompt: string, now: Date, tz: string): { from: string; to: string; label: string } {
  const s = String(prompt || "").toLowerCase();
  const offset = (() => {
    try {
      const dtf = new Intl.DateTimeFormat("en-US", { timeZone: tz || "UTC", hour12: false,
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const p: Record<string, string> = {};
      for (const part of dtf.formatToParts(now)) p[part.type] = part.value;
      const asUTC = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
      return asUTC - now.getTime();       // ms to add to a UTC instant to get local wall time
    } catch { return 0; }
  })();
  const localNow = new Date(now.getTime() + offset);
  const midnightUTC = Date.UTC(localNow.getUTCFullYear(), localNow.getUTCMonth(), localNow.getUTCDate()) - offset;
  const H = 3600_000, D = 86400_000;
  let from: number, to: number, label: string;
  if (/last night|overnight/.test(s)) { from = midnightUTC - 6 * H; to = midnightUTC + 6 * H; label = "last night"; }
  else if (/this morning/.test(s)) { from = midnightUTC + 5 * H; to = midnightUTC + 12 * H; label = "this morning"; }
  else if (/yesterday/.test(s)) { from = midnightUTC - D; to = midnightUTC; label = "yesterday"; }
  else if (/today/.test(s)) { from = midnightUTC; to = now.getTime(); label = "today"; }
  else { from = now.getTime() - D; to = now.getTime(); label = "last 24h"; }
  return { from: new Date(from).toISOString(), to: new Date(to).toISOString(), label };
}

// Match a camera the prompt names (by name or purpose). Returns the camera id, or null for all cameras.
export function resolveCameraId(prompt: string, cameras: any[]): string | null {
  const s = String(prompt || "").toLowerCase();
  for (const c of Array.isArray(cameras) ? cameras : []) {
    const name = String(c?.name || "").toLowerCase().trim();
    const purpose = String(c?.purpose || "").toLowerCase().trim();
    if ((name && s.includes(name)) || (purpose && purpose.length > 2 && s.includes(purpose))) return c.id;
  }
  // common synonyms -> purpose
  const syn: Record<string, string> = { armoury: "armory", "front door": "entrance", gate: "entrance" };
  for (const [word, p] of Object.entries(syn)) {
    if (s.includes(word)) { const hit = (cameras || []).find((c: any) => String(c?.purpose || "").toLowerCase().includes(p)); if (hit) return hit.id; }
  }
  return null;
}

// A compact, grounded summary of an evidence result (timestamps / cameras / detections) — usable by
// the deterministic floor so even a NO-provider deployment answers an evidence question truthfully.
export function evidenceSummary(evidence: any): { text: string; events: number; cameras: string[]; span: string | null } {
  const index: any[] = Array.isArray(evidence?.index) ? evidence.index : [];
  const bundles: any[] = Array.isArray(evidence?.bundles) ? evidence.bundles : [];
  const events = new Set(index.map((e) => e.event_ref)).size;
  const cams = [...new Set(index.map((e) => e.camera_id).filter(Boolean))].map(String);
  const times = index.map((e) => e.captured_at).filter(Boolean).sort();
  const span = times.length ? `${times[0]} … ${times[times.length - 1]}` : null;
  const labels = new Set<string>();
  for (const b of bundles) for (const d of (Array.isArray(b?.detections) ? b.detections : []))
    for (const x of (Array.isArray(d) ? d : [d])) if (x?.label) labels.add(String(x.label));
  const detTxt = labels.size ? ` Detected: ${[...labels].slice(0, 6).join(", ")}.` : "";
  const text = events
    ? `WatchLog found ${events} event${events === 1 ? "" : "s"} with retained evidence${cams.length ? ` across ${cams.length} camera${cams.length === 1 ? "" : "s"}` : ""} in the requested window (${evidence?.window?.label || "window"}).${detTxt}`
    : `WatchLog has no retained evidence for the requested window (${evidence?.window?.label || "window"}). Unverified monitoring time is never reported as "no activity".`;
  return { text, events, cameras: cams, span };
}

// Two-stage retrieval orchestration, dependency-injected on an `rpc` caller so it is testable without
// a live stack. Stage 1 searches the compact index; stage 2 loads full bundles for only the few
// relevant events. Returns null for a non-evidence prompt (so a NO_MODEL/status query never runs it).
export async function retrieveEvidence(
  rpc: (name: string, args: Record<string, unknown>) => Promise<{ ok: boolean; data?: any }>,
  prompt: string, siteId: string, ctx: any, now: Date,
): Promise<any | null> {
  if (!isEvidenceIntent(prompt)) return null;
  const tz = ctx?.site?.timezone || "UTC";
  const w = resolveEvidenceWindow(prompt, now, tz);
  const cameraId = resolveCameraId(prompt, ctx?.cameras || []);
  const idx = await rpc("wl_ai_evidence_index", { p_site_id: siteId, p_from: w.from, p_to: w.to, p_camera_id: cameraId });
  const index = idx.ok && Array.isArray(idx.data) ? idx.data : [];
  const eventRefs = [...new Set(index.map((e: any) => e.event_ref).filter(Boolean))].slice(0, 4);
  const bundles: any[] = [];
  for (const ref of eventRefs) {
    const b = await rpc("wl_ai_evidence_bundle", { p_site_id: siteId, p_event_ref: ref });
    if (b.ok && b.data?.found) bundles.push(b.data);
  }
  return { window: w, camera_id: cameraId, index: index.slice(0, 20), bundles };
}

// A candidate provider in priority order, tagged as primary or a configured fallback.
export interface Candidate {
  cfg: ProviderConfig;
  isFallback: boolean;
  compat: boolean; // true only for the legacy env bridge used when a mode is unconfigured
}

// Build the ordered candidate list from a resolved mode (+ optional legacy env bridge). Invalid
// provider configs are dropped here (fail-closed) rather than attempted.
export function buildCandidates(resolvedMode: any, envCfg: ProviderConfig | null): {
  candidates: Candidate[];
  modeExternalAllowed: boolean;
  primaryInvalid: boolean;
} {
  const candidates: Candidate[] = [];
  const modeExternalAllowed = !!resolvedMode?.external_egress_allowed;
  const primary = dbProviderToConfig(resolvedMode?.primary);
  const fallback = dbProviderToConfig(resolvedMode?.fallback);
  // Optional third layer (0109). Every free provider tier is rate-capped, so two layers is one
  // outage away from the deterministic floor. A tertiary is tried last and is subject to the
  // SAME egress gate as the others — depth in the chain never buys a provider extra permission.
  const tertiary = dbProviderToConfig(resolvedMode?.tertiary);
  // primaryInvalid: the mode names a primary provider but it did not resolve to a valid config.
  const primaryInvalid = !!(resolvedMode?.configured) && resolvedMode?.primary != null && primary === null;
  if (primary) candidates.push({ cfg: primary, isFallback: false, compat: false });
  if (fallback) candidates.push({ cfg: fallback, isFallback: true, compat: false });
  if (tertiary) candidates.push({ cfg: tertiary, isFallback: true, compat: false });
  // Legacy env bridge ONLY when the mode is entirely unconfigured, so it never masks a broken DB config.
  if (candidates.length === 0 && !resolvedMode?.configured && envCfg) {
    candidates.push({ cfg: envCfg, isFallback: false, compat: true });
  }
  return { candidates, modeExternalAllowed, primaryInvalid };
}
