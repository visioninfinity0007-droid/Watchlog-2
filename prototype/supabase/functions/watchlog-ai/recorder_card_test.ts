// deno test prototype/supabase/functions/watchlog-ai/recorder_card_test.ts
// MNVR-051: the Watch AI recorder card never claims support WatchLog has not confirmed.
import { assert, assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { groundRecorderCards, recorderCardData, verifiedOnThisRecorder } from "./recorder_card.ts";
import { customerCardData } from "./harness.ts";

const multiCtx = {
  capability_known: false,
  capabilities: {},
  recorder: null,
  recorders: [
    { id: "rec-a", name: "Main building recorder", state: "healthy", issue: null, checked_at: "2026-10-01T07:58:00Z", camera_count: 2, camera_ids: ["c1", "c2"] },
    { id: "rec-b", name: "Loading area recorder", state: "offline", issue: "connection", checked_at: "2026-10-01T07:57:00Z", camera_count: 2, camera_ids: ["c3", "c4"] },
  ],
};
const singleCtx = {
  capability_known: true,
  recorder: { vendor: "Hikvision", model: "NVR", identified: true },
  capabilities: [{ capability: "snapshot", verdict: "supported", evidence_class: "OFFICIAL_DOCUMENTED" }],
  recorders: [{ id: "rec-a", name: "Recorder", state: "healthy", camera_count: 4, camera_ids: [] }],
};

Deno.test("multi-recorder card data states that support is not confirmed", () => {
  const data = recorderCardData(multiCtx, { note: "advice" });
  assertEquals(data.capability_known, false);
  assertEquals(data.recorders.length, 2, "one row per recorder");
  assertEquals(data.recorders.map((r: any) => r.name), ["Main building recorder", "Loading area recorder"]);
  assertEquals(data.recorders[1].state, "offline");
  assert(!("recorder" in data) && !("capabilities" in data), "no site-wide recorder profile on a multi-recorder site");
  assertEquals(data.recommendation, { note: "advice" });
});

Deno.test("multi-recorder rows carry only what the card renders", () => {
  for (const r of recorderCardData(multiCtx).recorders) {
    assertEquals(Object.keys(r).sort(), ["camera_count", "issue", "name", "state"]);
  }
});

Deno.test("an unexpected recorder state is shown as unknown, never passed through", () => {
  const data = recorderCardData({ recorders: [{ name: "A", state: "rebooting" }, { name: "B", state: "healthy" }] });
  assertEquals(data.recorders[0].state, "unknown");
  assertEquals(data.recorders[1].state, "healthy");
});

Deno.test("single-recorder card data is an explicit boolean", () => {
  assertEquals(recorderCardData(singleCtx).capability_known, true);
  assertEquals(recorderCardData({ ...singleCtx, capability_known: undefined }).capability_known, false);
  assertEquals(recorderCardData({ ...singleCtx, capability_known: "true" }).capability_known, false);
  assertEquals(recorderCardData(singleCtx).recorder, singleCtx.recorder);
});

Deno.test("a model-written recorder card on a multi-recorder site is grounded in the site context", () => {
  const cards = groundRecorderCards([
    { type: "recorder", title: "Recorders", data: { capability_known: true, recorder: { vendor: "Dahua", model: "X" }, capabilities: [{ capability: "snapshot" }] } },
    { type: "capabilities", title: "Support", data: {} },
    { type: "coverage", title: "Coverage", data: { capability_known: true } },
  ], multiCtx);
  for (const card of cards.slice(0, 2)) {
    assertEquals(card.data.capability_known, false);
    assertEquals(card.data.recorders.length, 2);
    assert(!("recorder" in card.data) && !("capabilities" in card.data));
  }
  assertEquals(cards[2].data, { capability_known: true }, "other card types are left alone");
});

Deno.test("a model-written single-recorder card cannot claim more than the context", () => {
  const [missing, inflated, kept] = groundRecorderCards([
    { type: "recorder", title: "Recorder", data: { recorder: { vendor: "X" } } },
    { type: "recorder", title: "Recorder", data: { capability_known: true } },
    { type: "capabilities", title: "Support", data: { capability_known: false } },
  ], { ...singleCtx, capability_known: false });
  assertEquals(missing.data.capability_known, false);
  assertEquals(inflated.data.capability_known, false);
  assertEquals(kept.data.capability_known, false);
  const [confirmed] = groundRecorderCards([{ type: "recorder", title: "Recorder", data: {} }], singleCtx);
  assertEquals(confirmed.data.capability_known, true);
});

Deno.test("grounding tolerates missing input", () => {
  assertEquals(groundRecorderCards(undefined as any, multiCtx), []);
  assertEquals(groundRecorderCards([{ type: "recorder" }], {})[0].data.capability_known, false);
});

Deno.test("only evidence proven on this recorder counts as verified here (MNVR-049)", () => {
  const cap = { capability: "channel_title", verdict: "supported", evidence_class: "FIELD_VERIFIED", write: true, safety_class: "safe_write" };
  assertEquals(verifiedOnThisRecorder({ ...cap, evidence_scope: "recorder" }), true);
  for (const scope of [undefined, null, "model", "none"]) {
    assertEquals(verifiedOnThisRecorder({ ...cap, evidence_scope: scope }), false, `scope ${scope}`);
  }
  assertEquals(verifiedOnThisRecorder({ ...cap, evidence_class: "OFFICIAL_DOCUMENTED", evidence_scope: "recorder" }), false);
  assertEquals(verifiedOnThisRecorder(undefined as any), false);
});

Deno.test("a model-written single-recorder card cannot invent a recorder, a recorder list or capabilities", () => {
  const ctx = { capability_known: false, capabilities: [], recorder: { vendor: null, model: null, identified: false },
    recorders: [{ id: "rec-a", name: "Recorder", state: "unknown", camera_count: 4, camera_ids: [] }] };
  const invented = {
    recorder: { vendor: "Hikvision", model: "DS-7608NI-K2" }, vendor: "Hikvision", model: "DS-7608NI-K2",
    recorders: [{ name: "Main recorder", state: "healthy" }, { name: "Back office recorder", state: "healthy" }],
    capabilities: [{ capability: "line_crossing", verdict: "supported", evidence_class: "FIELD_VERIFIED" }],
    recommendation: { note: "advice" },
  };
  for (const ground of [ctx, { ...ctx, recorders: [] }]) {
    const [card] = groundRecorderCards([{ type: "recorder", title: "Recorder", data: invented }], ground);
    assertEquals(card.data.recorder, ctx.recorder, "the recorder identity is the context's");
    assert(!("recorders" in card.data), "no model-written recorder list");
    assert(!("vendor" in card.data) && !("model" in card.data), "no model-written vendor/model");
    assertEquals(card.data.capabilities, [], "capabilities are the context's");
    assertEquals(card.data.capability_known, false);
    assertEquals(card.data.recommendation, { note: "advice" });
  }
  const [confirmed] = groundRecorderCards([{ type: "capabilities", title: "Support", data: invented }], singleCtx);
  assertEquals(confirmed.data.recorder, singleCtx.recorder);
  assertEquals(confirmed.data.capabilities, customerCardData(singleCtx.capabilities), "the context's rows, in customer words");
});

Deno.test("recorder card data sent to the browser never carries the recorder driver (not displayed)", () => {
  const ctx = { ...singleCtx, recorder: { vendor: "Hikvision", model: "NVR", driver: "hikvision-isapi", firmware: "V4.1", identified: true } };
  const advice = { advisor_version: "v2", recorder: { vendor: "Hikvision", model: "NVR", driver: "hikvision-isapi" }, recommendations: [] };
  const data = recorderCardData(ctx, advice);
  assert(!("driver" in data.recorder), "no driver on the card's recorder");
  assertEquals(data.recorder, { vendor: "Hikvision", model: "NVR", firmware: "V4.1", identified: true });
  assert(!("driver" in data.recommendation.recorder), "no driver in the recommendation either");
  assertEquals(data.recommendation.recorder, { vendor: "Hikvision", model: "NVR" });
  assertEquals(ctx.recorder.driver, "hikvision-isapi", "the context itself is not mutated");
  const [card] = groundRecorderCards([{ type: "recorder", title: "Recorder", data: { recommendation: advice } }], ctx);
  assert(!JSON.stringify(card.data).includes("hikvision-isapi"), JSON.stringify(card.data));
  assert(!JSON.stringify(recorderCardData(multiCtx, advice)).includes("driver"));
});
