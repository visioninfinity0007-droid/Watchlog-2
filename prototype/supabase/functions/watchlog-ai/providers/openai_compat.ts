import { ChatMessage, ChatOptions, ChatResult, HealthResult, Provider, ProviderConfig } from "./types.ts";

// OpenAI-compatible adapter: OpenAI, OpenRouter, Together, vLLM, LM Studio, and Ollama's own /v1.
// `endpoint` is the base (e.g. https://api.openai.com/v1 or http://gateway:11434/v1); we POST
// {endpoint}/chat/completions. jsonMode -> response_format {type:"json_object"}.
// This single adapter also serves the "openai" provider type (OpenAI itself is the reference impl).
export class OpenAICompatProvider implements Provider {
  constructor(readonly config: ProviderConfig) {}

  private base() { return this.config.endpoint.replace(/\/+$/, ""); }

  async chat(messages: ChatMessage[], opts: ChatOptions = {}): Promise<ChatResult> {
    const t0 = Date.now();
    const model = opts.model || this.config.model;
    const body: Record<string, unknown> = { model, messages, temperature: opts.temperature ?? 0.2 };
    const maxOut = opts.maxOutput ?? this.config.maxOutput;
    if (maxOut) body.max_tokens = maxOut;
    if (opts.jsonMode ?? this.config.supportsJson) body.response_format = { type: "json_object" };
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.config.apiKey) headers["Authorization"] = `Bearer ${this.config.apiKey}`;
    const r = await fetch(`${this.base()}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(opts.timeoutMs ?? this.config.timeoutMs),
    });
    if (!r.ok) throw new Error(`provider_${r.status}`);
    const payload = await r.json();
    const text = payload?.choices?.[0]?.message?.content ?? payload?.output_text ?? "";
    if (!text) throw new Error("provider_empty_response");
    return { text: String(text), providerId: this.config.id, providerType: this.config.type, model, latencyMs: Date.now() - t0 };
  }

  async health(): Promise<HealthResult> {
    const t0 = Date.now();
    try {
      const headers: Record<string, string> = {};
      if (this.config.apiKey) headers["Authorization"] = `Bearer ${this.config.apiKey}`;
      const r = await fetch(`${this.base()}/models`, { headers, signal: AbortSignal.timeout(8000) });
      if (!r.ok) return { ok: false, error: `provider_${r.status}`, latencyMs: Date.now() - t0 };
      const d = await r.json();
      return { ok: true, latencyMs: Date.now() - t0, models: (d?.data || []).map((m: any) => String(m?.id)).filter(Boolean) };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : "unreachable", latencyMs: Date.now() - t0 };
    }
  }
}
