// Watch AI recorder cards (MNVR-051). A recorder or capabilities card shown to the customer must never
// claim more than the site context: support reads "Checked" in the portal only when capability_known is
// exactly true, and a multi-recorder site has no site-wide capability truth (wl_ai_context serves
// capability_known=false and no site-wide recorder there). Model-written cards are rebuilt from the
// context, so the model never decides a recorder fact.
import { customerCardData } from "./harness.ts";

type Json = Record<string, any>;

const RECORDER_STATES = new Set(["healthy", "offline", "attention", "unknown"]);
const CARD_TYPES = new Set(["recorder", "capabilities"]);

function siteRecorders(ctx: Json): Json[] {
  return Array.isArray(ctx?.recorders) ? ctx.recorders : [];
}

// Card data built from the site context only. One row per recorder on a multi-recorder site, carrying
// just what the card renders (no ids, no camera lists).
export function recorderCardData(ctx: Json, recommendation?: unknown): Json {
  const recorders = siteRecorders(ctx);
  const extra = recommendation === undefined ? {} : { recommendation };
  if (recorders.length > 1) {
    return {
      capability_known: false,
      recorders: recorders.map((r: Json, i: number) => {
        const state = String(r?.state || "").toLowerCase();
        return {
          name: String(r?.name || `Recorder ${i + 1}`),
          state: RECORDER_STATES.has(state) ? state : "unknown",
          issue: r?.issue ?? null,
          camera_count: r?.camera_count ?? null,
        };
      }),
      ...extra,
    };
  }
  return {
    recorder: ctx?.recorder || {},
    capabilities: ctx?.capabilities || [],
    capability_known: ctx?.capability_known === true,
    ...extra,
  };
}

// MNVR-049, the Site Control rule: a capability counts as verified on this recorder only when its
// field evidence was proven on this recorder (evidence_scope "recorder"). A model-level FIELD_VERIFIED
// from another unit of the same model is not, so it is never offered as a recorder change.
export function verifiedOnThisRecorder(cap: Json): boolean {
  return String(cap?.evidence_class || "").toUpperCase() === "FIELD_VERIFIED" && cap?.evidence_scope === "recorder";
}

// Ground every recorder/capabilities card in the context. Every such card is rebuilt from the context,
// on a single-recorder site too: a model cannot invent a recorder identity (vendor/model), a recorder
// list, capabilities or a support level. Only its recommendation is kept.
export function groundRecorderCards(cards: Json[], ctx: Json): Json[] {
  return (Array.isArray(cards) ? cards : []).map((card: Json) => {
    if (!CARD_TYPES.has(String(card?.type || ""))) return card;
    const data = card?.data && typeof card.data === "object" ? card.data : {};
    // Rebuilt from the context, so the customer vocabulary is applied again like any card.
    return { ...card, data: customerCardData(recorderCardData(ctx, data.recommendation)) };
  });
}
