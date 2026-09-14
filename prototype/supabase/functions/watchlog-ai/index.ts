import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.0";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const SYSTEM_PROMPT = `You are WatchLog AI, the conversational operator for WatchLog CCTV intelligence.

CORE BEHAVIOR
- Answer in concise, calm, operational language for a business/security user.
- The WATCHLOG_CONTEXT JSON supplied with each turn is the factual authority for this tenant/site.
- Never invent a recorder capability, camera state, incident, person identity, count, time, or health state.
- Recorder capability verdicts and evidence classes are authoritative. UNKNOWN means unconfirmed, not unsupported. OFFICIAL_DOCUMENTED means documented, not field verified. FIELD_VERIFIED is the strongest device evidence.
- If a requested feature is not native but WatchLog can provide a software analytic, explain that distinction plainly.
- Never ask for or expose recorder passwords/credentials. They remain on the on-site WatchLog service.
- Never claim unverified time means "no activity". Use LIVE / RECOVERED / UNVERIFIED provenance honestly.
- Behavioral identity is never certain: say estimated, probable, plausible movement journey, or unclassified as appropriate.
- Recorder writes are never silently executed. Any recorder change must be presented as a proposal requiring an authorized human approval and the existing WatchLog Site Control safety gate.
- Dangerous recorder actions (firmware, factory reset, disk formatting/deleting recordings, user/password administration, unsafe network changes) are unavailable.
- Prefer business outcomes over recorder/API jargon. Put technical detail behind an "Advanced" explanation when useful.

SETUP MODE
When setup is incomplete, act like a friendly setup engineer. Ask only the next useful question. Use the supplied onboarding steps, business context, recorder capability profile and cameras. Typical order:
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
Only propose actions supported by WATCHLOG_CONTEXT and the user's permissions. Do not claim an action was executed unless the context/tool result explicitly says so.`;

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

function setupFallback(ctx: any) {
  const steps = ctx?.onboarding?.steps || [];
  const next = steps.find((s: any) => !s?.done);
  const cameras = ctx?.cameras || [];
  const monitored = cameras.filter((c: any) => c.monitor).length;
  const recorder = ctx?.recorder || {};
  const site = ctx?.site?.name || "this site";
  const answer = next
    ? `I can continue setting up ${site}. The next step is ${String(next.label || next.key || "the next setup step").toLowerCase()}.`
    : `${site} has completed the recorded setup checklist. I can now help you check health, review incidents, reporting, or refine monitoring rules.`;
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
      },
    }],
    suggestions: next
      ? ["Continue setup", "Check my cameras", "What can my recorder support?"]
      : ["What happened today?", "Check site health", "Show today's incidents"],
    proposed_actions: [],
    mode: "guided_fallback",
  };
}

function genericFallback(prompt: string, ctx: any) {
  const p = prompt.toLowerCase();
  const cameras = ctx?.cameras || [];
  const faults = ctx?.faults || [];
  const recorder = ctx?.recorder || {};
  const coverage = ctx?.coverage || {};
  if (/setup|configure|connect|install/.test(p)) return setupFallback(ctx);
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
  if (/recorder|nvr|dvr|support|capabilit/.test(p)) {
    return {
      answer: recorder?.model
        ? `This site is using ${[recorder.vendor, recorder.model].filter(Boolean).join(" ")}. I will only describe capabilities recorded in WatchLog's evidence-graded device profile; anything unknown stays unconfirmed.`
        : "WatchLog has not identified the recorder model for this site yet. Once the on-site WatchLog service reports it, I can resolve the exact capability profile instead of guessing.",
      cards: [{ type: "recorder", title: "Recorder", data: { recorder, capabilities: ctx?.capabilities || [], capability_known: ctx?.capability_known } }],
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

async function callModel(prompt: string, history: any[], context: any) {
  const endpoint = Deno.env.get("WATCHLOG_AI_ENDPOINT") || "";
  const apiKey = Deno.env.get("WATCHLOG_AI_API_KEY") || "";
  const model = Deno.env.get("WATCHLOG_AI_MODEL") || "";
  if (!endpoint || !apiKey || !model) return genericFallback(prompt, context);

  const messages = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "system", content: `WATCHLOG_CONTEXT\n${JSON.stringify(compactContext(context))}` },
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

  // User-scoped client: every factual read and user write still passes the caller's JWT
  // through WatchLog's existing tenant/RLS/RPC authorization boundary.
  const sb = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: { user }, error: userError } = await sb.auth.getUser();
  if (userError || !user) return jsonResponse({ error: "not_authenticated" }, 401);

  // Server-only client is used for one narrow operation: persisting the assistant's
  // response after the user-scoped context has already been authorized and loaded.
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

    let result;
    try {
      result = await callModel(prompt, historyResult.data || [], ctxResult.data || {});
    } catch (modelError) {
      console.error("watchlog-ai provider error", modelError instanceof Error ? modelError.message : "unknown");
      result = genericFallback(prompt, ctxResult.data || {});
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
