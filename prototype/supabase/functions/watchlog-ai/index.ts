import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0";
import { buildProvider, legacyEnvProvider } from "./providers/registry.ts";
import type { ChatMessage } from "./providers/types.ts";
import {
  normalizeMode, isExternal, egressAllowed, noModelIntent, buildCandidates,
  stripEvidenceImages, evidenceSummary, retrieveEvidence,
  type AiMode, type RouteAudit,
} from "./providers/router.ts";

type Json = Record<string, any>;

const SYSTEM_PROMPT = `You are WatchLog AI, the conversational operator for WatchLog CCTV intelligence.

FACTUAL AUTHORITY
- WATCHLOG_CONTEXT and WATCHLOG_TOOL_RESULTS are authoritative for this tenant/site.
- Never invent a recorder capability, camera state, incident, person identity, count, time, health state, report, coverage state, or tool result.
- Capability verdicts and evidence classes are authoritative. UNKNOWN means unconfirmed, not unsupported. OFFICIAL_DOCUMENTED is not FIELD_VERIFIED.
- The deterministic setup advisor is authoritative for whether a recorder configuration can be proposed. Never weaken its evidence or safety gate.
- Never treat UNVERIFIED monitoring time as "no activity". Keep LIVE, RECOVERED, and UNVERIFIED provenance separate.
- Behavioral identity is uncertain unless an approved identity source explicitly proves it. Prefer estimated, probable, plausible journey, or unclassified.
- A frozen report snapshot is the authority for an already-generated historical report. Never silently rewrite it.

SAFETY
- Recorder credentials stay on the on-site WatchLog service and must never be requested or exposed.
- Recorder writes are never silently executed. Present recorder changes only as proposals requiring authorized human approval and the WatchLog Site Control safety gate.
- Firmware changes, factory reset, storage formatting/deletion, user/password administration, and unsafe network changes are unavailable.
- Prefer business outcomes over recorder/API jargon.

SETUP
When setup is incomplete, act like a concise setup engineer. Use the recorded onboarding state, business context, exact recorder capability profile, deterministic advisor, and cameras. Ask only the next useful question. Do not claim a step is complete unless WATCHLOG_CONTEXT says it is complete.

OUTPUT
Return JSON only:
{
  "answer":"natural-language response",
  "cards":[{"type":"health|coverage|recorder|cameras|capabilities|setup|incident|report|approval","title":"...","data":{}}],
  "suggestions":["short next prompt"],
  "proposed_actions":[{"kind":"navigate|setup_context|setup_camera|watchlog_rule|site_control_proposal","label":"...","data":{}}]
}
Only propose actions supported by WATCHLOG_CONTEXT, WATCHLOG_TOOL_RESULTS, and caller permissions. Never claim an action executed unless a WatchLog tool result explicitly proves it.`;

const SOFTWARE_ANALYTICS = new Set([
  "human_vehicle_classification", "restricted_area", "after_hours", "dwell",
  "line_crossing", "intrusion", "people_presence",
]);
const SITE_TYPE_GOALS: Record<string, string[]> = {
  office: ["human_vehicle_classification", "restricted_area", "after_hours", "line_crossing"],
  retail: ["human_vehicle_classification", "people_counting", "line_crossing", "dwell", "after_hours"],
  factory: ["line_crossing", "intrusion", "restricted_area", "after_hours"],
  restaurant: ["human_vehicle_classification", "people_counting", "dwell", "after_hours"],
  warehouse: ["line_crossing", "intrusion", "restricted_area", "after_hours"],
  clinic: ["human_vehicle_classification", "restricted_area", "after_hours"],
};
const ACTION_KINDS = new Set(["navigate", "setup_context", "setup_camera", "watchlog_rule", "site_control_proposal"]);
const CARD_TYPES = new Set(["health", "coverage", "recorder", "cameras", "capabilities", "setup", "incident", "report", "approval"]);
const SAFE_HREFS = new Set([
  "/ai/", "/setup/", "/reports/", "/incidents/", "/site-health/", "/control-room/",
  "/analytics/", "/analytics/studio/", "/analytics/schedules/", "/site-control/", "/settings/",
]);

function configuredOrigins() {
  return (Deno.env.get("WATCHLOG_PORTAL_ORIGINS") || "")
    .split(",").map((v) => v.trim()).filter(Boolean);
}
function cors(req: Request) {
  const origin = req.headers.get("Origin") || "";
  const allowed = configuredOrigins();
  const selected = allowed.length === 0 ? "*" : allowed.includes(origin) ? origin : "null";
  return {
    "Access-Control-Allow-Origin": selected,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
}
function response(req: Request, body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...cors(req), "Content-Type": "application/json" } });
}
function disallowedOrigin(req: Request) {
  const origin = req.headers.get("Origin") || "";
  const allowed = configuredOrigins();
  return allowed.length > 0 && !!origin && !allowed.includes(origin);
}
function compactContext(ctx: Json) {
  return {
    facts_version: ctx?.facts_version, generated_at: ctx?.generated_at, site: ctx?.site,
    business_context: ctx?.business_context, onboarding: ctx?.onboarding, recorder: ctx?.recorder,
    connectivity: ctx?.connectivity, capabilities: ctx?.capabilities, capability_known: ctx?.capability_known,
    cameras: ctx?.cameras, faults: ctx?.faults, coverage: ctx?.coverage, permissions: ctx?.permissions,
    recent_events: (ctx?.recent_events || []).slice(0, 12), safety: ctx?.safety,
  };
}
function dateInZone(timeZone: string | undefined, offsetDays = 0) {
  const base = new Date();
  const shifted = new Date(base.getTime() + offsetDays * 86400000);
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timeZone || "UTC", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(shifted);
    const part = (t: string) => parts.find((p) => p.type === t)?.value || "";
    return `${part("year")}-${part("month")}-${part("day")}`;
  } catch {
    return shifted.toISOString().slice(0, 10);
  }
}
function capabilityMap(profile: any) {
  const out: Json = {};
  for (const c of Array.isArray(profile) ? profile : []) if (c?.capability) out[String(c.capability)] = c;
  return out;
}
function deterministicSetupAdvice(ctx: Json) {
  const profile = capabilityMap(ctx?.capabilities);
  const bc = ctx?.business_context || {};
  const siteType = bc.site_type || ctx?.site?.site_type || "other";
  const goals = SITE_TYPE_GOALS[siteType] || ["human_vehicle_classification", "after_hours"];
  const recommendations: any[] = [], recorderProposals: any[] = [], software: string[] = [], unavailable: any[] = [], questions: string[] = [];
  const monitored = (ctx?.cameras || []).filter((c: Json) => c.monitor !== false);
  const hasPurpose = (terms: string[]) => monitored.some((c: Json) => terms.includes(String(c.purpose || "").toLowerCase()));
  const hoursKnown = !!(bc.open_time && bc.close_time && Array.isArray(bc.working_days) && bc.working_days.length);

  for (const analytic of goals) {
    const cap = profile[analytic] || { capability: analytic, verdict: "unknown", evidence_class: "UNKNOWN", write: null, safety_class: "na" };
    const verdict = cap.verdict || "unknown", evidence = cap.evidence_class || "UNKNOWN";
    if (verdict === "supported" && evidence === "FIELD_VERIFIED" && cap.write === true && cap.safety_class === "safe_write") {
      recommendations.push({ analytic, decision: "recorder_configure", evidence_class: evidence, rationale: "Field-verified safe recorder configuration is available for this exact model." });
      recorderProposals.push({ analytic, capability: analytic, safety_class: "safe_write", evidence_class: evidence, requires_approval: true });
    } else if (verdict === "supported") {
      recommendations.push({ analytic, decision: "recorder_needs_verification", evidence_class: evidence, rationale: "Recorder support is recorded, but WatchLog will not write until the exact safe path is field-verified." });
    } else if (verdict === "by_camera") {
      recommendations.push({ analytic, decision: "camera_side", evidence_class: evidence, rationale: "This capability belongs to the camera rather than the recorder." });
    } else if (SOFTWARE_ANALYTICS.has(analytic)) {
      recommendations.push({ analytic, decision: "watchlog_software", evidence_class: evidence, rationale: verdict === "unsupported" ? "The recorder does not provide this natively; WatchLog software analytics can provide it." : "Recorder support is unconfirmed; WatchLog software analytics can provide it without assuming recorder support." });
      software.push(analytic);
    } else {
      recommendations.push({ analytic, decision: "not_available", evidence_class: evidence, rationale: verdict === "unsupported" ? "Not supported by this recorder and not offered as a WatchLog software analytic." : "Recorder support is unconfirmed and WatchLog does not claim this software capability." });
      unavailable.push({ analytic, verdict, evidence_class: evidence });
    }
    if (verdict === "unknown") questions.push(`Recorder support for ${analytic.replaceAll("_", " ")} is unconfirmed; run a safe read before relying on it.`);
    if (analytic === "after_hours" && !hoursKnown) questions.push("Confirm this site's operating hours and working days.");
    if (analytic === "restricted_area" && !hasPurpose(["restricted"])) questions.push("Which monitored cameras view restricted or sensitive areas?");
    if ((analytic === "line_crossing" || analytic === "intrusion") && !hasPurpose(["entrance", "perimeter", "loading"])) questions.push("Which monitored cameras cover entrances, loading access or perimeter crossings?");
  }
  return { advisor_version: "site-config-advisor-v1-compatible", site_type: siteType, recorder: ctx?.recorder || {}, recommendations, site_control_proposals: recorderProposals, software_analytics: [...new Set(software)].sort(), unavailable, human_questions: [...new Set(questions)], note: "Recommendation only; recorder changes require authorized human approval and WatchLog safety verification." };
}
async function rpcOptional(sb: any, name: string, args: Json) {
  const { data, error } = await sb.rpc(name, args);
  if (!error) return { ok: true, data };
  const msg = String(error?.message || "");
  if (/schema cache|could not find the function|does not exist/i.test(msg)) return { ok: false, unavailable: true };
  return { ok: false, error: msg.slice(0, 300) };
}
async function gatherTools(sb: any, prompt: string, siteId: string, ctx: Json) {
  const p = prompt.toLowerCase(), tz = ctx?.site?.timezone || "UTC";
  const today = dateInZone(tz), yesterday = dateInZone(tz, -1);
  const out: Json = { site_local_date: today, setup_advisor: null, daily_intelligence: null, frozen_report: null, analytics: null };
  if (/setup|configure|configuration|support|capabilit|recorder|nvr|dvr|monitoring rule|what can/.test(p)) out.setup_advisor = deterministicSetupAdvice(ctx);
  const overnight = /overnight|last night|yesterday/.test(p);
  if (overnight || /what happened|today|incident|activity|people|visitor|staff|after.?hours|opening|closing|journey|restricted|dwell/.test(p)) {
    out.daily_intelligence = overnight ? {
      interpretation: "overnight_window",
      yesterday: await rpcOptional(sb, "wl_my_daily_intelligence", { p_site_id: siteId, p_date: yesterday }),
      today: await rpcOptional(sb, "wl_my_daily_intelligence", { p_site_id: siteId, p_date: today }),
    } : await rpcOptional(sb, "wl_my_daily_intelligence", { p_site_id: siteId, p_date: today });
  }
  if (/report|management brief|daily brief|pdf|executive summary/.test(p)) {
    out.frozen_report = await rpcOptional(sb, "wl_my_report_snapshot", { p_site_id: siteId, p_date: overnight ? yesterday : today });
  }
  if (/analytics|trend|visitor flow|vehicle flow|occupancy|busiest|dwell|traffic/.test(p)) {
    const days = /30 day|month/.test(p) ? 30 : /7 day|week/.test(p) ? 7 : 1;
    out.analytics = await rpcOptional(sb, "wl_analytics_overview", { p_days: days, p_site_id: siteId });
  }
  return out;
}
function dailyFallback(tools: Json) {
  const daily = tools?.daily_intelligence;
  if (!daily) return null;
  const unwrap = (v: any) => v?.ok ? v.data : null;
  const datasets = daily?.interpretation === "overnight_window" ? [unwrap(daily.yesterday), unwrap(daily.today)].filter(Boolean) : [unwrap(daily)].filter(Boolean);
  if (!datasets.length) return null;
  const incidents = datasets.flatMap((d: Json) => Array.isArray(d?.incidents) ? d.incidents : []);
  const attention = datasets.reduce((n: number, d: Json) => n + Number(d?.attention?.incidents_total || 0), 0);
  const caveats = [...new Set(datasets.flatMap((d: Json) => Array.isArray(d?.honesty) ? d.honesty : []))];
  const overnight = daily?.interpretation === "overnight_window";
  return {
    answer: `${overnight ? "For the overnight window" : "For today"}, WatchLog's canonical intelligence dataset shows ${attention} incident${attention === 1 ? "" : "s"} in the selected period.${caveats.length ? ` ${String(caveats[0])}` : ""}`,
    cards: [{ type: "incident", title: overnight ? "Overnight intelligence" : "Today's intelligence", data: { incidents: incidents.slice(0, 8), attention } }, ...(datasets[0]?.coverage ? [{ type: "coverage", title: "Monitoring coverage", data: datasets[0].coverage }] : [])],
    suggestions: ["Show the incidents", "Explain monitoring coverage", "Check site health"],
    proposed_actions: [{ kind: "navigate", label: "Open incidents", data: { href: "/incidents/" } }], mode: "guided_fallback",
  };
}
function fallback(prompt: string, ctx: Json, tools: Json) {
  const p = prompt.toLowerCase(), cameras = ctx?.cameras || [], faults = ctx?.faults || [], recorder = ctx?.recorder || {}, coverage = ctx?.coverage || {};
  if (tools?.evidence) {
    const ev = tools.evidence, sum = evidenceSummary(ev);
    return {
      answer: sum.text + (ev?.window?.from ? ` Window: ${ev.window.from} to ${ev.window.to}.` : ""),
      cards: [{ type: "incident", title: `Evidence — ${ev?.window?.label || "requested window"}`,
        data: { events: sum.events, cameras: sum.cameras, span: sum.span, index: (ev?.index || []).slice(0, 8) } }],
      suggestions: ["Show the snapshots", "Which cameras were involved?", "Check site health"],
      proposed_actions: [{ kind: "navigate", label: "Open incidents", data: { href: "/incidents/" } }], mode: "guided_fallback",
    };
  }
  if (/setup|configure|connect|install|what can|capabilit/.test(p)) {
    const steps = ctx?.onboarding?.steps || [], next = steps.find((s: Json) => !s?.done), advice = tools?.setup_advisor || deterministicSetupAdvice(ctx);
    return { answer: next ? `The next recorded setup step is ${String(next.label || next.key).toLowerCase()}. I checked the exact recorder capability profile before making this recommendation.` : "The recorded setup checklist is complete. I can still refine monitoring using the evidence-graded recorder profile and WatchLog software analytics.", cards: [{ type: "setup", title: "WatchLog setup", data: { steps, recorder: [recorder.vendor, recorder.model].filter(Boolean).join(" ") || "Not identified", cameras_discovered: cameras.length, cameras_monitored: cameras.filter((c: Json) => c.monitor).length, recommendation_summary: advice?.recommendations, software_analytics: advice?.software_analytics, human_questions: advice?.human_questions } }], suggestions: next ? ["Continue setup", "Check my cameras", "What can my recorder support?"] : ["What happened today?", "Check site health"], proposed_actions: [{ kind: "navigate", label: "Open guided setup", data: { href: "/setup/" } }], mode: "guided_fallback" };
  }
  const daily = dailyFallback(tools);
  if (daily && /overnight|last night|yesterday|what happened|today|incident|activity|people|visitor|staff|after.?hours|opening|closing|journey|restricted|dwell/.test(p)) return daily;
  if (/report|management brief|daily brief|pdf|executive summary/.test(p)) {
    const r = tools?.frozen_report;
    return r?.ok && r.data ? { answer: `A frozen WatchLog report exists for ${r.data.report_date}. I am using that saved report rather than recomputing historical figures.`, cards: [{ type: "report", title: `Report — ${r.data.report_date}`, data: r.data }], suggestions: ["Summarize the report", "Show incidents in this report"], proposed_actions: [{ kind: "navigate", label: "Open Reports", data: { href: "/reports/" } }], mode: "guided_fallback" } : { answer: "No frozen report is available for that requested day yet. I will not fabricate or silently regenerate a historical customer report.", cards: [], suggestions: ["Open Reports", "What happened today?"], proposed_actions: [{ kind: "navigate", label: "Open Reports", data: { href: "/reports/" } }], mode: "guided_fallback" };
  }
  if (/camera|health|offline|recording/.test(p)) {
    const offline = cameras.filter((c: Json) => c.monitor && c.health_state === "offline");
    return { answer: offline.length ? `${offline.length} monitored camera${offline.length === 1 ? " is" : "s are"} currently offline.` : `No monitored camera is currently marked offline in the latest WatchLog context.`, cards: [{ type: "health", title: "Camera health", data: { cameras, faults } }], suggestions: ["Which cameras are not recording?", "Check my recorder", "Show monitoring coverage"], proposed_actions: [{ kind: "navigate", label: "Open site health", data: { href: "/site-health/" } }], mode: "guided_fallback" };
  }
  if (/recorder|nvr|dvr|support/.test(p)) return { answer: recorder?.model ? `This site is using ${[recorder.vendor, recorder.model].filter(Boolean).join(" ")}. I will only describe capabilities recorded in WatchLog's evidence-graded device profile; unknown remains unconfirmed.` : "WatchLog has not identified the recorder model for this site yet.", cards: [{ type: "recorder", title: "Recorder", data: { recorder, capabilities: ctx?.capabilities || [], capability_known: ctx?.capability_known, recommendation: tools?.setup_advisor } }], suggestions: ["What analytics can this recorder support?", "Check recorder health"], proposed_actions: [], mode: "guided_fallback" };
  if (/coverage|downtime|missed|recovered|unverified/.test(p)) return { answer: "WatchLog keeps live, recovered, and unverified monitoring time separate. Unverified time is never treated as no activity.", cards: [{ type: "coverage", title: "Monitoring coverage", data: coverage }], suggestions: ["Explain any unverified time", "Was anything recovered from the recorder?"], proposed_actions: [], mode: "guided_fallback" };
  return { answer: `I have the current verified WatchLog context for ${ctx?.site?.name || "this site"}. Full model reasoning is not configured or unavailable, so I am using WatchLog's deterministic guidance.`, cards: [{ type: "health", title: "Current site", data: { site: ctx?.site, connectivity: ctx?.connectivity, faults, coverage } }], suggestions: ["Check my cameras", "Continue setup", "What can my recorder support?"], proposed_actions: [], mode: "guided_fallback" };
}
function sanitizeResult(value: any) {
  const src = value && typeof value === "object" ? value : {};
  const answer = String(src.answer || "").slice(0, 16000) || "WatchLog could not produce a safe response.";
  const cards = (Array.isArray(src.cards) ? src.cards : []).filter((c: Json) => CARD_TYPES.has(String(c?.type || ""))).slice(0, 6).map((c: Json) => ({ type: c.type, title: String(c.title || "WatchLog").slice(0, 120), data: c.data && typeof c.data === "object" ? c.data : {} }));
  const suggestions = (Array.isArray(src.suggestions) ? src.suggestions : []).map(String).map((s: string) => s.slice(0, 140)).filter(Boolean).slice(0, 4);
  const proposed_actions = (Array.isArray(src.proposed_actions) ? src.proposed_actions : []).filter((a: Json) => ACTION_KINDS.has(String(a?.kind || ""))).slice(0, 4).map((a: Json) => {
    const kind = String(a.kind), label = String(a.label || "Continue").slice(0, 100), data = a.data && typeof a.data === "object" ? { ...a.data } : {};
    if (kind === "navigate") data.href = SAFE_HREFS.has(String(data.href || "")) ? String(data.href) : "/ai/";
    if (kind === "setup_context" || kind === "setup_camera") data.href = "/setup/";
    if (kind === "watchlog_rule") data.href = "/analytics/studio/";
    if (kind === "site_control_proposal") data.href = "/site-control/";
    delete data.url; delete data.command; delete data.script;
    return { kind, label, data };
  });
  return { answer, cards, suggestions, proposed_actions, mode: src.mode === "ai" ? "ai" : "guided_fallback" };
}
// Two-stage evidence retrieval, tenant/site scoped via the user's own client (wl_assert_my_site
// inside the RPCs). Stage 1 = compact index (no bytes); stage 2 = full bundles for the few relevant
// events. Only called on a MODEL route for an evidence-intent prompt — a NO_MODEL question never
// touches the evidence workspace.
async function loadEvidence(sb: any, prompt: string, siteId: string, ctx: Json): Promise<Json | null> {
  return retrieveEvidence((name, args) => rpcOptional(sb, name, args), prompt, siteId, ctx, new Date());
}
function buildMessages(context: Json, tools: Json, history: any[]): ChatMessage[] {
  return [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "system", content: `WATCHLOG_CONTEXT\n${JSON.stringify(compactContext(context))}` },
    { role: "system", content: `WATCHLOG_TOOL_RESULTS\n${JSON.stringify(tools)}` },
    ...history.slice(-18).filter((m: Json) => m?.role === "user" || m?.role === "assistant")
      .map((m: Json) => ({ role: m.role as "user" | "assistant", content: String(m.content || "").slice(0, 20000) })),
  ];
}
function baseAudit(mode: AiMode, route: RouteAudit["route"], extra: Partial<RouteAudit>): RouteAudit {
  return {
    mode, route, provider_id: null, provider_name: null, model: null, used_fallback: false,
    egress: "n/a", latency_ms: 0, candidates_tried: 0, tool_calls: [], outcome: "ok", ...extra,
  };
}
// The router: tenant/site authorization has already happened by the time we get here. We resolve the
// WatchLog mode -> provider(s) from the DB, apply the site egress policy AFTER resolution, fail closed
// on invalid config, try the configured fallback next, and keep the verified-data guided fallback as
// the floor. Provider/model identities are returned ONLY in `audit` (Admin/audit), never in `result`.
async function routeChat(opts: {
  sb: any; service: any; prompt: string; siteId: string; history: any[]; context: Json; tools: Json;
  mode: AiMode; siteAllowsExternal: boolean; toolCalls: string[];
}): Promise<{ result: Json; audit: RouteAudit }> {
  const { sb, service, prompt, siteId, history, context, tools, mode, siteAllowsExternal, toolCalls } = opts;

  // NO_MODEL: canonical health/status/coverage answered from verified data — no LLM, no egress, and
  // (crucially) NO evidence workspace access.
  if (noModelIntent(prompt)) {
    return { result: sanitizeResult(fallback(prompt, context, tools)),
             audit: baseAudit(mode, "no_model", { outcome: "deterministic", tool_calls: toolCalls }) };
  }

  // Scoped, two-stage evidence retrieval — only for an evidence-intent MODEL route.
  const evidence = await loadEvidence(sb, prompt, siteId, context);
  const toolsEv = evidence ? { ...tools, evidence } : tools;
  const evToolCalls = evidence
    ? [...toolCalls, "evidence_index", ...(Array.isArray(evidence.bundles) && evidence.bundles.length ? ["evidence_bundle"] : [])]
    : toolCalls;

  // Resolve the mode -> providers from DB (service-role only). A resolver error flows to the floor.
  let resolved: any = null;
  try {
    const r = await service.rpc("wl_ai_resolve_mode", { p_mode: mode, p_needs_vision: false });
    if (!r.error) resolved = r.data;
  } catch { /* resolved stays null */ }

  const { candidates, modeExternalAllowed, primaryInvalid } = buildCandidates(resolved, legacyEnvProvider());

  if (candidates.length === 0) {
    // Nothing configured, or the configured primary is structurally invalid (fail closed).
    const outcome = primaryInvalid ? "config_invalid" : "no_provider_configured";
    return { result: sanitizeResult(fallback(prompt, context, toolsEv)),
             audit: baseAudit(mode, "guided_fallback", { outcome, tool_calls: evToolCalls }) };
  }

  let anyBlocked = false, tried = 0, last = candidates[candidates.length - 1].cfg;
  for (const cand of candidates) {
    last = cand.cfg;
    // Egress gate AFTER resolution: a local-only site can never be sent to an external model, no
    // matter what an admin configured for the mode/provider.
    if (!egressAllowed(cand.cfg, siteAllowsExternal, modeExternalAllowed)) { anyBlocked = true; continue; }
    tried++;
    // Evidence images accompany the prompt ONLY to an egress-permitted provider; the explicit strip
    // keeps a LOCAL-ONLY site's images away from any external model (defense in depth).
    const evForProvider = evidence
      ? (isExternal(cand.cfg) && !(siteAllowsExternal && modeExternalAllowed) ? stripEvidenceImages(evidence) : evidence)
      : null;
    const messages = buildMessages(context, evidence ? { ...tools, evidence: evForProvider } : tools, history);
    try {
      const out = await buildProvider(cand.cfg).chat(messages, { jsonMode: true, temperature: 0.2, maxOutput: cand.cfg.maxOutput });
      if (!out.text) throw new Error("provider_empty_response");
      let parsed: Json;
      try { parsed = sanitizeResult({ ...JSON.parse(String(out.text)), mode: "ai" }); }
      catch { parsed = sanitizeResult({ answer: String(out.text), mode: "ai" }); }
      return { result: parsed, audit: {
        mode, route: cand.isFallback ? "ai_fallback" : "ai_primary",
        provider_id: cand.cfg.id, provider_name: cand.cfg.name, model: cand.cfg.model,
        used_fallback: cand.isFallback, egress: isExternal(cand.cfg) ? "external" : "local",
        latency_ms: out.latencyMs || 0, candidates_tried: tried, tool_calls: evToolCalls, outcome: "ok",
      } };
    } catch { /* try the next configured candidate */ }
  }

  // All candidates were blocked or failed -> verified-data guided fallback (the floor), grounded on
  // the loaded evidence when the question was an evidence query.
  return { result: sanitizeResult(fallback(prompt, context, toolsEv)), audit: {
    mode, route: "guided_fallback",
    provider_id: last?.id ?? null, provider_name: last?.name ?? null, model: last?.model ?? null,
    used_fallback: false, egress: anyBlocked && tried === 0 ? "blocked_local_only" : "n/a",
    latency_ms: 0, candidates_tried: tried, tool_calls: evToolCalls,
    outcome: tried === 0 && anyBlocked ? "egress_blocked" : "all_providers_failed",
  } };
}

Deno.serve(async (req) => {
  if (disallowedOrigin(req)) return response(req, { error: "origin_not_allowed" }, 403);
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors(req) });
  if (req.method !== "POST") return response(req, { error: "method_not_allowed" }, 405);

  const authHeader = req.headers.get("Authorization") || "";
  if (!authHeader.toLowerCase().startsWith("bearer ")) return response(req, { error: "not_authenticated" }, 401);
  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "", anonKey = Deno.env.get("SUPABASE_ANON_KEY") || Deno.env.get("SUPABASE_PUBLISHABLE_KEY") || "", serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!supabaseUrl || !anonKey || !serviceRoleKey) return response(req, { error: "server_not_configured" }, 503);

  const sb = createClient(supabaseUrl, anonKey, { global: { headers: { Authorization: authHeader } }, auth: { persistSession: false, autoRefreshToken: false } });
  const service = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false, autoRefreshToken: false } });
  const { data: { user }, error: userError } = await sb.auth.getUser();
  if (userError || !user) return response(req, { error: "not_authenticated" }, 401);

  let body: Json;
  try { body = await req.json(); } catch { return response(req, { error: "invalid_json" }, 400); }
  const prompt = String(body?.prompt || "").trim(), siteId = String(body?.site_id || "").trim();
  let conversationId = body?.conversation_id ? String(body.conversation_id) : "";
  if (!prompt || prompt.length > 12000) return response(req, { error: "invalid_prompt" }, 400);
  if (!siteId) return response(req, { error: "site_required" }, 400);

  try {
    const tenantRes = await sb.rpc("wl_my_tenant");
    if (tenantRes.error || !tenantRes.data) throw new Error("tenant_context_unavailable");
    const usage = await service.rpc("wl_ai_record_usage", { p_user_id: user.id, p_tenant_id: tenantRes.data, p_prompt_chars: prompt.length, p_minute_limit: 20, p_daily_limit: 500 });
    if (usage.error) throw usage.error;
    if (!usage.data?.ok) return response(req, { error: "rate_limited", message: "WatchLog AI request limit reached. Please retry later.", retry_after_seconds: usage.data?.retry_after_seconds || 60 }, 429);

    if (conversationId) {
      const check = await sb.rpc("wl_ai_conversation_context", { p_conversation_id: conversationId });
      if (check.error) throw check.error;
      if (String(check.data?.site_id || "") !== siteId) return response(req, { error: "conversation_site_mismatch", message: "This conversation belongs to a different site. Start a new chat for the selected site." }, 409);
    } else {
      const created = await sb.rpc("wl_ai_new_conversation", { p_site_id: siteId, p_title: "New conversation" });
      if (created.error) throw created.error;
      conversationId = String(created.data?.id || "");
    }

    const append = await sb.rpc("wl_ai_append_message", { p_conversation_id: conversationId, p_role: "user", p_content: prompt, p_payload: { source: "portal" } });
    if (append.error) throw append.error;

    const [ctxResult, historyResult] = await Promise.all([
      sb.rpc("wl_ai_context", { p_site_id: siteId }),
      sb.rpc("wl_ai_messages", { p_conversation_id: conversationId, p_limit: 40 }),
    ]);
    if (ctxResult.error) throw ctxResult.error;
    if (historyResult.error) throw historyResult.error;
    const tools = await gatherTools(sb, prompt, siteId, ctxResult.data || {});
    const toolCalls = Object.keys(tools).filter((k) => k !== "site_local_date" && (tools as Json)[k] != null);

    // Site data-egress policy (tenant-owned). Unreadable => local-only; never fail open.
    let siteAllowsExternal = false;
    try {
      const eg = await sb.rpc("wl_ai_site_egress", { p_site_id: siteId });
      if (!eg.error) siteAllowsExternal = !!eg.data?.external_egress_allowed;
    } catch { /* default local-only */ }

    const mode = normalizeMode(body?.mode);
    let result: Json, audit: RouteAudit;
    try {
      ({ result, audit } = await routeChat({
        sb, service, prompt, siteId, history: historyResult.data || [], context: ctxResult.data || {},
        tools, mode, siteAllowsExternal, toolCalls,
      }));
    } catch (routerError) {
      console.error("watchlog-ai router error", routerError instanceof Error ? routerError.message : "unknown");
      result = sanitizeResult(fallback(prompt, ctxResult.data || {}, tools));
      audit = baseAudit(mode, "guided_fallback", { outcome: "router_error", tool_calls: toolCalls });
    }
    if (audit.route === "guided_fallback") {
      result.answer += " Full model reasoning is not configured or is temporarily unavailable, so this answer uses verified WatchLog data and deterministic guidance only.";
    }

    // Route audit — mode/provider/model/fallback/egress/latency/tool-calls for Admin + audit ONLY.
    // Provider identities are NEVER placed in the browser response below. Best-effort; never blocks.
    try {
      const logged = await service.rpc("wl_ai_log_route", {
        p: { ...audit, tenant_id: tenantRes.data, site_id: siteId, user_id: user.id, conversation_id: conversationId },
      });
      if (logged.error) console.error("watchlog-ai route log failed", String(logged.error?.message || "").slice(0, 200));
    } catch (logErr) {
      console.error("watchlog-ai route log threw", logErr instanceof Error ? logErr.message : "unknown");
    }

    const saved = await service.rpc("wl_ai_append_assistant_message", { p_conversation_id: conversationId, p_user_id: user.id, p_content: result.answer, p_payload: { cards: result.cards, suggestions: result.suggestions, proposed_actions: result.proposed_actions, mode: result.mode } });
    if (saved.error) throw saved.error;
    return response(req, { ...result, conversation_id: conversationId });
  } catch (error) {
    console.error("watchlog-ai request failed", error instanceof Error ? error.message : "unknown");
    return response(req, { error: "watchlog_ai_failed", message: "WatchLog could not complete that request. Your site configuration was not changed." }, 500);
  }
});
