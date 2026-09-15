// WatchLog AI provider abstraction.
//
// The MODEL is never the security boundary. A Provider only performs chat/vision inference over
// messages the APPLICATION already assembled and authorized. Provider selection, privacy/egress
// policy, credentials, tenant/site scope, and action authorization are decided outside this layer
// (router + registry + Postgres RPCs) — never by a model.

export type ProviderType = "ollama" | "openai_compat" | "openai" | "anthropic" | "gemini";
export type PrivacyClass = "LOCAL" | "EXTERNAL";

export interface ProviderConfig {
  id: string;                 // stable id: "env:default" or a provider_configs row uuid
  name: string;               // admin label, e.g. "VI Ollama"
  type: ProviderType;
  endpoint: string;           // base URL (no trailing slash required)
  model: string;              // default model id for this provider
  apiKey?: string;            // resolved SERVER-SIDE only (Supabase Vault). Never logged, never returned to the browser, never placed in a prompt.
  supportsText: boolean;
  supportsVision: boolean;
  supportsTools: boolean;
  supportsJson: boolean;
  privacy: PrivacyClass;      // LOCAL = runs on our infra, no external egress; EXTERNAL = prompt content leaves our control
  externalEgress: boolean;
  timeoutMs: number;
  maxContext?: number;
  maxOutput?: number;
}

export interface ChatMessage { role: "system" | "user" | "assistant"; content: string; }

export interface ChatOptions {
  model?: string;             // override provider default (mode routing)
  jsonMode?: boolean;         // request strict JSON output
  temperature?: number;
  maxOutput?: number;
  timeoutMs?: number;
}

export interface ChatResult {
  text: string;
  providerId: string;
  providerType: ProviderType;
  model: string;
  latencyMs: number;
}

export interface HealthResult {
  ok: boolean;
  latencyMs?: number;
  models?: string[];
  error?: string;
}

export interface Provider {
  readonly config: ProviderConfig;
  chat(messages: ChatMessage[], opts?: ChatOptions): Promise<ChatResult>;
  health(): Promise<HealthResult>;
}

// Defence in depth: never let something key-shaped reach a log line.
export function redactSecrets(s: string): string {
  return s.replace(/(sk-[A-Za-z0-9_-]{6,})|(Bearer\s+[A-Za-z0-9._-]{8,})/g, "***");
}
