// Per-recorder Site Control flow. Site Control links to Watch AI with the recorder (and camera) a change
// is about; the request carries them as site_control_target. They are validated against this site's
// context and are the ONLY recorder/camera target a site_control_proposal may carry: anything the model
// wrote is dropped. On a multi-recorder site an untargeted proposal is marked as needing a recorder,
// matching the database, which refuses a recorder change without a recorder target there.
type Json = Record<string, any>;

const id = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : null);

export function siteControlTarget(raw: unknown, ctx: Json): { recorder_id: string; camera_id: string | null } | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const req = raw as Json;
  const recorders: Json[] = Array.isArray(ctx?.recorders) ? ctx.recorders : [];
  const cameras: Json[] = Array.isArray(ctx?.cameras) ? ctx.cameras : [];
  const recorderIds = new Set(recorders.map((r) => String(r?.id ?? "")).filter(Boolean));
  const cameraId = id(req.camera_id), recorderId = id(req.recorder_id);
  if (req.camera_id != null && !cameraId) return null;
  if (req.recorder_id != null && !recorderId) return null;
  if (cameraId) {
    const cam = cameras.find((c) => String(c?.id ?? "") === cameraId);
    if (!cam) return null;
    const owner = id(cam.recorder_id)
      ?? recorders.find((r) => (Array.isArray(r?.camera_ids) ? r.camera_ids : []).map(String).includes(cameraId))?.id
      ?? null;
    if (!owner || !recorderIds.has(String(owner))) return null;
    if (recorderId && recorderId !== String(owner)) return null;
    return { recorder_id: String(owner), camera_id: cameraId };
  }
  if (recorderId && recorderIds.has(recorderId)) return { recorder_id: recorderId, camera_id: null };
  return null;
}

export function targetSiteControlActions(
  actions: Json[],
  target: { recorder_id: string; camera_id: string | null } | null,
  ctx: Json,
): Json[] {
  const multi = (Array.isArray(ctx?.recorders) ? ctx.recorders : []).length > 1;
  return (Array.isArray(actions) ? actions : []).map((a: Json) => {
    if (a?.kind !== "site_control_proposal") return a;
    const data: Json = a.data && typeof a.data === "object" ? { ...a.data } : {};
    delete data.recorder_id; delete data.camera_id; delete data.needs_recorder;
    if (target) {
      data.recorder_id = target.recorder_id;
      if (target.camera_id) data.camera_id = target.camera_id;
    } else if (multi) {
      data.needs_recorder = true;
    }
    return { ...a, data };
  });
}
