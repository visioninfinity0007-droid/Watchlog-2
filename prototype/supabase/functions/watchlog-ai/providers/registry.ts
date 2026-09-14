import { Provider, ProviderConfig } from "./types.ts";
import { OllamaProvider } from "./ollama.ts";
import { OpenAICompatProvider } from "./openai_compat.ts";

// Instantiate the concrete adapter for a config. anthropic/gemini native adapters are PLANNED;
// until then a config of those types must present an OpenAI-compatible endpoint (many gateways do).
export function buildProvider(cfg: ProviderConfig): Provider {
  switch (cfg.type) {
    case "ollama":
      return new OllamaProvider(cfg);
    case "openai_compat":
    case "openai":
    case "anthropic":
    case "gemini":
      return new OpenAICompatProvider(cfg);
    default:
      return new OpenAICompatProvider(cfg);
  }
}

// Legacy default provider from env (WATCHLOG_AI_*). Keeps existing deployments working before an
// admin configures providers in the DB (Phase 4/5). Returns null when unset => deterministic
// fallback. apiKey may be empty for a local/no-auth endpoint.
export function legacyEnvProvider(): ProviderConfig | null {
  const endpoint = (Deno.env.get("WATCHLOG_AI_ENDPOINT") || "").trim();
  const apiKey = (Deno.env.get("WATCHLOG_AI_API_KEY") || "").trim();
  const model = (Deno.env.get("WATCHLOG_AI_MODEL") || "").trim();
  if (!endpoint || !model) return null;
  const isOllama = /\/api\/?$/.test(endpoint) === false && /:11434(\/|$)/.test(endpoint);
  return {
    id: "env:default",
    name: "Environment default",
    type: isOllama ? "ollama" : "openai_compat",
    endpoint: isOllama ? endpoint.replace(/\/v1\/?$/, "") : endpoint,
    model,
    apiKey: apiKey || undefined,
    supportsText: true,
    supportsVision: false,
    supportsTools: false,
    supportsJson: true,
    privacy: isOllama ? "LOCAL" : "EXTERNAL",
    externalEgress: !isOllama,
    timeoutMs: isOllama ? 90000 : 35000,
    maxOutput: 900,
  };
}
