// Pure-logic tests for the WatchLog AI router policy — no DB, no network. Proves the security-
// critical decisions: fail-closed config, the egress gate a site's local-only restriction imposes,
// NO_MODEL classification, and candidate ordering.  Run:  deno test providers/router_test.ts
import {
  normalizeMode, dbProviderToConfig, isExternal, egressAllowed, noModelIntent, buildCandidates,
} from "./router.ts";
import type { ProviderConfig } from "./types.ts";

function assert(cond: unknown, msg: string) { if (!cond) throw new Error("FAIL: " + msg); }
function eq(a: unknown, b: unknown, msg: string) { assert(a === b, `${msg} (got ${JSON.stringify(a)})`); }

const LOCAL = {
  id: "p1", name: "VI Ollama", type: "ollama", endpoint: "http://ollama.local:11434", model: "qwen3:4b",
  supports_text: true, supports_json: true, privacy: "LOCAL", external_egress: false, timeout_ms: 90000, max_output: 900,
};
const EXTERNAL = {
  id: "p2", name: "Cloud", type: "openai_compat", endpoint: "https://api.example/v1", model: "gpt-x",
  privacy: "EXTERNAL", external_egress: true, timeout_ms: 35000, max_output: 900, api_key: "sk-secret",
};

Deno.test("normalizeMode: valid passes, junk defaults to instant", () => {
  eq(normalizeMode("thinking"), "thinking", "thinking");
  eq(normalizeMode("HIVE"), "hive", "case-insensitive");
  eq(normalizeMode("nonsense"), "instant", "junk -> instant");
  eq(normalizeMode(undefined), "instant", "missing -> instant");
});

Deno.test("dbProviderToConfig: valid maps snake->camel; invalid fails closed to null", () => {
  const cfg = dbProviderToConfig(LOCAL)!;
  assert(cfg, "valid local resolves");
  eq(cfg.privacy, "LOCAL", "privacy mapped");
  eq(cfg.externalEgress, false, "external_egress mapped");
  eq(cfg.timeoutMs, 90000, "timeout mapped");
  eq(dbProviderToConfig(null), null, "null -> null");
  eq(dbProviderToConfig({ ...LOCAL, endpoint: "" }), null, "missing endpoint -> null (fail closed)");
  eq(dbProviderToConfig({ ...LOCAL, model: "" }), null, "missing model -> null (fail closed)");
  eq(dbProviderToConfig({ ...LOCAL, type: "wat" }), null, "unknown type -> null (fail closed)");
  eq(dbProviderToConfig({ ...LOCAL, id: "" }), null, "missing id -> null (fail closed)");
});

Deno.test("isExternal: LOCAL/no-egress is internal; EXTERNAL or egress is external", () => {
  eq(isExternal(dbProviderToConfig(LOCAL)!), false, "local internal");
  eq(isExternal(dbProviderToConfig(EXTERNAL)!), true, "external");
  eq(isExternal(dbProviderToConfig({ ...LOCAL, external_egress: true })!), true, "egress flag flips it");
});

Deno.test("egressAllowed: local always ok; external needs BOTH site + mode allow", () => {
  const local = dbProviderToConfig(LOCAL)!, ext = dbProviderToConfig(EXTERNAL)!;
  eq(egressAllowed(local, false, false), true, "local always allowed even on local-only site");
  eq(egressAllowed(ext, false, true), false, "external blocked when site is local-only (mode cannot override)");
  eq(egressAllowed(ext, true, false), false, "external blocked when mode disallows egress");
  eq(egressAllowed(ext, true, true), true, "external allowed only when site AND mode allow");
});

Deno.test("noModelIntent: canonical status = true; reasoning/setup = false", () => {
  eq(noModelIntent("Are my cameras online?"), true, "camera status");
  eq(noModelIntent("how many cameras do I have"), true, "canonical count");
  eq(noModelIntent("show monitoring coverage"), true, "coverage");
  eq(noModelIntent("Explain the monitoring coverage and why it dropped"), false, "explain -> model");
  eq(noModelIntent("help me set up line crossing"), false, "setup -> model");
  eq(noModelIntent("what should I monitor for a retail site"), false, "recommend -> model");
});

Deno.test("buildCandidates: primary+fallback ordered; unconfigured uses env bridge; invalid primary flagged", () => {
  const env: ProviderConfig = dbProviderToConfig(LOCAL)!;
  // configured primary + fallback
  const r1 = buildCandidates({ configured: true, external_egress_allowed: true, primary: LOCAL, fallback: EXTERNAL }, null);
  eq(r1.candidates.length, 2, "two candidates");
  eq(r1.candidates[0].isFallback, false, "primary first");
  eq(r1.candidates[1].isFallback, true, "fallback second");
  eq(r1.modeExternalAllowed, true, "mode egress flag propagated");
  // unconfigured -> env bridge only
  const r2 = buildCandidates({ configured: false, primary: null, fallback: null }, env);
  eq(r2.candidates.length, 1, "env bridge candidate");
  eq(r2.candidates[0].compat, true, "flagged as compat");
  // configured but primary invalid -> flagged, not silently bridged
  const r3 = buildCandidates({ configured: true, primary: { ...LOCAL, endpoint: "" }, fallback: null }, env);
  eq(r3.primaryInvalid, true, "invalid primary flagged");
  eq(r3.candidates.length, 0, "no env bridge masks a broken DB config");
});
