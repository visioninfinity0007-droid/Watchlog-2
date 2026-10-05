// Multi-recorder owner surfaces: deterministic unit checks for the React-free portal modules.
//   argv[2] portal/app/site-health/recorder-impact.js   System Health root cause (MNVR-068)
//   argv[3] portal/app/site-control/recorder-control.js Site Control grouping + labels (MNVR-048/049)
//   argv[4] portal/app/ai/recorder-card.js              Watch AI recorder card (MNVR-051)
//   argv[5] portal/app/control-room/camera-events.js    Cameras & Evidence latest event per camera
// Run through test_portal_recorder_surfaces.py, which copies each module to an .mjs file first.
import assert from "node:assert/strict";
const H = await import(process.argv[2]);
const C = await import(process.argv[3]);
const K = await import(process.argv[4]);
const E = await import(process.argv[5]);

let passed = 0;
const failures = [];
const t = (name, fn) => { try { fn(); passed++; } catch (e) { failures.push(`${name}: ${e.message}`); } };
const cam = (id, recorder_id, name, health_state, recording_state = "recording") =>
  ({ id, recorder_id, channel: 1, name, monitor: true, health_state, recording_state });
const rec = (id, name, state, camera_ids, issue = null) =>
  ({ id, name, state, issue, camera_count: camera_ids.length, camera_ids });

// --- System Health (MNVR-068) ------------------------------------------------------------------
t("a degraded camera with its own fault is one item, not two (office fixture)", () => {
  const cams = [cam("c1", null, "Main entrance", "operational"), cam("c3", null, "Rear access", "degraded", "unknown")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "Rear access", reason: "not_recording" }], recorderRows: [] });
  assert.equal(r.issueCount, 1);
});

t("an offline, not-recording camera with a fault is still one item", () => {
  const cams = [cam("c1", "rec-a", "Gate", "offline", "not_recording")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "Gate", reason: "video_loss" }], recorderRows: [rec("rec-a", "Recorder", "healthy", ["c1"])] });
  assert.equal(r.issueCount, 1);
});

t("cameras behind a failed recorder are folded into that recorder", () => {
  const cams = [
    cam("c1", "rec-a", "Main entrance", "operational"), cam("c2", "rec-a", "Service area", "operational"),
    cam("c3", "rec-b", "Rear access", "offline", "unknown"), cam("c4", "rec-b", "Storage corridor", "unknown", "unknown"),
  ];
  const rows = [rec("rec-a", "Main building recorder", "healthy", ["c1", "c2"]), rec("rec-b", "Loading area recorder", "offline", ["c3", "c4"], "connection")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "Rear access", reason: "nvr_unreachable" }], recorderRows: rows });
  assert.equal(r.recorderFor(cams[2])?.id, "rec-b", "Rear access must take the recorder's advice");
  assert.equal(r.recorderFor(cams[3])?.id, "rec-b", "Storage corridor must take the recorder's advice");
  assert.equal(r.recorderFor(cams[0]), null, "a camera on an available recorder keeps its own advice");
  assert.equal(r.cameraFaults.length, 0, "the recorder-level fault is not repeated as a camera fault");
  assert.equal(r.faultFor(cams[2]), null);
  assert.equal(r.issueCount, 1, "one recorder issue; its cameras are not counted again");
  assert.deepEqual([...r.recorderIssueCameraIds].sort(), ["c3", "c4"]);
});

t("a camera fault behind a failed recorder is folded too (video loss under an unreachable recorder)", () => {
  const cams = [cam("c1", "rec-a", "Gate", "offline"), cam("c2", "rec-b", "Yard", "operational")];
  const rows = [rec("rec-a", "Recorder A", "offline", ["c1"], "connection"), rec("rec-b", "Recorder B", "healthy", ["c2"])];
  const r = H.recorderImpact({ cams, faults: [{ camera: "Gate", reason: "video_loss" }], recorderRows: rows });
  assert.equal(r.cameraFaults.length, 0);
  assert.equal(r.issueCount, 1);
});

t("a recorder-level fault is kept when that camera's recorder is not reported as failing", () => {
  const cams = [cam("c1", "rec-a", "Gate", "offline"), cam("c2", "rec-b", "Yard", "operational")];
  const rows = [rec("rec-a", "Recorder A", "unknown", ["c1"]), rec("rec-b", "Recorder B", "offline", ["c2"], "connection")];
  const fault = { camera: "Gate", reason: "nvr_unreachable" };
  const r = H.recorderImpact({ cams, faults: [fault], recorderRows: rows });
  assert.equal(r.cameraFaults.length, 1, "suppress only for recorders that report an issue");
  assert.equal(r.faultFor(cams[0]), fault);
  assert.equal(r.recorderFor(cams[0]), null);
  assert.equal(r.issueCount, 2, "Recorder B plus the Gate fault");
});

t("a recorder-level fault is kept on a site without recorder rows", () => {
  const cams = [cam("c1", null, "Gate", "offline")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "Gate", reason: "nvr_auth_failed" }], recorderRows: [] });
  assert.equal(r.cameraFaults.length, 1);
  assert.equal(r.issueCount, 1);
});

t("a fault keyed by camera id never lands on a same-named camera of another recorder", () => {
  const cams = [cam("c1", "rec-a", "Camera 1", "operational"), cam("c3", "rec-b", "Camera 1", "offline")];
  const rows = [rec("rec-a", "Recorder A", "healthy", ["c1"]), rec("rec-b", "Recorder B", "healthy", ["c3"])];
  const r = H.recorderImpact({ cams, faults: [{ camera_id: "c3", camera: "Camera 1", reason: "video_loss" }], recorderRows: rows });
  assert.equal(r.faultFor(cams[0]), null);
  assert.ok(r.faultFor(cams[1]));
  assert.equal(r.issueCount, 1);
});

t("a name-only fault on a shared camera name is attributed to neither camera, and counted once", () => {
  const cams = [cam("c1", "rec-a", "Camera 1", "operational"), cam("c3", "rec-b", "Camera 1", "offline")];
  const rows = [rec("rec-a", "Recorder A", "healthy", ["c1"]), rec("rec-b", "Recorder B", "healthy", ["c3"])];
  const fault = { camera: "Camera 1", reason: "video_loss" };
  const r = H.recorderImpact({ cams, faults: [fault], recorderRows: rows });
  assert.equal(r.faultFor(cams[0]), null);
  assert.equal(r.faultFor(cams[1]), null, "channel-derived names are not camera identity");
  assert.deepEqual(r.cameraFaults, [fault], "the fault is still shown");
  assert.ok(r.unattributedCameraIds.has("c3"));
  assert.equal(r.issueCount, 1, "the fault and the offline camera it may be about are one item");
});

// Two recorders that both have a profile-named Channel 1: identical name-only fault rows, which Postgres
// may return in either order. The result must not depend on that order, and a camera's fault must never
// be handed to the other recorder's root cause.
const sameName = () => {
  const cams = [cam("a1", "rec-a", "Camera 1", "offline"), cam("b1", "rec-b", "Camera 1", "offline")];
  const rows = [rec("rec-a", "Recorder A", "attention", ["a1"], "storage"), rec("rec-b", "Recorder B", "unknown", ["b1"])];
  return { cams, rows };
};
const summary = r => ({
  issueCount: r.issueCount,
  faults: r.cameraFaults.map(f => f.reason).sort(),
  recorderFor: ["a1", "b1"].map(id => r.recorderIssueCameraIds.has(id)),
});
t("name-only faults on a shared name give the same result in either row order", () => {
  const fa = { camera: "MediaProfile_Channel1_MainStream", reason: "storage_fault" };
  const fb = { camera: "MediaProfile_Channel1_MainStream", reason: "video_loss" };
  const { cams, rows } = sameName();
  const one = H.recorderImpact({ cams, faults: [fa, fb], recorderRows: rows });
  const two = H.recorderImpact({ cams, faults: [fb, fa], recorderRows: rows });
  assert.deepEqual(summary(one), summary(two));
  assert.equal(one.recorderFor(cams[0]), null, "a1's video loss is not hidden behind Recorder A's storage advice");
  assert.equal(one.issueCount, 3, "the storage issue plus the two camera faults");
});

t("faults carrying camera_id are attributed exactly, whatever the row order", () => {
  const { cams, rows } = sameName();
  const fa = { camera_id: "a1", recorder_id: "rec-a", camera: "MediaProfile_Channel1_MainStream", reason: "video_loss" };
  const fb = { camera_id: "b1", recorder_id: "rec-b", camera: "MediaProfile_Channel1_MainStream", reason: "storage_fault" };
  for (const faults of [[fa, fb], [fb, fa]]) {
    const r = H.recorderImpact({ cams, faults, recorderRows: rows });
    assert.equal(r.faultFor(cams[0])?.reason, "video_loss");
    assert.equal(r.faultFor(cams[1])?.reason, "storage_fault", "Recorder B's camera fault stays on Recorder B's camera");
    assert.equal(r.recorderFor(cams[0]), null);
    assert.equal(r.issueCount, 3);
  }
});

t("a recorder that needs attention takes over its cameras' advice too", () => {
  const cams = [cam("c1", "rec-a", "Gate", "unknown", "unknown"), cam("c2", "rec-b", "Yard", "operational")];
  const rows = [rec("rec-a", "Recorder A", "attention", ["c1"], "sign_in"), rec("rec-b", "Recorder B", "healthy", ["c2"])];
  const r = H.recorderImpact({ cams, faults: [], recorderRows: rows });
  assert.equal(r.recorderFor(cams[0])?.id, "rec-a");
  assert.equal(r.recorderFor(cams[1]), null);
});

t("camera ownership also comes from the camera's recorder id", () => {
  const cams = [cam("c9", "rec-b", "Dock", "offline")];
  const rows = [rec("rec-b", "Recorder B", "offline", [], "connection")];
  const r = H.recorderImpact({ cams, faults: [], recorderRows: rows });
  assert.equal(r.recorderFor(cams[0])?.id, "rec-b");
  assert.equal(r.issueCount, 1);
});

t("a healthy multi-recorder site has no issues", () => {
  const cams = [cam("c1", "rec-a", "Gate", "operational"), cam("c2", "rec-b", "Yard", "operational")];
  const rows = [rec("rec-a", "A", "healthy", ["c1"]), rec("rec-b", "B", "healthy", ["c2"])];
  const r = H.recorderImpact({ cams, faults: [], recorderRows: rows });
  assert.equal(r.issueCount, 0);
  assert.equal(r.recorderIssues.length, 0);
});

// The real 0152 wl_ai_context fault shape: no camera_id, and the raw camera name, while the cameras
// list in the same payload carries the normalized "Camera N" (PS-R1).
const RAW_NAME = /MediaProfile|legacy-profile|_SubStream|_MainStream/i;
t("0152 shape: cameras behind an offline recorder are folded although faults carry raw profile names", () => {
  const cams = [cam("c1", "rec-a", "Camera 1", "offline", "unknown"), cam("c2", "rec-a", "Camera 2", "offline", "unknown")];
  const rows = [rec("rec-a", "Recorder", "offline", ["c1", "c2"], "connection")];
  const faults = [{ camera: "MediaProfile_Channel1_MainStream", reason: "nvr_unreachable" }, { camera: "MediaProfile_Channel2_MainStream", reason: "nvr_unreachable" }];
  const r = H.recorderImpact({ cams, faults, recorderRows: rows });
  assert.deepEqual(r.cameraFaults.map(f => f.camera), [], "no extra camera rows under the recorder issue");
  assert.equal(r.issueCount, 1);
  assert.equal(r.recorderFor(cams[0])?.id, "rec-a");
});

t("0152 shape: a raw-named fault on a healthy recorder is one item and is shown by its customer name", () => {
  const cams = [cam("c3", "rec-a", "Camera 3", "offline", "not_recording")];
  const fault = { camera: "MediaProfile_Channel3_MainStream", reason: "video_loss" };
  const r = H.recorderImpact({ cams, faults: [fault], recorderRows: [rec("rec-a", "Recorder", "healthy", ["c3"])] });
  assert.equal(r.issueCount, 1, "one camera, one item");
  assert.ok(r.faultFor(cams[0]), "the fault belongs to Camera 3");
  assert.deepEqual(r.cameraFaults.map(f => f.camera), ["Camera 3"]);
});

t("0152 shape: a legacy profile name is normalized too, and the fault row never shows a raw name", () => {
  const cams = [cam("c5", null, "Camera 5", "offline", "unknown")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "Legacy MediaProfile_Channel5_SubStream", reason: "video_loss" }], recorderRows: [] });
  assert.equal(r.issueCount, 1);
  for (const f of r.cameraFaults) assert.ok(!RAW_NAME.test(f.camera), f.camera);
});

t("an unmatched recorder-root fault is folded into a recorder with that issue, not shown as a camera", () => {
  // The fault cannot be matched to a monitored camera (its profile number differs from the channel).
  const cams = [cam("c1", "rec-a", "Camera 9", "offline", "unknown")];
  const rows = [rec("rec-a", "Recorder", "offline", ["c1"], "connection")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "MediaProfile_Channel1_MainStream", reason: "nvr_unreachable" }], recorderRows: rows });
  assert.equal(r.cameraFaults.length, 0);
  assert.equal(r.issueCount, 1);
});

t("an unmatched camera fault is still kept, under its customer name", () => {
  const cams = [cam("c1", "rec-a", "Gate", "operational")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "MediaProfile_Channel7_MainStream", reason: "video_loss" }], recorderRows: [rec("rec-a", "Recorder", "healthy", ["c1"])] });
  assert.deepEqual(r.cameraFaults.map(f => f.camera), ["Camera 7"]);
  assert.equal(r.issueCount, 1);
});

t("two offline cameras sharing a name: only the one on the healthy recorder is a camera item", () => {
  const cams = [cam("c1", "rec-a", "Camera 1", "offline", "unknown"), cam("c3", "rec-b", "Camera 1", "offline", "unknown")];
  const rows = [rec("rec-a", "Recorder A", "healthy", ["c1"]), rec("rec-b", "Recorder B", "offline", ["c3"], "connection")];
  // The 0157 context shape: each fault names its camera.
  const faults = [{ camera_id: "c1", camera: "MediaProfile_Channel1_MainStream", reason: "video_loss" }, { camera_id: "c3", camera: "MediaProfile_Channel1_MainStream", reason: "nvr_unreachable" }];
  const r = H.recorderImpact({ cams, faults, recorderRows: rows });
  assert.equal(r.cameraFaults.length, 1);
  assert.equal(r.cameraFaults[0].reason, "video_loss");
  assert.equal(r.faultFor(cams[0])?.reason, "video_loss");
  assert.equal(r.issueCount, 2, "Recorder B plus the camera on Recorder A");
});

// A recorder storage issue does not stop WatchLog observing the cameras (PS-R2).
t("storage attention is one issue but keeps an independent video loss and its camera advice", () => {
  const cams = [cam("c1", "rec-a", "Gate", "offline", "not_recording"), cam("c2", "rec-a", "Yard", "operational", "recording")];
  const rows = [rec("rec-a", "Recorder", "attention", ["c1", "c2"], "storage")];
  const fault = { camera: "Gate", reason: "video_loss" };
  const r = H.recorderImpact({ cams, faults: [fault], recorderRows: rows });
  assert.equal(r.recorderIssues.length, 1);
  assert.deepEqual(r.cameraFaults, [fault], "the video loss stays a camera fault");
  assert.equal(r.faultFor(cams[0]), fault);
  assert.equal(r.recorderFor(cams[0]), null, "Gate keeps camera-level advice");
  assert.equal(r.recorderFor(cams[1]), null, "a healthy recording camera is not marked as needing attention");
  assert.ok(!r.recorderIssueCameraIds.has("c2"));
  assert.equal(r.issueCount, 2, "the storage issue plus Gate");
});

t("storage attention folds the storage symptoms it explains", () => {
  const cams = [cam("c1", "rec-a", "Gate", "operational", "storage_fault"), cam("c2", "rec-a", "Yard", "offline", "unknown")];
  const rows = [rec("rec-a", "Recorder", "attention", ["c1", "c2"], "storage")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "Yard", reason: "storage_fault" }], recorderRows: rows });
  assert.equal(r.recorderFor(cams[0])?.id, "rec-a", "a storage recording fault takes the recorder's storage advice");
  assert.equal(r.recorderFor(cams[1])?.id, "rec-a");
  assert.equal(r.cameraFaults.length, 0);
  assert.equal(r.issueCount, 1);
});

t("sign-in attention still takes over its cameras and their faults", () => {
  const cams = [cam("c1", "rec-a", "Camera 1", "offline", "unknown")];
  const rows = [rec("rec-a", "Recorder", "attention", ["c1"], "sign_in")];
  const r = H.recorderImpact({ cams, faults: [{ camera: "MediaProfile_Channel1_MainStream", reason: "nvr_auth_failed" }], recorderRows: rows });
  assert.equal(r.recorderFor(cams[0])?.id, "rec-a");
  assert.equal(r.cameraFaults.length, 0);
  assert.equal(r.issueCount, 1);
});

// Page leads (Cameras & Evidence, System Health): only a blocking recorder issue outranks camera faults,
// and "affected" means the cameras the root cause explains.
t("a storage issue on Recorder A never claims its recording cameras or hides an offline camera on B", () => {
  const cams = [
    cam("a1", "rec-a", "Till", "operational"), cam("a2", "rec-a", "Door", "operational"),
    cam("a3", "rec-a", "Store", "operational"), cam("a4", "rec-a", "Office", "operational"),
    cam("b1", "rec-b", "Yard", "offline", "unknown"),
  ];
  const rows = [rec("rec-a", "Recorder A", "attention", ["a1", "a2", "a3", "a4"], "storage"), rec("rec-b", "Recorder B", "healthy", ["b1"])];
  const r = H.recorderImpact({ cams, faults: [{ camera_id: "b1", camera: "Yard", reason: "video_loss" }], recorderRows: rows });
  assert.equal(r.recorderIssues.length, 1);
  assert.equal(r.blockingIssues.length, 0, "a storage issue does not stop observation, so it never leads over camera faults");
  assert.equal(r.camerasAffectedBy(rows[0]), 0, "recording cameras are not affected by the storage issue");
  assert.equal(r.recorderIssueCameraIds.size, 0);
  assert.equal(r.recorderFor(cams[4]), null, "Yard keeps its own camera advice");
});

t("a blocking recorder affects every camera behind it; a storage issue only the symptoms it explains", () => {
  const cams = [cam("a1", "rec-a", "Gate", "offline", "unknown"), cam("a2", "rec-a", "Dock", "unknown", "unknown"),
    cam("b1", "rec-b", "Till", "operational", "storage_fault"), cam("b2", "rec-b", "Door", "operational")];
  const rows = [rec("rec-a", "Recorder A", "offline", ["a1", "a2"], "connection"), rec("rec-b", "Recorder B", "attention", ["b1", "b2"], "storage")];
  const r = H.recorderImpact({ cams, faults: [], recorderRows: rows });
  assert.deepEqual(r.blockingIssues.map(x => x.id), ["rec-a"]);
  assert.equal(r.camerasAffectedBy(rows[0]), 2);
  assert.equal(r.camerasAffectedBy(rows[1]), 1, "only Till shows the storage symptom");
  assert.deepEqual([...r.recorderIssueCameraIds].sort(), ["a1", "a2", "b1"]);
});

// While the site connection is lost, recorder health is last known (fresh for 15 min server-side).
t("a disconnected site shows no recorder as currently available or failing", () => {
  const rows = [rec("rec-a", "Recorder A", "healthy", ["c1"]), rec("rec-b", "Recorder B", "offline", ["c2"], "connection")];
  assert.equal(H.currentRecorderRows(rows, true), rows, "a connected site keeps the current states");
  const stale = H.currentRecorderRows(rows, false);
  assert.deepEqual(stale.map(r => r.state), ["unknown", "unknown"]);
  assert.deepEqual(stale.map(r => r.last_known_state), ["healthy", "offline"]);
  assert.deepEqual(stale.map(r => r.issue), [null, null]);
  assert.equal(stale[1].last_known_issue, "connection");
  assert.deepEqual(stale.map(r => r.id), ["rec-a", "rec-b"]);
  assert.equal(stale.filter(r => r.state === "healthy").length, 0, "never counted as available");
  assert.equal(H.recorderImpact({ cams: [], faults: [], recorderRows: stale }).recorderIssues.length, 0);
  assert.deepEqual(H.currentRecorderRows(undefined, false), []);
});

// --- Site Control labels (MNVR-049) ------------------------------------------------------------
const VERIFIED_HERE = /verified on (your|this)/i;
t("model-level verified evidence is never shown as verified on the customer's system", () => {
  for (const scope of [undefined, null, "model", "none"]) {
    const cap = { capability: "channel_title", verdict: "supported", evidence_class: "FIELD_VERIFIED", evidence_scope: scope };
    const v = C.capView(cap);
    assert.notEqual(v.action, "configure", `scope ${scope}`);
    assert.ok(!VERIFIED_HERE.test(v.note + " " + v.label), `scope ${scope}: ${v.note}`);
    assert.ok(!VERIFIED_HERE.test(C.evidenceLabel(cap)), `scope ${scope}: ${C.evidenceLabel(cap)}`);
  }
});

t("recorder-scoped verified evidence is configurable and says so", () => {
  const cap = { capability: "channel_title", verdict: "supported", evidence_class: "FIELD_VERIFIED", evidence_scope: "recorder" };
  const v = C.capView(cap);
  assert.equal(v.action, "configure");
  assert.match(v.note, /Verified on your camera system/);
  assert.match(C.evidenceLabel(cap), /Verified on this system/);
});

t("unknown, unsupported, documented and camera-side settings keep their honest treatment", () => {
  assert.equal(C.capView({ verdict: "unknown", evidence_class: "UNKNOWN" }).label, "Not verified");
  assert.equal(C.capView({ verdict: "unsupported", evidence_class: "UNSUPPORTED" }).action, "disabled");
  assert.equal(C.capView({ verdict: "by_camera", evidence_class: "OFFICIAL_DOCUMENTED" }).action, "none");
  assert.equal(C.capView({ verdict: "supported", evidence_class: "OFFICIAL_DOCUMENTED" }).action, "verify");
  assert.equal(C.evidenceLabel({ evidence_class: "UNKNOWN" }), "Not verified");
  assert.equal(C.evidenceLabel({ evidence_class: "OFFICIAL_DOCUMENTED" }), "Documented by the maker");
});

// --- Site Control grouping (MNVR-048) ----------------------------------------------------------
const RAW = /MediaProfile|legacy-profile|_SubStream|_MainStream/i;
t("raw recorder profile rows never reach the camera list (pre-0155 diagnosis shape)", () => {
  // 8 cameras exposed as 16 profile rows, as the old site diagnosis returned them.
  const cameras = [];
  for (let i = 1; i <= 8; i++) {
    cameras.push({ channel: String(i), name: `MediaProfile_Channel${i}_MainStream`, purpose: null });
    cameras.push({ channel: `legacy-profile-${i}`, name: `MediaProfile_Channel${i}_SubStream`, purpose: null });
  }
  const groups = C.recorderGroups({ cameras });
  assert.equal(groups.length, 1);
  assert.equal(groups[0].cameras.length, 8);
  for (const c of groups[0].cameras) assert.ok(!RAW.test(c.name) && !RAW.test(String(c.channel)), JSON.stringify(c));
  assert.deepEqual(groups[0].cameras.map(c => c.name).slice(0, 2), ["Camera 1", "Camera 2"]);
});

const multi = {
  multi_recorder: true, recorder_count: 2, recorder: null, capabilities: null, capability_known: false,
  recorders: [
    { recorder_id: "rec-a", display_name: "Main building recorder", is_primary: true, vendor: "Dahua", model: "XVR-A", identified: true, capability_known: true, camera_count: 2,
      cameras: [{ camera_id: "c1", channel: "1", name: "Main entrance", purpose: "entrance" }, { camera_id: "c2", channel: "2", name: "Service area", purpose: "queue" }] },
    { recorder_id: "rec-b", display_name: "Loading area recorder", is_primary: false, vendor: null, model: null, identified: false, capability_known: false, camera_count: 2,
      cameras: [{ camera_id: "c3", channel: "1", name: "Camera 1", purpose: null }, { camera_id: "c4", channel: "2", name: "Storage corridor", purpose: "storage" }] },
  ],
  cameras: [
    { camera_id: "c1", recorder_id: "rec-a", recorder_name: "Main building recorder", channel: "1", name: "Main entrance", purpose: "entrance" },
    { camera_id: "c2", recorder_id: "rec-a", recorder_name: "Main building recorder", channel: "2", name: "Service area", purpose: "queue" },
    { camera_id: "c3", recorder_id: "rec-b", recorder_name: "Loading area recorder", channel: "1", name: "Camera 1", purpose: null },
    { camera_id: "c4", recorder_id: "rec-b", recorder_name: "Loading area recorder", channel: "2", name: "Storage corridor", purpose: "storage" },
  ],
};

t("a multi-recorder site is grouped by recorder with each recorder's own profile", () => {
  const groups = C.recorderGroups(multi);
  assert.deepEqual(groups.map(g => g.name), ["Main building recorder", "Loading area recorder"]);
  assert.deepEqual(groups.map(g => g.recorderId), ["rec-a", "rec-b"]);
  assert.deepEqual(groups.map(g => g.identified), [true, false]);
  assert.deepEqual(groups.map(g => g.system), ["Dahua XVR-A", ""]);
  assert.deepEqual(groups.map(g => g.capabilityKnown), [true, false]);
  assert.deepEqual(groups[1].cameras.map(c => c.cameraId), ["c3", "c4"]);
});

t("camera rows are keyed by camera identity, unique across recorders sharing a channel", () => {
  const keys = C.recorderGroups(multi).flatMap(g => g.cameras.map(c => c.key));
  assert.equal(new Set(keys).size, keys.length);
  assert.ok(keys.includes("c1") && keys.includes("c3"));
});

t("an unnamed recorder and id-less rows still render safely", () => {
  const groups = C.recorderGroups({ recorders: [
    { recorder_id: "r1", display_name: "Office recorder", cameras: [{ channel: "1", name: "Camera 1" }, { channel: "1", name: "Camera 1" }] },
    { recorder_id: "r2", display_name: null, cameras: [{ channel: "1", name: "Camera 1" }] },
  ] });
  assert.equal(groups[1].name, "Recorder 2");
  const keys = groups.flatMap(g => g.cameras.map(c => c.key));
  assert.equal(new Set(keys).size, keys.length);
});

t("a single-recorder site is one group keyed by camera identity", () => {
  const groups = C.recorderGroups({ recorder_count: 1, multi_recorder: false, recorder: { vendor: "Hikvision", model: "NVR", identified: true },
    recorders: [{ recorder_id: "rec-a", display_name: "Recorder", identified: true, vendor: "Hikvision", model: "NVR", cameras: [{ camera_id: "c1", channel: "1", name: "Gate" }] }],
    cameras: [{ camera_id: "c1", recorder_id: "rec-a", channel: "1", name: "Gate" }] });
  assert.equal(groups.length, 1);
  assert.equal(groups[0].recorderId, "rec-a");
  assert.equal(groups[0].cameras[0].key, "c1");
});

t("duplicate channel and name rows without ids get distinct keys", () => {
  const keys = C.recorderGroups({ cameras: [{ channel: 1, name: "Camera 1" }, { channel: 1, name: "Camera 1" }] })[0].cameras.map(c => c.key);
  assert.equal(new Set(keys).size, 2);
});

t("an unidentified recorder never shows a partial system name", () => {
  const [g] = C.recorderGroups({ recorders: [{ recorder_id: "r1", display_name: "A", identified: false, vendor: "Dahua", model: null, cameras: [] }] });
  assert.equal(g.system, "");
  assert.equal(g.identified, false);
});

// --- Watch AI recorder card (MNVR-051) ---------------------------------------------------------
const text = rows => rows.map(r => `${r.label}: ${r.value}`).join(" | ");
t("a multi-recorder card without confirmed support never says Checked", () => {
  const rows = K.recorderCardRows({ recorders: [{ name: "NVR A", state: "healthy" }, { name: "NVR B", state: "unknown" }], recommendation: {} });
  assert.ok(!/Checked/.test(text(rows)), text(rows));
  assert.ok(/Not yet confirmed/.test(text(rows)), text(rows));
});

t("a multi-recorder card has one row per recorder, in customer words", () => {
  const rows = K.recorderCardRows({ recorders: [{ name: "NVR A", state: "healthy" }, { name: "NVR B", state: "offline" }, { name: null, state: "attention" }], capability_known: false });
  const labels = rows.map(r => r.label);
  assert.ok(labels.includes("NVR A") && labels.includes("NVR B") && labels.includes("Recorder 3"), labels.join(","));
  assert.ok(!labels.includes("System"), "no single site-wide System row on a multi-recorder card");
  const byName = Object.fromEntries(rows.map(r => [r.label, r.value]));
  assert.equal(byName["NVR A"], "Available");
  assert.equal(byName["NVR B"], "Unavailable");
  assert.equal(byName["Recorder 3"], "Needs attention");
  assert.equal(new Set(rows.map(r => r.key)).size, rows.length);
});

t("a multi-recorder card cannot be promoted to Checked by a site-level flag", () => {
  const rows = K.recorderCardRows({ recorders: [{ name: "NVR A", state: "healthy" }, { name: "NVR B", state: "healthy" }], capability_known: true });
  assert.ok(!/Checked/.test(text(rows)), text(rows));
});

t("a single-recorder card says Checked only on an explicit true", () => {
  const value = d => K.recorderCardRows(d).find(r => r.label === "WatchLog support").value;
  assert.equal(value({ recorder: { vendor: "X" } }), "Not yet confirmed");
  assert.equal(value({ vendor: "X", model: "Y" }), "Not yet confirmed");
  assert.equal(value({ recorder: { vendor: "X" }, capability_known: false }), "Not yet confirmed");
  assert.equal(value({ recorder: { vendor: "X" }, capability_known: "true" }), "Not yet confirmed");
  assert.equal(value({ recorder: { vendor: "X", model: "Y" }, capability_known: true }), "Checked");
  assert.equal(K.recorderCardRows({ recorder: { vendor: "X", model: "Y" } }).find(r => r.label === "System").value, "X Y");
  assert.equal(K.recorderCardRows({}).find(r => r.label === "System").value, "Not identified");
});

// --- Cameras & Evidence: latest camera event per camera ---------------------------------------
const latestFor = (ctx, recorderRows = []) => {
  const index = E.latestEventIndex(ctx.recent_events);
  const channelFallback = E.atMostOneRecorder(recorderRows, ctx);
  const recorderOf = c => String(c.recorder_id || recorderRows.find(r => (r.camera_ids || []).map(String).includes(String(c.id)))?.id || "");
  return Object.fromEntries(ctx.cameras.map(c => [c.id, E.latestEventFor(index, c, { recorderId: recorderOf(c), channelFallback, cameras: ctx.cameras })?.event_id ?? null]));
};
// wl_ai_context before 0152 (0128): recent events carry no camera_id/recorder_id, cameras no recorder_id,
// and wl_my_site_recorders does not exist yet, so the page has no recorder rows.
const pre0152 = {
  cameras: [
    { id: "k1", channel: "1", name: "Floor 1", monitor: true },
    { id: "k2", channel: "2", name: "Floor 2", monitor: true },
    { id: "k3", channel: "3", name: "Kitchen", monitor: true },
  ],
  recent_events: [
    { event_id: "e9", event_type: "visual_sample", device_ts: "2026-10-01T07:59:00Z", camera: "Floor 2", channel: "2", source: "live", recovered: false },
    { event_id: "e8", event_type: "visual_sample", device_ts: "2026-10-01T07:58:00Z", camera: "Floor 1", channel: "1", source: "live", recovered: false },
    { event_id: "e7", event_type: "visual_sample", device_ts: "2026-10-01T07:50:00Z", camera: "Floor 1", channel: "1", source: "live", recovered: false },
  ],
};
t("pre-0152 context: a single-recorder site still shows each camera's latest event", () => {
  assert.deepEqual(latestFor(pre0152), { k1: "e8", k2: "e9", k3: null });
});

t("pre-0152 context: a channel shared by two cameras never picks one of them", () => {
  const ctx = { ...pre0152, cameras: [...pre0152.cameras, { id: "k4", channel: "1", name: "Camera 1", monitor: true }] };
  const got = latestFor(ctx);
  assert.equal(got.k1, null);
  assert.equal(got.k4, null);
  assert.equal(got.k2, "e9");
});

t("an id-less event is never matched by channel on a multi-recorder site", () => {
  const ctx = { ...pre0152, cameras: pre0152.cameras.map((c, i) => ({ ...c, recorder_id: i ? "rec-b" : "rec-a" })) };
  assert.deepEqual(latestFor(ctx), { k1: null, k2: null, k3: null });
  const rows = [{ id: "rec-a", camera_ids: ["k1"] }, { id: "rec-b", camera_ids: ["k2", "k3"] }];
  assert.deepEqual(latestFor(pre0152, rows), { k1: null, k2: null, k3: null });
});

t("v7 context: camera identity wins and overlapping Channel 1 stays per recorder", () => {
  const ctx = {
    recorders: [{ id: "rec-a" }, { id: "rec-b" }],
    cameras: [
      { id: "c1", recorder_id: "rec-a", channel: 1, name: "Main entrance", monitor: true },
      { id: "c3", recorder_id: "rec-b", channel: 1, name: "Rear access", monitor: true },
    ],
    recent_events: [
      { event_id: "b1", camera_id: "c3", recorder_id: "rec-b", channel: 1, event_type: "vehicle", device_ts: "2026-10-01T07:56:00Z" },
      { event_id: "a1", camera_id: "c1", recorder_id: "rec-a", channel: 1, event_type: "person", device_ts: "2026-10-01T07:55:00Z" },
    ],
  };
  assert.deepEqual(latestFor(ctx, ctx.recorders), { c1: "a1", c3: "b1" });
});

if (failures.length) {
  console.error(`${failures.length} check(s) failed:\n- ` + failures.join("\n- "));
  process.exit(1);
}
console.log(`portal recorder surfaces: ${passed} checks passed`);
