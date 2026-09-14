import { ChatMessage, ChatOptions, ChatResult, HealthResult, Provider, ProviderConfig } from "./types.ts";

// Ollama adapter — native /api/chat (more control than the /v1 shim).
//   think:false   -> "thinking" models (qwen3, deepseek-r1, …) don't burn the token budget on hidden
//                    reasoning and then return empty content. MEASURED: qwen3:4b returns empty content
//                    without this; valid JSON with it.
//   format:"json" -> strict JSON output when jsonMode is requested.
//   stream:false  -> single response.
// Ollama ships with NO auth. In production the endpoint MUST be a private/authenticated gateway;
// the router enforces the provider's privacy class, and if an apiKey is configured (a gateway token)
// it is sent as a Bearer. The raw open endpoint must never be used from an EXTERNAL-egress route.
export class OllamaProvider implements Provider {
  constructor(readonly config: ProviderConfig) {}

  private base() { return this.config.endpoint.replace(/\/+$/, ""); }

  async chat(messages: ChatMessage[], opts: ChatOptions = {}): Promise<ChatResult> {
    const t0 = Date.now();
    const model = opts.model || this.config.model;
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.config.apiKey) headers["Authorization"] = `Bearer ${this.config.apiKey}`;
    const body: Record<string, unknown> = {
      model,
      messages,
      stream: false,
      think: false,
      options: {
        temperature: opts.temperature ?? 0.2,
        num_predict: opts.maxOutput ?? this.config.maxOutput ?? 800,
      },
    };
    if (opts.jsonMode ?? this.config.supportsJson) body.format = "json";
    const r = await fetch(`${this.base()}/api/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(opts.timeoutMs ?? this.config.timeoutMs),
    });
    if (!r.ok) throw new Error(`ollama_${r.status}`);
    const payload = await r.json();
    const text = payload?.message?.content ?? "";
    if (!text) throw new Error("ollama_empty_response");
    return { text: String(text), providerId: this.config.id, providerType: "ollama", model, latencyMs: Date.now() - t0 };
  }

  async health(): Promise<HealthResult> {
    const t0 = Date.now();
    try {
      const headers: Record<string, string> = {};
      if (this.config.apiKey) headers["Authorization"] = `Bearer ${this.config.apiKey}`;
      const r = await fetch(`${this.base()}/api/tags`, { headers, signal: AbortSignal.timeout(8000) });
      if (!r.ok) return { ok: false, error: `ollama_${r.status}`, latencyMs: Date.now() - t0 };
      const d = await r.json();
      return { ok: true, latencyMs: Date.now() - t0, models: (d?.models || []).map((m: any) => String(m?.name)).filter(Boolean) };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : "unreachable", latencyMs: Date.now() - t0 };
    }
  }
}
