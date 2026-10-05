// System Health recorder root cause (MNVR-068). React-free so it can be unit-checked in plain node
// (prototype/tests/test_portal_recorder_surfaces.py).
//
// A recorder that is unavailable or needs attention is ONE issue. The cameras behind it take the
// recorder's advice and are not counted again, and their camera faults are folded into it. Faults on
// cameras whose recorder is not reported as failing are kept, whatever their reason. The issue count
// is distinct: a camera with a fault and a bad state is one item, not two.
const ISSUE_STATES=new Set(["offline","attention"]);
const low=v=>String(v||"").toLowerCase();

function needsAttention(cam){
  return ["offline","degraded"].includes(low(cam.health_state))||["not_recording","storage_fault"].includes(low(cam.recording_state));
}

export function recorderImpact({cams=[],faults=[],recorderRows=[]}={}){
  const recorderIssues=recorderRows.filter(r=>ISSUE_STATES.has(low(r.state)));
  const failedById=new Map(recorderIssues.map(r=>[String(r.id),r]));
  const failedByCamera=new Map();
  for(const r of recorderIssues)for(const id of r.camera_ids||[])failedByCamera.set(String(id),r);
  const recorderFor=cam=>failedByCamera.get(String(cam?.id))||failedById.get(String(cam?.recorder_id??""))||null;
  const recorderIssueCameraIds=new Set(failedByCamera.keys());
  for(const c of cams)if(recorderFor(c))recorderIssueCameraIds.add(String(c.id));

  // Which monitored camera a fault is about: camera id first; a name shared by several cameras goes
  // to the offline one(s), since a fault row is only raised for an offline camera.
  const camerasOf=f=>{
    if(f?.camera_id!=null)return cams.filter(c=>String(c.id)===String(f.camera_id));
    if(!f?.camera)return[];
    const named=cams.filter(c=>c.name===f.camera);
    if(named.length<=1)return named;
    const offline=named.filter(c=>low(c.health_state)==="offline");
    return offline.length?offline:named;
  };
  const owned=faults.map(f=>({fault:f,cams:camerasOf(f)}));
  const kept=owned.filter(x=>!(x.cams.length&&x.cams.every(c=>recorderFor(c))));
  const cameraFaults=kept.map(x=>x.fault);
  const faultFor=cam=>kept.find(x=>x.cams.includes(cam))?.fault||null;

  const cameraItems=cams.filter(c=>!recorderFor(c)&&!faultFor(c)&&needsAttention(c)).length;
  return{
    recorderIssues,
    recorderIssueCameraIds,
    recorderFor,
    cameraFaults,
    faultFor,
    issueCount:recorderIssues.length+cameraFaults.length+cameraItems
  };
}
