// Site Control recorder rules. React-free so they can be unit-checked in plain node
// (prototype/tests/test_portal_recorder_surfaces.py).

// MNVR-049: capability evidence is recorded per recorder model, not per recorder. Only evidence
// scoped to this recorder (evidence_scope === "recorder") may read "Verified on your camera system"
// and be offered for change; evidence from the same recorder type elsewhere is never presented as
// verified here.
const verifiedHere=cap=>String(cap?.evidence_class||"").toUpperCase()==="FIELD_VERIFIED"&&cap?.evidence_scope==="recorder";

export function evidenceLabel(cap){
  switch(String(cap?.evidence_class||"").toUpperCase()){
    case"FIELD_VERIFIED":return verifiedHere(cap)?"Verified on this system":"Verified on the same recorder type";
    case"OFFICIAL_DOCUMENTED":return"Documented by the maker";
    case"IMPLEMENTED_UNVERIFIED":return"Needs verification";
    case"UNSUPPORTED":return"Not available";
    default:return"Not verified";
  }
}
// Capability-aware treatment of every verdict x evidence pairing. UNKNOWN stays honestly "Not verified";
// documented support is never presented as verified; by_camera is camera-side, not recorder-
// configurable; unsupported is disabled with the recorder's own reason. Only a capability verified on
// this recorder carries colour; every other state stays neutral with its word.
export function capView(cap){
  const v=cap?.verdict;
  if(v==="supported"&&verifiedHere(cap))return{tone:"verified",label:"Configurable",note:"Verified on your camera system",action:"configure"};
  if(v==="supported"&&String(cap?.evidence_class||"").toUpperCase()==="FIELD_VERIFIED")return{tone:"unknown",label:"Not verified here",note:"Works on the same recorder type — WatchLog will confirm on your system first",action:"verify"};
  if(v==="supported")return{tone:"unknown",label:"Documented",note:"Listed by the maker — WatchLog will confirm on your system first",action:"verify"};
  if(v==="by_camera")return{tone:"unknown",label:"Camera-side",note:"Set on the camera itself, not the recorder",action:"none"};
  if(v==="unsupported")return{tone:"unknown",label:"Not available",note:cap.constraints||"This camera system does not support this setting",action:"disabled"};
  return{tone:"unknown",label:"Not verified",note:"WatchLog will confirm this on your system before proposing a change",action:"check"};
}

// MNVR-048: Site Control shows cameras per recorder, keyed by camera identity, never raw recorder
// profile rows. The recorder-aware diagnosis (0155) already returns canonical cameras per recorder;
// the fallback below keeps an older diagnosis shape (one site-wide camera list that could include
// legacy-profile-N / MediaProfile_Channel... rows) customer-safe.
const RAW_CHANNEL=/^legacy-profile-/i;
const RAW_SUBSTREAM=/^(Legacy )?MediaProfile_Channel\d+_SubStream/i;
const RAW_MAIN=/^(Legacy )?MediaProfile_Channel(\d+)_MainStream/i;

function cameraRows(list,scope){
  return(Array.isArray(list)?list:[])
    .filter(c=>!RAW_CHANNEL.test(String(c?.channel??""))&&!RAW_SUBSTREAM.test(String(c?.name||"")))
    .map((c,i)=>{
      const raw=String(c?.name||"").match(RAW_MAIN);
      const channel=c?.channel??(raw?raw[1]:null);
      const cameraId=c?.camera_id??null;
      return{
        key:cameraId!=null?String(cameraId):scope+":"+String(channel??"")+":"+i,
        cameraId,
        channel,
        name:raw||!c?.name?"Camera "+(channel??raw?.[1]??i+1):c.name,
        purpose:c?.purpose||null
      };
    });
}
function systemName(identified,vendor,model){return identified?[vendor,model].filter(Boolean).join(" "):""}

export function recorderGroups(d){
  const recorders=Array.isArray(d?.recorders)?d.recorders:[];
  if(recorders.length)return recorders.map((r,i)=>{
    const identified=r?.identified===true;
    return{
      key:"recorder-"+(r?.recorder_id??i),
      recorderId:r?.recorder_id??null,
      name:r?.display_name||"Recorder "+(i+1),
      identified,
      system:systemName(identified,r?.vendor,r?.model),
      capabilityKnown:r?.capability_known===true,
      cameras:cameraRows(r?.cameras,"recorder-"+(r?.recorder_id??i))
    };
  });
  const system=d?.recorder||{},identified=system.identified===true;
  return[{
    key:"recorder-site",
    recorderId:null,
    name:"Recorder",
    identified,
    system:systemName(identified,system.vendor,system.model),
    capabilityKnown:d?.capability_known===true,
    cameras:cameraRows(d?.cameras,"site")
  }];
}
