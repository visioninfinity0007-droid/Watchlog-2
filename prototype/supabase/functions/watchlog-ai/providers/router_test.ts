// Pure-logic tests for the WatchLog AI router policy — no DB, no network. Proves the security-
// critical decisions: fail-closed config, the egress gate a site's local-only restriction imposes,
// NO_MODEL classification, and candidate ordering.  Run:  deno test providers/router_test.ts
import {
  normalizeMode, dbProviderToConfig, isExternal, egressAllowed, noModelIntent, buildCandidates,
  isEvidenceIntent, stripEvidenceImages, resolveEvidenceWindow, resolveCameraId, evidenceSummary,
  retrieveEvidence, isLocalEndpoint,
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

Deno.test("isLocalEndpoint: private/loopback/service-name true; public IP/host false", () => {
  eq(isLocalEndpoint("http://localhost:11434"), true, "localhost");
  eq(isLocalEndpoint("http://127.0.0.1:11434"), true, "loopback");
  eq(isLocalEndpoint("http://10.0.0.2:11434"), true, "RFC1918 10/8");
  eq(isLocalEndpoint("http://192.168.1.5:11434"), true, "RFC1918 192.168");
  eq(isLocalEndpoint("http://ollama:11434"), true, "bare docker service name");
  eq(isLocalEndpoint("http://ollama.internal"), true, ".internal");
  eq(isLocalEndpoint("http://185.250.37.61:11434"), false, "public IP on :11434 is NOT local");
  eq(isLocalEndpoint("https://api.openai.com/v1"), false, "public hostname");
});

Deno.test("#2 network-safe classification: :cloud and public endpoints are EXTERNAL; private stays LOCAL", () => {
  eq(dbProviderToConfig({ ...LOCAL, endpoint: "http://ollama:11434" })!.privacy, "LOCAL", "bare service name stays LOCAL");
  eq(dbProviderToConfig({ ...LOCAL, endpoint: "http://127.0.0.1:11434" })!.privacy, "LOCAL", "loopback stays LOCAL");
  // a public-IP endpoint claimed LOCAL is downgraded to EXTERNAL — never infer LOCAL from :11434
  const pub = dbProviderToConfig({ ...LOCAL, privacy: "LOCAL", external_egress: false, endpoint: "http://185.250.37.61:11434" })!;
  eq(pub.privacy, "EXTERNAL", "public IP :11434 is not LOCAL");
  eq(pub.externalEgress, true, "public endpoint forces external egress");
  // a :cloud model is ALWAYS external regardless of admin metadata
  const cloud = dbProviderToConfig({ ...LOCAL, privacy: "LOCAL", external_egress: false, model: "deepseek-v3.2:cloud" })!;
  eq(cloud.privacy, "EXTERNAL", ":cloud model is external");
  eq(isExternal(cloud), true, ":cloud is external for the egress gate");
  // and a local-only site therefore fails closed against a :cloud model
  eq(egressAllowed(cloud, /*siteAllowsExternal*/ false, /*modeExternalAllowed*/ true), false, ":cloud blocked on local-only site");
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

Deno.test("evidence intent vs NO_MODEL: status is caught by NO_MODEL first (evidence never loads)", () => {
  eq(isEvidenceIntent("What happened around the armory last night?"), true, "armory last night");
  eq(isEvidenceIntent("show me footage from the entrance"), true, "footage");
  eq(noModelIntent("are my cameras online?"), true, "status is NO_MODEL, so evidence is never loaded (#8)");
});

Deno.test("resolveEvidenceWindow: 'last night' is a ~12h overnight window in the site tz, in the past", () => {
  const now = new Date("2026-09-15T09:00:00Z");   // Asia/Karachi is UTC+5 -> local 14:00
  const w = resolveEvidenceWindow("what happened last night", now, "Asia/Karachi");
  eq(w.label, "last night", "label");
  assert(new Date(w.from) < new Date(w.to), "from before to");
  const hours = (new Date(w.to).getTime() - new Date(w.from).getTime()) / 3600000;
  assert(hours >= 11 && hours <= 13, "~12h window, got " + hours);
  assert(new Date(w.to) < now, "window is entirely in the past");
});

Deno.test("resolveCameraId: matches a named/purposed camera, else null (all cameras)", () => {
  const cams = [{ id: "c1", name: "Armory", purpose: "restricted" }, { id: "c2", name: "Front Gate", purpose: "entrance" }];
  eq(resolveCameraId("what happened around the armory", cams), "c1", "by name");
  eq(resolveCameraId("who was at the entrance", cams), "c2", "by purpose");
  eq(resolveCameraId("what happened last night", cams), null, "no camera named -> all cameras");
});

Deno.test("#9 evidence egress: a LOCAL-ONLY site's images never reach an external model", () => {
  const ext = dbProviderToConfig(EXTERNAL)!;
  eq(egressAllowed(ext, false, true), false, "external provider is not egress-allowed for a local-only site");
  const ev = { bundles: [{ snapshots: [{ id: "s1", image_b64: "SECRET-IMAGE", camera_id: "c1" }] }] };
  const stripped = stripEvidenceImages(ev);
  eq(stripped.bundles[0].snapshots[0].image_b64, undefined, "image bytes removed");
  eq(stripped.bundles[0].snapshots[0].image_omitted, true, "snapshot marked image_omitted");
  eq(stripped.images_withheld, true, "evidence flagged images_withheld");
});

Deno.test("evidenceSummary: grounded — cites distinct events, cameras and detections", () => {
  const ev = { window: { label: "last night" },
    index: [{ event_ref: "e1", camera_id: "c1", captured_at: "2026-09-14T22:10:00Z" },
            { event_ref: "e1", camera_id: "c1", captured_at: "2026-09-14T22:11:00Z" },
            { event_ref: "e2", camera_id: "c2", captured_at: "2026-09-14T23:00:00Z" }],
    bundles: [{ detections: [[{ label: "person", confidence: 0.8 }]] }] };
  const s = evidenceSummary(ev);
  eq(s.events, 2, "2 distinct events");
  eq(s.cameras.length, 2, "2 distinct cameras");
  assert(s.text.includes("2 events"), "mentions event count");
  assert(s.text.toLowerCase().includes("person"), "mentions detection label");
});

Deno.test("armory last night: two-stage retrieval — compact index first, load only the selected event, grounded", async () => {
  const calls: string[] = [];
  const rpc = async (name: string, args: Record<string, any>) => {
    calls.push(name);
    if (name === "wl_ai_evidence_index") {
      // stage 1 must be scoped to the resolved camera + a last-night window
      eq(args.p_camera_id, "cam-armory", "index scoped to the resolved armory camera");
      assert(new Date(args.p_to as string) < new Date("2026-09-15T09:00:00Z"), "window is in the past");
      return { ok: true, data: [
        { event_ref: "evt-armory-1", camera_id: "cam-armory", captured_at: "2026-09-14T22:10:00Z", has_payload: true },
        { event_ref: "evt-armory-1", camera_id: "cam-armory", captured_at: "2026-09-14T22:11:00Z", has_payload: true },
      ] };
    }
    if (name === "wl_ai_evidence_bundle") {
      eq(args.p_event_ref, "evt-armory-1", "stage 2 loads only the selected event");
      return { ok: true, data: { found: true, snapshots: [{ image_b64: "IMG", camera_id: "cam-armory" }],
        detections: [[{ label: "person", confidence: 0.8 }]] } };
    }
    return { ok: false };
  };
  const ctx = { site: { id: "site-1", timezone: "Asia/Karachi" },
    cameras: [{ id: "cam-armory", name: "Armory", purpose: "restricted" }] };
  const ev = await retrieveEvidence(rpc, "What happened around the armory last night?", "site-1", ctx, new Date("2026-09-15T09:00:00Z"));
  assert(ev, "evidence loaded for an evidence-intent prompt");
  eq(calls[0], "wl_ai_evidence_index", "stage 1 (compact index) runs first");
  assert(calls.includes("wl_ai_evidence_bundle"), "stage 2 (bundle) loads the relevant event");
  eq(ev.camera_id, "cam-armory", "resolved the armory camera");
  const s = evidenceSummary(ev);
  eq(s.events, 1, "one distinct event");
  assert(s.text.toLowerCase().includes("person"), "answer is grounded on the detection");
  // a non-evidence status prompt never runs retrieval
  eq(await retrieveEvidence(rpc, "is the site online?", "site-1", ctx, new Date()), null, "status prompt -> no retrieval");
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

Deno.test("buildCandidates: optional tertiary layer (0109) is ordered last and fails closed", () => {
  const THIRD = { ...EXTERNAL, id: "p3", name: "Third", endpoint: "https://third.example/v1", model: "m3" };
  // three configured layers -> tried in order, both non-primary layers marked as fallback
  const r1 = buildCandidates(
    { configured: true, external_egress_allowed: true, primary: LOCAL, fallback: EXTERNAL, tertiary: THIRD }, null);
  eq(r1.candidates.length, 3, "three candidates");
  eq(r1.candidates[0].cfg.id, "p1", "primary first");
  eq(r1.candidates[1].cfg.id, "p2", "fallback second");
  eq(r1.candidates[2].cfg.id, "p3", "tertiary third");
  eq(r1.candidates[2].isFallback, true, "tertiary counts as a fallback leg for the audit");
  // a structurally invalid tertiary is DROPPED, never guessed, and never disturbs layers 1-2
  const r2 = buildCandidates(
    { configured: true, primary: LOCAL, fallback: EXTERNAL, tertiary: { ...THIRD, model: "" } }, null);
  eq(r2.candidates.length, 2, "invalid tertiary dropped");
  eq(r2.primaryInvalid, false, "a bad tertiary does not flag the primary");
  // tertiary alone (no fallback configured) still resolves as the second candidate
  const r3 = buildCandidates({ configured: true, primary: LOCAL, fallback: null, tertiary: THIRD }, null);
  eq(r3.candidates.length, 2, "primary + tertiary");
  eq(r3.candidates[1].cfg.id, "p3", "tertiary follows primary when no fallback is set");
  // absent tertiary keeps the pre-0108 two-layer behaviour byte for byte
  const r4 = buildCandidates({ configured: true, primary: LOCAL, fallback: EXTERNAL }, null);
  eq(r4.candidates.length, 2, "no tertiary -> unchanged two-layer chain");
});
