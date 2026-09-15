import { Provider, ProviderConfig } from "./types.ts";
import { OllamaProvider } from "./ollama.ts";
import { OpenAICompatProvider } from "./openai_compat.ts";
import { isLocalEndpoint } from "./router.ts";

// There are only TWO real adapters today: Ollama and OpenAI-compatible. `anthropic` and `gemini` are
// NOT native adapters — they are served through the OpenAI-compatible GATEWAY adapter and must be
// configured with an OpenAI-compatible endpoint. Native Anthropic/Gemini SDK adapters are PLANNED;
// the admin UI labels those types as gateway accordingly (no "native adapter" claim).
export function buildProvider(cfg: ProviderConfig): Provider {
  switch (cfg.type) {
    case "ollama":
      return new OllamaProvider(cfg);
    case "openai_compat":
    case "openai":
    case "anthropic":   // via OpenAI-compatible gateway (not a native adapter)
    case "gemini":      // via OpenAI-compatible gateway (not a native adapter)
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
  const isOllamaWire = /\/api\/?$/.test(endpoint) === false && /:11434(\/|$)/.test(endpoint);
  // Network-safe: LOCAL is decided by the endpoint HOST, never inferred from :11434, and a `:cloud`
  // model is always external. A public-IP Ollama endpoint is therefore EXTERNAL, not LOCAL.
  const local = isLocalEndpoint(endpoint) && !/:cloud$/i.test(model);
  return {
    id: "env:default",
    name: "Environment default",
    type: isOllamaWire ? "ollama" : "openai_compat",
    endpoint: isOllamaWire ? endpoint.replace(/\/v1\/?$/, "") : endpoint,
    model,
    apiKey: apiKey || undefined,
    supportsText: true,
    supportsVision: false,
    supportsTools: false,
    supportsJson: true,
    privacy: local ? "LOCAL" : "EXTERNAL",
    externalEgress: !local,
    timeoutMs: isOllamaWire ? 90000 : 35000,
    maxOutput: 900,
  };
}
