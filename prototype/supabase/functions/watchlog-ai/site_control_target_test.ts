// deno test prototype/supabase/functions/watchlog-ai/site_control_target_test.ts
// Per-recorder Site Control flow: a recorder change carries a validated recorder_id/camera_id.
import { assert, assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { siteControlTarget, targetSiteControlActions } from "./site_control_target.ts";

const multiCtx = {
  recorders: [
    { id: "rec-a", name: "Main building recorder", camera_ids: ["c1"] },
    { id: "rec-b", name: "Loading area recorder", camera_ids: ["c3"] },
  ],
  cameras: [
    { id: "c1", recorder_id: "rec-a", channel: "1", name: "Main entrance" },
    { id: "c3", recorder_id: "rec-b", channel: "1", name: "Camera 1" },
  ],
};
const singleCtx = { recorders: [{ id: "rec-a", name: "Recorder" }], cameras: [{ id: "c1", recorder_id: "rec-a" }] };

Deno.test("a camera target resolves to the recorder that owns the camera", () => {
  assertEquals(siteControlTarget({ camera_id: "c3" }, multiCtx), { recorder_id: "rec-b", camera_id: "c3" });
  assertEquals(siteControlTarget({ camera_id: "c3", recorder_id: "rec-b" }, multiCtx), { recorder_id: "rec-b", camera_id: "c3" });
});

Deno.test("camera ownership also comes from the recorder's camera list", () => {
  const ctx = { recorders: multiCtx.recorders, cameras: [{ id: "c3", recorder_id: null }] };
  assertEquals(siteControlTarget({ camera_id: "c3" }, ctx), { recorder_id: "rec-b", camera_id: "c3" });
});

Deno.test("a recorder target must be a recorder of this site", () => {
  assertEquals(siteControlTarget({ recorder_id: "rec-a" }, multiCtx), { recorder_id: "rec-a", camera_id: null });
  assertEquals(siteControlTarget({ recorder_id: "rec-x" }, multiCtx), null);
});

Deno.test("mismatched, unknown or malformed targets fail closed", () => {
  assertEquals(siteControlTarget({ camera_id: "c3", recorder_id: "rec-a" }, multiCtx), null, "camera on another recorder");
  assertEquals(siteControlTarget({ camera_id: "c9" }, multiCtx), null, "camera not at this site");
  assertEquals(siteControlTarget({ camera_id: "c1" }, { recorders: [], cameras: multiCtx.cameras }), null, "recorder not configured");
  assertEquals(siteControlTarget({ camera_id: 7 }, multiCtx), null);
  assertEquals(siteControlTarget("rec-a", multiCtx), null);
  assertEquals(siteControlTarget(null, multiCtx), null);
  assertEquals(siteControlTarget({}, multiCtx), null);
});

Deno.test("a proposal carries the validated target and never a model-supplied one", () => {
  const actions = targetSiteControlActions([
    { kind: "site_control_proposal", label: "Rename camera", data: { href: "/site-control/", recorder_id: "rec-a", camera_id: "c1" } },
    { kind: "navigate", label: "Open site health", data: { href: "/site-health/" } },
  ], { recorder_id: "rec-b", camera_id: "c3" }, multiCtx);
  assertEquals(actions[0].data, { href: "/site-control/", recorder_id: "rec-b", camera_id: "c3" });
  assertEquals(actions[1], { kind: "navigate", label: "Open site health", data: { href: "/site-health/" } });
});

Deno.test("an untargeted proposal on a multi-recorder site asks for the recorder first", () => {
  const [a] = targetSiteControlActions([
    { kind: "site_control_proposal", label: "Change a setting", data: { href: "/site-control/", recorder_id: "rec-a" } },
  ], null, multiCtx);
  assertEquals(a.kind, "site_control_proposal");
  assertEquals(a.data.needs_recorder, true);
  assert(!("recorder_id" in a.data) && !("camera_id" in a.data));
});

Deno.test("a single-recorder proposal keeps working without an explicit target", () => {
  const [a] = targetSiteControlActions([{ kind: "site_control_proposal", label: "Change a setting", data: { href: "/site-control/" } }], null, singleCtx);
  assertEquals(a.data, { href: "/site-control/" });
  const [b] = targetSiteControlActions([{ kind: "site_control_proposal", label: "Change a setting", data: { href: "/site-control/" } }], { recorder_id: "rec-a", camera_id: null }, singleCtx);
  assertEquals(b.data, { href: "/site-control/", recorder_id: "rec-a" });
  assertEquals(targetSiteControlActions(undefined as any, null, singleCtx), []);
});
