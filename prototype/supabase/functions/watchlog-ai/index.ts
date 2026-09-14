import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const SYSTEM_PROMPT = `You are WatchLog AI, the conversational operator for WatchLog CCTV intelligence.

CORE BEHAVIOR
- Answer in concise, calm, operational language for a business/security user.
- WATCHLOG_CONTEXT and WATCHLOG_TOOL_RESULTS are the factual authority for this tenant/site.
- Never invent a recorder capability, camera state, incident, person identity, count, time, health state, report or tool result.
- Recorder capability verdicts and evidence classes are authoritative. UNKNOWN means unconfirmed, not unsupported. OFFICIAL_DOCUMENTED means documented, not field verified. FIELD_VERIFIED is the strongest device evidence.
- The deterministic setup advisor is authoritative for whether a recorder configuration can be proposed. Do not weaken its evidence/safety gate.
- If a requested feature is not native but WatchLog can provide a software analytic, explain that distinction plainly.
- Never ask for or expose recorder passwords/credentials. They remain on the on-site WatchLog service.
- Never claim unverified time means "no activity". Use LIVE / RECOVERED / UNVERIFIED provenance honestly.
- Behavioral identity is never certain: say estimated, probable, plausible movement journey, or unclassified as appropriate.
- Recorder writes are never silently executed. Any recorder change must be presented as a proposal requiring an authorized human approval and the existing WatchLog Site Control safety gate.
- Dangerous recorder actions (firmware, factory reset, disk formatting/deleting recordings, user/password administration, unsafe network changes) are unavailable.
- Prefer business outcomes over recorder/API jargon. Put technical detail behind an "Advanced" explanation when useful.
- A frozen report snapshot is the authority for a report that has already been generated. Do not silently regenerate or rewrite a historical customer report.

SETUP MODE
When setup is incomplete, act like a friendly setup engineer. Ask only the next useful question. Use the supplied onboarding steps, business context, recorder capability profile, deterministic setup advisor and cameras. Typical order:
1) site type / operating context,
2) WatchLog connection + recorder connection,
3) camera Monitor/Ignore + friendly names + purposes,
4) business hours / entrance and restricted mappings,
5) capability-aware recommendations,
6) human review/approval,
7) verification/readiness.
Do not claim a step is done unless WATCHLOG_CONTEXT says it is done.

OUTPUT
Return JSON only, with this shape:
{
  "answer": "natural-language response",
  "cards": [
    {"type":"health|coverage|recorder|cameras|capabilities|setup|incident|report|approval","title":"...","data":{}}
  ],
  "suggestions": ["short next prompt", "short next prompt"],
  "proposed_actions": [
    {"kind":"navigate|setup_context|setup_camera|watchlog_rule|site_control_proposal","label":"...","data":{}}
  ]
}
Only propose actions supported by WATCHLOG_CONTEXT, WATCHLOG_TOOL_RESULTS and the user's permissions. Do not claim an action was executed unless a tool result explicitly says so.`;

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

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

function compactContext(ctx: any) {
  if (!ctx || typeof ctx !== "object") return {};
  return {
    facts_version: ctx.facts_version,
    generated_at: ctx.generated_at,
    site: ctx.site,
    business_context: ctx.business_context,
    onboarding: ctx.onboarding,
    recorder: ctx.recorder,
    connectivity: ctx.connectivity,
    capabilities: ctx.capabilities,
    capability_known: ctx.capability_known,
    cameras: ctx.cameras,
    faults: ctx.faults,
    coverage: ctx.coverage,
    permissions: ctx.permissions,
    recent_events: (ctx.recent_events || []).slice(0, 12),
    safety: ctx.safety,
  };
}

function dateInZone(timeZone: string | undefined, offsetDays = 0) {
  const d = new Date(Date.now() + offsetDays * 86400000);
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timeZone || "UTC", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(d);
    const part = (type: string) => parts.find((p) => p.type === type)?.value || "";
    return `${part("year")}-${part("month")}-${part("day")}`;
  } catch {
    return d.toISOString().slice(0, 10);
  }
}

function capabilityMap(profile: any) {
  const list = Array.isArray(profile) ? profile : [];
  const out: Record<string, any> = {};
  for (const c of list) if (c?.capability) out[String(c.capability)] = c;
  return out;
}

// Mirrors prototype/advisor/site_config_advisor.py. The model receives this deterministic
// package and may explain it, but may not relax its capability/evidence/safety decisions.
function deterministicSetupAdvice(ctx: any) {
  const profile = capabilityMap(ctx?.capabilities);
  const bc = ctx?.business_context || {};
  const siteType = bc.site_type || ctx?.site?.site_type || "other";
  const goals = SITE_TYPE_GOALS[siteType] || ["human_vehicle_classification", "after_hours"];
  const recommendations: any[] = [];
  const recorderProposals: any[] = [];
  const software: string[] = [];
  const unavailable: any[] = [];
  const questions: string[] = [];

  const monitored = (ctx?.cameras || []).filter((c: any) => c.monitor !== false);
  const hasPurpose = (terms: string[]) => monitored.some((c: any) => terms.includes(String(c.purpose || "").toLowerCase()));
  const hoursKnown = !!(bc.open_time && bc.close_time && Array.isArray(bc.working_days) && bc.working_days.length);

  for (const analytic of goals) {
    const cap = profile[analytic] || {
      capability: analytic, verdict: "unknown", evidence_class: "UNKNOWN",
      write: null, safety_class: "na",
    };
    const verdict = cap.verdict || "unknown";
    const evidence = cap.evidence_class || "UNKNOWN";

    if (verdict === "supported" && evidence === "FIELD_VERIFIED" && cap.write === true && cap.safety_class === "safe_write") {
      recommendations.push({ analytic, decision: "recorder_configure", evidence_class: evidence,
        rationale: "Field-verified safe recorder configuration is available for this exact model." });
      recorderProposals.push({ analytic, capability: analytic, safety_class: "safe_write", evidence_class: evidence, requires_approval: true });
    } else if (verdict === "supported") {
      recommendations.push({ analytic, decision: "recorder_needs_verification", evidence_class: evidence,
        rationale: "Recorder support is recorded, but WatchLog will not write until the exact safe path is field-verified." });
    } else if (verdict === "by_camera") {
      recommendations.push({ analytic, decision: "camera_side", evidence_class: evidence,
        rationale: "This capability belongs to the camera rather than the recorder." });
    } else if (SOFTWARE_ANALYTICS.has(analytic)) {
      recommendations.push({ analytic, decision: "watchlog_software", evidence_class: evidence,
        rationale: verdict === "unsupported" ? "The recorder does not provide this natively; WatchLog software analytics can provide it." : "Recorder support is unconfirmed; WatchLog software analytics can provide it without assuming recorder support." });
      software.push(analytic);
    } else {
      recommendations.push({ analytic, decision: "not_available", evidence_class: evidence,
        rationale: verdict === "unsupported" ? "Not supported by this recorder and not offered as a WatchLog software analytic." : "Recorder support is unconfirmed and WatchLog does not claim this software capability." });
      unavailable.push({ analytic, verdict, evidence_class: evidence });
    }

    if (verdict === "unknown") questions.push(`Recorder support for ${analytic.replaceAll("_", " ")} is unconfirmed; run a safe read before relying on it.`);
    if (analytic === "after_hours" && !hoursKnown) questions.push("Confirm this site's operating hours and working days.");
    if (analytic === "restricted_area" && !hasPurpose(["restricted"])) questions.push("Which monitored cameras view restricted or sensitive areas?");
    if ((analytic === "line_crossing" || analytic === "intrusion") && !hasPurpose(["entrance","perimeter","loading"])) questions.push("Which monitored cameras cover entrances, loading access or perimeter crossings?");
  }

  return {
    advisor_version: "site-config-advisor-v1-compatible",
    site_type: siteType,
    recorder: ctx?.recorder || {},
    recommendations,
    site_control_proposals: recorderProposals,
    software_analytics: [...new Set(software)].sort(),
    unavailable,
    human_questions: [...new Set(questions)],
    note: "Recommendation only; recorder changes require authorized human approval and WatchLog safety verification.",
  };
}

async function rpcOptional(sb: any, name: string, args: any) {
  const { data, error } = await sb.rpc(name, args);
  if (!error) return { ok: true, data };
  const msg = String(error?.message || "");
  if (new RegExp(`${name}|schema cache|function`, "i").test(msg)) return { ok: false, unavailable: true };
  return { ok: false, error: msg };
}

async function gatherToolResults(sb: any, prompt: string, siteId: string, ctx: any) {
  const p = prompt.toLowerCase();
  const tz = ctx?.site?.timezone || "UTC";
  const today = dateInZone(tz, 0);
  const yesterday = dateInZone(tz, -1);
  const out: any = {
    site_local_date: today,
    setup_advisor: null,
    daily_intelligence: null,
    frozen_report: null,
    analytics: null,
  };

  const setupIntent = /setup|configure|configuration|support|capabilit|recorder|nvr|dvr|monitoring rule|what can/.test(p);
  if (setupIntent) out.setup_advisor = deterministicSetupAdvice(ctx);

  const overnight = /overnight|last night|yesterday/.test(p);
  const intelligenceIntent = overnight || /what happened|today|incident|activity|people|visitor|staff|after.?hours|opening|closing|journey|restricted|dwell/.test(p);
  if (intelligenceIntent) {
    if (overnight) {
      const [prior, current] = await Promise.all([
        rpcOptional(sb, "wl_my_daily_intelligence", { p_site_id: siteId, p_date: yesterday }),
        rpcOptional(sb, "wl_my_daily_intelligence", { p_site_id: siteId, p_date: today }),
      ]);
      out.daily_intelligence = { interpretation: "overnight_window", yesterday: prior, today: current };
    } else {
      out.daily_intelligence = await rpcOptional(sb, "wl_my_daily_intelligence", { p_site_id: siteId, p_date: today });
    }
  }

  const reportIntent = /report|management brief|daily brief|pdf|executive summary/.test(p);
  if (reportIntent) {
    const requestedDate = /yesterday|last night/.test(p) ? yesterday : today;
    out.frozen_report = await rpcOptional(sb, "wl_my_report_snapshot", { p_site_id: siteId, p_date: requestedDate });
  }

  const analyticsIntent = /analytics|trend|visitor flow|vehicle flow|occupancy|busiest|dwell|traffic/.test(p);
  if (analyticsIntent) {
    const days = /30 day|month/.test(p) ? 30 : /7 day|week/.test(p) ? 7 : 1;
    out.analytics = await rpcOptional(sb, "wl_analytics_overview", { p_days: days, p_site_id: siteId });
  }

  return out;
}

function setupFallback(ctx: any, tools: any) {
  const steps = ctx?.onboarding?.steps || [];
  const next = steps.find((s: any) => !s?.done);
  const cameras = ctx?.cameras || [];
  const monitored = cameras.filter((c: any) => c.monitor).length;
  const recorder = ctx?.recorder || {};
  const site = ctx?.site?.name || "this site";
  const advice = tools?.setup_advisor || deterministicSetupAdvice(ctx);
  const answer = next
    ? `I can continue setting up ${site}. The next recorded step is ${String(next.label || next.key || "the next setup step").toLowerCase()}. I have also checked the exact recorder capability profile so I will not recommend unsupported recorder features.`
    : `${site} has completed the recorded setup checklist. I can still refine monitoring using the evidence-graded recorder profile and WatchLog software analytics.`;
  return {
    answer,
    cards: [{
      type: "setup",
      title: "WatchLog setup",
      data: {
        site,
        recorder: [recorder.vendor, recorder.model].filter(Boolean).join(" ") || "Not identified yet",
        cameras_discovered: cameras.length,
        cameras_monitored: monitored,
        steps,
        recommendation_summary: advice?.recommendations,
        software_analytics: advice?.software_analytics,
        human_questions: advice?.human_questions,
      },
    }],
    suggestions: next
      ? ["Continue setup", "Check my cameras", "What can my recorder support?"]
      : ["What happened today?", "Check site health", "Show today's incidents"],
    proposed_actions: [],
    mode: "guided_fallback",
  };
}

function dailyFallback(tools: any, ctx: any) {
  const daily = tools?.daily_intelligence;
  if (!daily) return null;
  const unwrap = (v: any) => v?.ok ? v.data : null;
  const datasets = daily?.interpretation === "overnight_window" ? [unwrap(daily.yesterday), unwrap(daily.today)].filter(Boolean) : [unwrap(daily)].filter(Boolean);
  if (!datasets.length) return null;
  const incidents = datasets.flatMap((d: any) => Array.isArray(d?.incidents) ? d.incidents : []);
  const attention = datasets.reduce((n: number, d: any) => n + Number(d?.attention?.incidents_total || 0), 0);
  const caveats = [...new Set(datasets.flatMap((d: any) => Array.isArray(d?.honesty) ? d.honesty : []))];
  const overnight = daily?.interpretation === "overnight_window";
  return {
    answer: `${overnight ? "For the overnight window" : "For today"}, WatchLog's canonical intelligence dataset shows ${attention} incident${attention === 1 ? "" : "s"} requiring classification in the selected period.${caveats.length ? ` ${caveats[0]}` : ""}`,
    cards: [
      { type: "incident", title: overnight ? "Overnight intelligence" : "Today's intelligence", data: { incidents: incidents.slice(0, 8), attention } },
      ...(datasets[0]?.coverage ? [{ type: "coverage", title: "Monitoring coverage", data: datasets[0].coverage }] : []),
    ],
    suggestions: ["Show the incidents", "Explain monitoring coverage", "Check site health"],
    proposed_actions: [{ kind: "navigate", label: "Open incidents", data: { href: "/incidents/" } }],
    mode: "guided_fallback",
  };
}

function genericFallback(prompt: string, ctx: any, tools: any) {
  const p = prompt.toLowerCase();
  const cameras = ctx?.cameras || [];
  const faults = ctx?.faults || [];
  const recorder = ctx?.recorder || {};
  const coverage = ctx?.coverage || {};
  if (/setup|configure|connect|install|what can|capabilit/.test(p)) return setupFallback(ctx, tools);

  const daily = dailyFallback(tools, ctx);
  if (daily && /overnight|last night|yesterday|what happened|today|incident|activity|people|visitor|staff|after.?hours|opening|closing|journey|restricted|dwell/.test(p)) return daily;

  if (/report|management brief|daily brief|pdf|executive summary/.test(p)) {
    const report = tools?.frozen_report;
    if (report?.ok && report.data) {
      return {
        answer: `A frozen WatchLog report exists for ${report.data.report_date}. I am using that saved report rather than recomputing historical figures.`,
        cards: [{ type: "report", title: `Report — ${report.data.report_date}`, data: report.data }],
        suggestions: ["Summarize the report", "Show incidents in this report", "Open Reports"],
        proposed_actions: [{ kind: "navigate", label: "Open Reports", data: { href: "/reports/" } }], mode: "guided_fallback",
      };
    }
    return {
      answer: "No frozen report is available for that requested day yet. I will not fabricate or silently regenerate a historical customer report.",
      cards: [], suggestions: ["Open Reports", "What happened today?"],
      proposed_actions: [{ kind: "navigate", label: "Open Reports", data: { href: "/reports/" } }], mode: "guided_fallback",
    };
  }

  if (/camera|health|offline|recording/.test(p)) {
    const offline = cameras.filter((c: any) => c.monitor && c.health_state === "offline");
    return {
      answer: offline.length
        ? `${offline.length} monitored camera${offline.length === 1 ? " is" : "s are"} currently offline. I can guide you through the affected camera${offline.length === 1 ? "" : "s"} and recording state.`
        : `I can see ${cameras.filter((c: any) => c.monitor).length} monitored camera${cameras.filter((c: any) => c.monitor).length === 1 ? "" : "s"}. No monitored camera is currently marked offline in the latest WatchLog context.`,
      cards: [{ type: "health", title: "Camera health", data: { cameras, faults } }],
      suggestions: ["Which cameras are not recording?", "Check my recorder", "Show monitoring coverage"],
      proposed_actions: [], mode: "guided_fallback",
    };
  }
  if (/recorder|nvr|dvr|support/.test(p)) {
    return {
      answer: recorder?.model
        ? `This site is using ${[recorder.vendor, recorder.model].filter(Boolean).join(" ")}. I will only describe capabilities recorded in WatchLog's evidence-graded device profile; anything unknown stays unconfirmed.`
        : "WatchLog has not identified the recorder model for this site yet. Once the on-site WatchLog service reports it, I can resolve the exact capability profile instead of guessing.",
      cards: [{ type: "recorder", title: "Recorder", data: { recorder, capabilities: ctx?.capabilities || [], capability_known: ctx?.capability_known, recommendation: tools?.setup_advisor } }],
      suggestions: ["What analytics can this recorder support?", "Check recorder health", "Continue setup"],
      proposed_actions: [], mode: "guided_fallback",
    };
  }
  if (/coverage|downtime|missed|recovered|unverified/.test(p)) {
    return {
      answer: "WatchLog keeps live, recovered and unverified monitoring time separate. The coverage card shows the latest verified coverage for this site; unverified time is never treated as 'no activity'.",
      cards: [{ type: "coverage", title: "Monitoring coverage", data: coverage }],
      suggestions: ["Explain any unverified time", "Was anything recovered from the recorder?", "Check site health"],
      proposed_actions: [], mode: "guided_fallback",
    };
  }
  const recent = ctx?.recent_events || [];
  return {
    answer: recent.length
      ? `I have the latest WatchLog context for ${ctx?.site?.name || "this site"}, including recorder capability truth, camera health, monitoring coverage and ${recent.length} recent event${recent.length === 1 ? "" : "s"}. Full AI reasoning is not configured in this environment yet, so I am using verified WatchLog guidance only.`
      : `I have the current WatchLog context for ${ctx?.site?.name || "this site"}. Full AI reasoning is not configured in this environment yet, so setup and health guidance use verified WatchLog data only.`,
    cards: [{ type: "health", title: "Current site", data: { site: ctx?.site, connectivity: ctx?.connectivity, faults: ctx?.faults, coverage } }],
    suggestions: ["Check my cameras", "Continue setup", "What can my recorder support?"],
    proposed_actions: [], mode: "guided_fallback",
  };
}

async function callModel(prompt: string, history: any[], context: any, tools: any) {
  const endpoint = Deno.env.get("WATCHLOG_AI_ENDPOINT") || "";
  const apiKey = Deno.env.get("WATCHLOG_AI_API_KEY") || "";
  const model = Deno.env.get("WATCHLOG_AI_MODEL") || "";
  if (!endpoint || !apiKey || !model) return genericFallback(prompt, context, tools);

  const messages = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "system", content: `WATCHLOG_CONTEXT\n${JSON.stringify(compactContext(context))}` },
    { role: "system", content: `WATCHLOG_TOOL_RESULTS\n${JSON.stringify(tools)}` },
    ...history.slice(-18).filter((m: any) => m?.role === "user" || m?.role === "assistant").map((m: any) => ({ role: m.role, content: m.content })),
  ];

  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${apiKey}` },
    body: JSON.stringify({ model, messages, temperature: 0.2 }),
  });
  if (!response.ok) throw new Error(`AI provider returned ${response.status}`);
  const payload = await response.json();
  const text = payload?.choices?.[0]?.message?.content ?? payload?.output_text ?? payload?.response ?? "";
  if (!text) throw new Error("AI provider returned no response text");

  try {
    const parsed = JSON.parse(text);
    if (!parsed?.answer) throw new Error("missing answer");
    return {
      answer: String(parsed.answer),
      cards: Array.isArray(parsed.cards) ? parsed.cards : [],
      suggestions: Array.isArray(parsed.suggestions) ? parsed.suggestions.slice(0, 4) : [],
      proposed_actions: Array.isArray(parsed.proposed_actions) ? parsed.proposed_actions : [],
      mode: "ai",
    };
  } catch {
    return { answer: String(text), cards: [], suggestions: [], proposed_actions: [], mode: "ai" };
  }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);

  const authHeader = req.headers.get("Authorization") || "";
  if (!authHeader.toLowerCase().startsWith("bearer ")) return jsonResponse({ error: "not_authenticated" }, 401);

  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY") || Deno.env.get("SUPABASE_PUBLISHABLE_KEY") || "";
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!supabaseUrl || !anonKey || !serviceRoleKey) return jsonResponse({ error: "server_not_configured" }, 503);

  const sb = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: { user }, error: userError } = await sb.auth.getUser();
  if (userError || !user) return jsonResponse({ error: "not_authenticated" }, 401);

  // Service role is deliberately limited here to persisting server-authored assistant history.
  // All customer facts/tools below still execute through the caller-JWT client.
  const service = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  let body: any;
  try { body = await req.json(); } catch { return jsonResponse({ error: "invalid_json" }, 400); }
  const prompt = String(body?.prompt || "").trim();
  const siteId = body?.site_id || null;
  let conversationId = body?.conversation_id || null;
  if (!prompt || prompt.length > 12000) return jsonResponse({ error: "invalid_prompt" }, 400);
  if (!siteId) return jsonResponse({ error: "site_required" }, 400);

  try {
    if (!conversationId) {
      const { data, error } = await sb.rpc("wl_ai_new_conversation", { p_site_id: siteId, p_title: "New conversation" });
      if (error) throw error;
      conversationId = data?.id;
    }

    const append = await sb.rpc("wl_ai_append_message", {
      p_conversation_id: conversationId,
      p_role: "user",
      p_content: prompt,
      p_payload: { source: "portal" },
    });
    if (append.error) throw append.error;

    const [ctxResult, historyResult] = await Promise.all([
      sb.rpc("wl_ai_context", { p_site_id: siteId }),
      sb.rpc("wl_ai_messages", { p_conversation_id: conversationId, p_limit: 40 }),
    ]);
    if (ctxResult.error) throw ctxResult.error;
    if (historyResult.error) throw historyResult.error;

    const tools = await gatherToolResults(sb, prompt, siteId, ctxResult.data || {});

    let result;
    try {
      result = await callModel(prompt, historyResult.data || [], ctxResult.data || {}, tools);
    } catch (modelError) {
      console.error("watchlog-ai provider error", modelError instanceof Error ? modelError.message : "unknown");
      result = genericFallback(prompt, ctxResult.data || {}, tools);
      result.answer += " The full AI reasoning service is temporarily unavailable, so I am showing verified WatchLog guidance only.";
    }

    const saved = await service.rpc("wl_ai_append_assistant_message", {
      p_conversation_id: conversationId,
      p_user_id: user.id,
      p_content: result.answer,
      p_payload: { cards: result.cards, suggestions: result.suggestions, proposed_actions: result.proposed_actions, mode: result.mode },
    });
    if (saved.error) throw saved.error;

    return jsonResponse({ ...result, conversation_id: conversationId });
  } catch (error) {
    console.error("watchlog-ai request failed", error instanceof Error ? error.message : "unknown");
    return jsonResponse({ error: "watchlog_ai_failed", message: "WatchLog could not complete that request. Your site configuration was not changed." }, 500);
  }
});
