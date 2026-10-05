// Latest camera event per camera (Cameras & Evidence). React-free so it can be unit-checked in plain
// node (prototype/tests/test_portal_recorder_surfaces.py).
//
// Camera UUID is canonical, then recorder + channel. Channel alone is only used for an event that
// carries neither id (the wl_ai_context shape before 0152, which has no camera_id or recorder_id on
// recent events) and only while it cannot name the wrong camera: the site has at most one recorder
// and exactly one camera on that channel. Two recorders can both have a Channel 1.

// recent_events arrive newest first, so the first event stored under a key is the latest.
export function latestEventIndex(events){
  const map=new Map();
  for(const e of Array.isArray(events)?events:[]){
    const keys=[];
    if(e?.camera_id)keys.push("camera:"+String(e.camera_id));
    if(e?.recorder_id&&e.channel!=null)keys.push("recorder:"+String(e.recorder_id)+":"+String(e.channel));
    if(!e?.camera_id&&!e?.recorder_id&&e?.channel!=null)keys.push("channel:"+String(e.channel));
    for(const key of keys)if(!map.has(key))map.set(key,e);
  }
  return map;
}

// Whether everything WatchLog knows about this site points to one recorder at most: the recorder
// summary, the context's recorder rows and the cameras' own recorder ids.
export function atMostOneRecorder(recorderRows,ctx){
  const ids=new Set();
  for(const r of Array.isArray(recorderRows)?recorderRows:[])ids.add(String(r?.id??""));
  for(const r of Array.isArray(ctx?.recorders)?ctx.recorders:[])ids.add(String(r?.id??""));
  for(const c of Array.isArray(ctx?.cameras)?ctx.cameras:[])if(c?.recorder_id)ids.add(String(c.recorder_id));
  return ids.size<=1;
}

export function latestEventFor(index,camera,{recorderId="",channelFallback=false,cameras=[]}={}){
  const byCamera=index.get("camera:"+String(camera?.id));
  if(byCamera)return byCamera;
  if(recorderId){
    const byRecorder=index.get("recorder:"+String(recorderId)+":"+String(camera?.channel??""));
    if(byRecorder)return byRecorder;
  }
  if(!channelFallback||camera?.channel==null)return null;
  const channel=String(camera.channel);
  const onChannel=(Array.isArray(cameras)?cameras:[]).filter(c=>String(c?.channel??"")===channel).length;
  return onChannel===1?index.get("channel:"+channel)||null:null;
}
