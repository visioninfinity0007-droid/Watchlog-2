// System Health recorder root cause (MNVR-068). React-free so it can be unit-checked in plain node
// (prototype/tests/test_portal_recorder_surfaces.py).
//
// A recorder that is unavailable or needs attention is ONE issue. When it stops WatchLog observing its
// cameras (unreachable, sign-in failed) the cameras behind it take the recorder's advice, are not
// counted again, and their camera faults are folded into it. A storage issue does not stop observation:
// it folds only the storage symptoms it explains, and independent camera faults keep camera advice.
// Faults on cameras whose recorder is not reported as failing are kept, whatever their reason. The
// issue count is distinct: a camera with a fault and a bad state is one item, not two.
const ISSUE_STATES=new Set(["offline","attention"]);
// Fault reasons that describe the recorder rather than the camera, and the recorder issue they match.
const RECORDER_REASONS={nvr_unreachable:"connection",nvr_auth_failed:"sign_in",storage_fault:"storage",storage_degraded:"storage",disk_full:"storage"};
// wl_ai_context faults carry the raw camera name, while its cameras list applies this rule (0152).
const PROFILE_NAME=/^(?:Legacy )?MediaProfile_Channel(\d+)_(?:MainStream|SubStream)/i;
const low=v=>String(v||"").toLowerCase();

export function customerCameraName(name){
  const m=PROFILE_NAME.exec(String(name||""));
  return m?"Camera "+m[1]:name;
}

function needsAttention(cam){
  return ["offline","degraded"].includes(low(cam.health_state))||["not_recording","storage_fault"].includes(low(cam.recording_state));
}
function issueOf(r){
  const state=low(r.state);
  if(state==="offline")return"connection";
  return low(r.issue)||"other";
}
// Whether the recorder failure prevents WatchLog from observing the cameras behind it.
const blocking=r=>issueOf(r)!=="storage";
const reasonOf=f=>low(f?.reason||f?.reason_code||f?.type||f?.fault_type);
// A camera whose only problem is the recorder's storage.
const storageSymptom=cam=>low(cam.recording_state)==="storage_fault"&&!["offline","degraded"].includes(low(cam.health_state));

export function recorderImpact({cams=[],faults=[],recorderRows=[]}={}){
  const recorderIssues=recorderRows.filter(r=>ISSUE_STATES.has(low(r.state)));
  const failedById=new Map(recorderIssues.map(r=>[String(r.id),r]));
  const failedByCamera=new Map();
  for(const r of recorderIssues)for(const id of r.camera_ids||[])failedByCamera.set(String(id),r);
  const issueRecorderOf=cam=>failedByCamera.get(String(cam?.id))||failedById.get(String(cam?.recorder_id??""))||null;
  const issueKinds=new Set(recorderIssues.map(issueOf));

  // Which monitored camera each fault is about. A fault with a camera_id (wl_ai_context since 0157) is
  // that camera. A name-only fault (an older context) is matched by its customer name only when exactly
  // one monitored camera has that name. Two recorders can both have a "Camera 1", and a guess between
  // them would hand one recorder's fault to the other recorder's root cause, so such a fault stays
  // unattributed: it is folded only when every camera it could be about is behind a recorder issue that
  // explains it, and otherwise kept as one item that names neither camera.
  const shown=faults.map(f=>f?.camera&&customerCameraName(f.camera)!==f.camera?{...f,camera:customerCameraName(f.camera)}:f);
  const owned=shown.map(f=>({fault:f,cams:[],candidates:[]}));
  for(const x of owned){
    const f=x.fault;
    if(f?.camera_id!=null){x.cams=cams.filter(c=>String(c.id)===String(f.camera_id));continue}
    if(!f?.camera)continue;
    const named=cams.filter(c=>c.name===f.camera);
    if(named.length===1)x.cams=named;else x.candidates=named;
  }
  // A fault is folded into a recorder issue that explains it.
  const explains=(r,f)=>Boolean(r)&&(blocking(r)||RECORDER_REASONS[reasonOf(f)]===issueOf(r));
  const folded=x=>x.cams.length
    ?x.cams.every(c=>explains(issueRecorderOf(c),x.fault))
    :x.candidates.length
      ?x.candidates.every(c=>explains(issueRecorderOf(c),x.fault))
      :Boolean(RECORDER_REASONS[reasonOf(x.fault)])&&issueKinds.has(RECORDER_REASONS[reasonOf(x.fault)]);
  const foldedCams=new Set(owned.filter(folded).flatMap(x=>x.cams));
  const kept=owned.filter(x=>!folded(x));
  const cameraFaults=kept.map(x=>x.fault);
  const faultFor=cam=>kept.find(x=>x.cams.includes(cam))?.fault||null;

  const recorderFor=cam=>{
    const r=issueRecorderOf(cam);
    if(!r)return null;
    return blocking(r)||storageSymptom(cam)||foldedCams.has(cam)?r:null;
  };
  const recorderIssueCameraIds=new Set();
  for(const r of recorderIssues)if(blocking(r))for(const id of r.camera_ids||[])recorderIssueCameraIds.add(String(id));
  for(const c of cams)if(recorderFor(c))recorderIssueCameraIds.add(String(c.id));

  // Offline cameras a kept name-only fault could be about (a fault row is raised only for an offline
  // camera). That fault already counts them; they are not counted again or reported as fault-less.
  const unattributedCameraIds=new Set(kept.flatMap(x=>x.candidates).filter(c=>low(c.health_state)==="offline").map(c=>String(c.id)));

  const cameraItems=cams.filter(c=>!recorderFor(c)&&!faultFor(c)&&!unattributedCameraIds.has(String(c.id))&&needsAttention(c)).length;
  return{
    recorderIssues,
    recorderIssueCameraIds,
    recorderFor,
    cameraFaults,
    faultFor,
    unattributedCameraIds,
    issueCount:recorderIssues.length+cameraFaults.length+cameraItems
  };
}
