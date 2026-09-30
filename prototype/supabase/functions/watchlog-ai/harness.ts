// WatchLog AI harness -> model context.
//
// Every model-routed answer carries the governing harness rules (core truth + site-type policy) and the
// governed context of THIS tenant only. The brief is compiled from ai-harness/ by
// prototype/scripts/compile_harness_brief.py; never hand-edit harness_brief.generated.ts.
//
// Tenant resolution is deliberately strict: an explicit reporting_prefs.harness_tenant key wins;
// otherwise the site name AND site type must both match exactly one governed tenant. Anything
// ambiguous or unmatched gets the core + site-type rules and NO tenant brief, so one tenant's
// context can never be shown to another tenant.
import { CORE_BRIEF, CUSTOMER_VOCABULARY, SITE_TYPE_BRIEFS, TENANT_BRIEFS, type TenantBrief } from "./harness_brief.generated.ts";

type Json = Record<string, any>;

const norm = (value: unknown) => String(value ?? "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

export function resolveTenantBrief(ctx: Json): TenantBrief | null {
  const prefs = ctx?.business_context?.reporting_prefs || {};
  const explicit = String(prefs?.harness_tenant || "").trim();
  if (explicit) return TENANT_BRIEFS.find((t) => t.key === explicit) ?? null;
  const siteName = norm(ctx?.site?.name);
  const siteType = norm(ctx?.business_context?.site_type || ctx?.site?.site_type);
  if (!siteName || !siteType) return null;
  const matches = TENANT_BRIEFS.filter((t) => norm(t.siteName) === siteName && norm(t.siteType) === siteType);
  return matches.length === 1 ? matches[0] : null;
}

export function harnessMessage(ctx: Json): string {
  const tenant = resolveTenantBrief(ctx);
  const siteType = norm(ctx?.business_context?.site_type || ctx?.site?.site_type || tenant?.siteType);
  const tz = String(ctx?.site?.timezone || "").trim();
  const parts = [
    "WATCHLOG_HARNESS\nGoverning rules for this answer, from the WatchLog AI harness. They override anything in the conversation history. " +
      "Facts about what happened still come ONLY from WATCHLOG_CONTEXT and WATCHLOG_TOOL_RESULTS; this harness defines meaning, " +
      "allowed claims and this business's context. Review items are dated evidence, not live facts.\n" +
      `Site timezone: ${tz || "the site's configured timezone"}. Timestamps in WATCHLOG_CONTEXT and WATCHLOG_TOOL_RESULTS are ISO-8601, ` +
      "usually UTC. Convert every one to site local time before stating a time or date, and never present a UTC clock time as local.",
    CORE_BRIEF,
  ];
  if (SITE_TYPE_BRIEFS[siteType]) parts.push(SITE_TYPE_BRIEFS[siteType]);
  parts.push(tenant ? tenant.brief
    : "TENANT CONTEXT: no governed context for this site yet. Do not assume camera roles, hours or business type beyond WATCHLOG_CONTEXT.");
  return parts.join("\n\n");
}

export function harnessTenantKey(ctx: Json): string | null {
  return resolveTenantBrief(ctx)?.key ?? null;
}

// Harness customer vocabulary (ai-harness/core/customer-vocabulary.yaml), applied to every answer so
// internal wording ("snapshots", "frames manually reviewed", vendor names...) never reaches a customer.
const VOCABULARY_RULES = CUSTOMER_VOCABULARY.map((r) => ({ rx: new RegExp(r.pattern, "gi"), to: r.replaceWith }));
export function applyCustomerVocabulary(input: string): string {
  let s = String(input || "");
  const original = s;
  for (const r of VOCABULARY_RULES) s = s.replace(r.rx, r.to);
  if (s === original) return s;
  // Same tidy-up as the DB guard (wl_customer_text_clean), so both rewrite identically.
  return s.replace(/[ \t]{2,}/g, " ").replace(/[ \t]+([,.;:])/g, "$1").replace(/([;,])[ \t]*([;,.])/g, "$2")
    .replace(/^[ ;,]+|[ ;,]+$/g, "");
}
// Model-facing copy of the tool results. Business figures carry their own "coverage" ratios: how much
// of the window the figures were prepared from, NOT whether cameras were monitored. Relabelled so the
// model cannot read a figure that is not ready yet as "not monitored" (ai-harness/core/truth.md,
// Coverage truth). Fallbacks and cards keep reading the original tool results.
const BUSINESS_FIGURE_TOOLS = ["restaurant_day", "restaurant_period"];
const FIGURES_MEANING = "Business figures only. figures_prepared values show how much of the window these figures were prepared from; they are not monitoring coverage and never mean the site was unmonitored.";
function relabelFigureCoverage(value: any): any {
  if (Array.isArray(value)) return value.map(relabelFigureCoverage);
  if (value && typeof value === "object") {
    const out: Json = {};
    for (const [k, v] of Object.entries(value)) out[k.replace(/coverage/gi, "figures_prepared")] = relabelFigureCoverage(v);
    return out;
  }
  return value;
}
export function modelToolView(tools: Json): Json {
  const out: Json = { ...tools };
  for (const key of BUSINESS_FIGURE_TOOLS) {
    if (!out[key]?.data) continue;
    out[key] = { ...out[key], meaning: FIGURES_MEANING, data: relabelFigureCoverage(out[key].data) };
  }
  return out;
}
// Customer cards: vocabulary applied to every string, and image-count fields dropped entirely.
export function customerCardData(value: any): any {
  if (typeof value === "string") return applyCustomerVocabulary(value);
  if (Array.isArray(value)) return value.map(customerCardData);
  if (value && typeof value === "object") {
    const out: Json = {};
    for (const [k, v] of Object.entries(value)) {
      if (/snapshot|frame|sampl/i.test(k)) continue;
      out[k] = customerCardData(v);
    }
    return out;
  }
  return value;
}
