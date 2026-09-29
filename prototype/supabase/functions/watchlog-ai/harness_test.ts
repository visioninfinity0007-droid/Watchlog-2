// deno test prototype/supabase/functions/watchlog-ai/harness_test.ts
import { assert, assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { applyCustomerVocabulary, customerCardData, harnessMessage, resolveTenantBrief } from "./harness.ts";

// ---- customer vocabulary (ai-harness/core/customer-vocabulary.yaml) ----------------------------
Deno.test("internal wording never reaches a customer answer", () => {
  const leaked = [
    "Frames manually reviewed: 913 / 913",
    "Every available service-day snapshot across all 8 canonical cameras; no sampling",
    "Visual review completed for 610 snapshots.",
    "The vision worker on Groq flagged this frame.",
  ];
  for (const s of leaked) {
    const out = applyCustomerVocabulary(s);
    assert(!/snapshot|frame|canonical|sampling|visual review|vision worker|groq/i.test(out), `leaked: ${out}`);
  }
});

Deno.test("business wording and line breaks are left alone", () => {
  for (const s of ["No sales, queue length or transaction totals are inferred.", "Share the recorder model and your sales-pipeline system.",
                   "*Site*\n\n• Reviewed: afternoon\n• Note: all clear"]) {
    assertEquals(applyCustomerVocabulary(s), s);
  }
});

Deno.test("customer cards carry no image counts and no internal wording", () => {
  const card = customerCardData({ snapshots_reviewed: 913, frames_total: 913, sampling: false, status: "Visual review complete",
                                  gaps: ["4:00-4:39 PM"], nested: { note: "913 snapshots" } });
  assertEquals(Object.keys(card).sort(), ["gaps", "nested", "status"]);
  assert(!/snapshot|visual review/i.test(JSON.stringify(card)));
});
import { CORE_BRIEF, SITE_TYPE_BRIEFS, TENANT_BRIEFS } from "./harness_brief.generated.ts";
import { providerEgress, recentCalendar, resolveNamedDay } from "./providers/router.ts";

Deno.test("named weekdays resolve to the right site-local date (no model date arithmetic)", () => {
  // 2026-09-28 is a Monday.
  assertEquals(resolveNamedDay("was there activity last Saturday?", "2026-09-28"), "2026-09-26");
  assertEquals(resolveNamedDay("what happened on friday", "2026-09-28"), "2026-09-25");
  assertEquals(resolveNamedDay("anything last monday?", "2026-09-28"), "2026-09-21");
  assertEquals(resolveNamedDay("anything monday?", "2026-09-28"), "2026-09-28");
  assertEquals(resolveNamedDay("what happened yesterday", "2026-09-28"), null);
  assertEquals(recentCalendar("2026-09-28", 3), ["2026-09-28 Monday (today)", "2026-09-27 Sunday (calendar yesterday)", "2026-09-26 Saturday"]);
});
import type { ProviderConfig } from "./providers/types.ts";

const ctx = (name: string, siteType: string | null, prefs: Record<string, unknown> = {}) =>
  ({ site: { name }, business_context: siteType ? { site_type: siteType, reporting_prefs: prefs } : null });

Deno.test("each governed tenant resolves by exact site name + site type", () => {
  assertEquals(resolveTenantBrief(ctx("Chai Wala - Chota Bukhari", "restaurant"))?.key, "chaiwala-chota-bukhari");
  assertEquals(resolveTenantBrief(ctx("Head Office", "office"))?.key, "hasco-steel-head-office");
  assertEquals(resolveTenantBrief(ctx("Main site", "office"))?.key, "al-khalid-main-site");
});

Deno.test("an explicit harness_tenant key wins; an unknown key never falls back to name matching", () => {
  assertEquals(resolveTenantBrief(ctx("Anything", "office", { harness_tenant: "hasco-steel-head-office" }))?.key, "hasco-steel-head-office");
  assertEquals(resolveTenantBrief(ctx("Main site", "office", { harness_tenant: "does-not-exist" })), null);
});

Deno.test("a same-named site without a matching site type gets NO tenant brief (no cross-tenant context)", () => {
  // Production has a second, empty 'Main site' (another tenant, no business context).
  assertEquals(resolveTenantBrief(ctx("Main site", null)), null);
  assertEquals(resolveTenantBrief(ctx("Main site", "restaurant")), null);
  assertEquals(resolveTenantBrief(ctx("Chai Wala Restaurant", "restaurant")), null);
  const msg = harnessMessage(ctx("Main site", null));
  assert(msg.includes("no governed context for this site yet"));
  assert(!msg.includes("TENANT CONTEXT: Main site"));
});

Deno.test("the harness tells the model the site timezone and that tool timestamps are UTC", () => {
  const msg = harnessMessage({ site: { name: "Chai Wala - Chota Bukhari", timezone: "Asia/Karachi" }, business_context: { site_type: "restaurant" } });
  assert(msg.includes("Site timezone: Asia/Karachi"));
  assert(msg.includes("never present a UTC clock time as local"));
});

Deno.test("a tenant's message carries core rules, its site-type policy and only its own context", () => {
  const msg = harnessMessage(ctx("Chai Wala - Chota Bukhari", "restaurant"));
  assert(msg.startsWith("WATCHLOG_HARNESS"));
  assert(msg.includes(CORE_BRIEF));
  assert(msg.includes(SITE_TYPE_BRIEFS.restaurant));
  assert(msg.includes("TENANT CONTEXT: Chai Wala - Chota Bukhari"));
  for (const other of ["Al-Khalid", "HASCO", "Armory Gate", "Head Office"]) assert(!msg.includes(other), `leaked ${other}`);
});

Deno.test("shared harness text names no tenant and no internal implementation terms", () => {
  const shared = CORE_BRIEF + Object.values(SITE_TYPE_BRIEFS).join("\n");
  for (const t of TENANT_BRIEFS) assert(!shared.includes(t.siteName) || t.siteName === "Main site", `shared text names ${t.siteName}`);
  for (const bad of ["Al-Khalid", "XVR1B08", "wl_", "site_business_context", "MediaProfile", ".yaml", "device-knowledge"]) {
    assert(!shared.includes(bad), `shared text contains ${bad}`);
    for (const t of TENANT_BRIEFS) {
      if (bad === "Al-Khalid" && t.key === "al-khalid-main-site") continue;
      assert(!t.brief.includes(bad) || bad === "Al-Khalid", `${t.key} brief contains ${bad}`);
    }
  }
});

Deno.test("the brief stays compact enough for free-tier token limits", () => {
  for (const t of TENANT_BRIEFS) {
    const chars = harnessMessage({ site: { name: t.siteName }, business_context: { site_type: t.siteType } }).length;
    assert(chars < 12000, `${t.key} harness message is ${chars} chars`);
  }
});

const ext = { privacy: "EXTERNAL", externalEgress: true } as unknown as ProviderConfig;
const local = { privacy: "LOCAL", externalEgress: false } as unknown as ProviderConfig;

Deno.test("text-only consent reaches an external model with images withheld", () => {
  assertEquals(providerEgress(ext, false, true, true), { allowed: true, stripImages: true, decision: "external_text_only" });
});
Deno.test("full consent sends images; no consent blocks; mode must permit external in both cases", () => {
  assertEquals(providerEgress(ext, true, false, true), { allowed: true, stripImages: false, decision: "external" });
  assertEquals(providerEgress(ext, false, false, true).allowed, false);
  assertEquals(providerEgress(ext, false, true, false).allowed, false);
  assertEquals(providerEgress(ext, true, true, false).allowed, false);
  assertEquals(providerEgress(local, false, false, false), { allowed: true, stripImages: false, decision: "local" });
});
