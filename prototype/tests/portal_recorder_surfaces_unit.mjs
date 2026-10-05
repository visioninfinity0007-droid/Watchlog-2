// Multi-recorder owner surfaces: deterministic unit checks for the React-free portal modules.
//   argv[2] portal/app/site-health/recorder-impact.js   System Health root cause (MNVR-068)
//   argv[3] portal/app/site-control/recorder-control.js Site Control grouping + labels (MNVR-048/049)
//   argv[4] portal/app/ai/recorder-card.js              Watch AI recorder card (MNVR-051)
// Run through test_portal_recorder_surfaces.py, which copies each module to an .mjs file first.
import assert from "node:assert/strict";
const H = await import(process.argv[2]);
const C = await import(process.argv[3]);
const K = await import(process.argv[4]);

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

t("a name-only fault on a shared camera name goes to the offline camera", () => {
  const cams = [cam("c1", "rec-a", "Camera 1", "operational"), cam("c3", "rec-b", "Camera 1", "offline")];
  const rows = [rec("rec-a", "Recorder A", "healthy", ["c1"]), rec("rec-b", "Recorder B", "healthy", ["c3"])];
  const r = H.recorderImpact({ cams, faults: [{ camera: "Camera 1", reason: "video_loss" }], recorderRows: rows });
  assert.equal(r.faultFor(cams[0]), null);
  assert.ok(r.faultFor(cams[1]));
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

if (failures.length) {
  console.error(`${failures.length} check(s) failed:\n- ` + failures.join("\n- "));
  process.exit(1);
}
console.log(`portal recorder surfaces: ${passed} checks passed`);
