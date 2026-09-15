// WatchLog AI router — the deterministic decision layer between a tenant/site-authorized request and
// a model. It resolves a WatchLog mode (Instant/Thinking/Hive) to provider configs from the DB,
// applies the site's data-egress policy AFTER resolution (so an admin-configured cloud model can
// never override a site's local-only restriction), fails closed on invalid config, and always keeps
// the verified-data guided fallback as the floor. It never sees credentials-as-authority: the MODEL
// is never the boundary. These helpers are pure so the policy is testable in isolation.

import { ProviderConfig, ProviderType } from "./types.ts";

export type AiMode = "instant" | "thinking" | "hive";
export type RouteKind = "no_model" | "ai_primary" | "ai_fallback" | "guided_fallback";
export type EgressDecision = "local" | "external" | "blocked_local_only" | "n/a";
export type RouteOutcome =
  | "ok" | "deterministic" | "no_provider_configured" | "config_invalid"
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
    privacy: o.privacy === "EXTERNAL" ? "EXTERNAL" : "LOCAL",
    externalEgress: !!o.external_egress,
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
  // primaryInvalid: the mode names a primary provider but it did not resolve to a valid config.
  const primaryInvalid = !!(resolvedMode?.configured) && resolvedMode?.primary != null && primary === null;
  if (primary) candidates.push({ cfg: primary, isFallback: false, compat: false });
  if (fallback) candidates.push({ cfg: fallback, isFallback: true, compat: false });
  // Legacy env bridge ONLY when the mode is entirely unconfigured, so it never masks a broken DB config.
  if (candidates.length === 0 && !resolvedMode?.configured && envCfg) {
    candidates.push({ cfg: envCfg, isFallback: false, compat: true });
  }
  return { candidates, modeExternalAllowed, primaryInvalid };
}
