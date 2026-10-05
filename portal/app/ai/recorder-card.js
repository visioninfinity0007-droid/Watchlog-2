// Watch AI recorder card rows (MNVR-051). React-free so it can be unit-checked in plain node
// (prototype/tests/test_portal_recorder_surfaces.py).
//
// "WatchLog support: Checked" only when the card says capability_known === true for a single recorder.
// A multi-recorder site has no site-wide capability truth: one row per recorder, and support stays
// "Not yet confirmed" whatever a site-level flag says.
function recorderState(state){
  switch(String(state||"").toLowerCase()){
    case"healthy":return"Available";
    case"offline":return"Unavailable";
    case"attention":return"Needs attention";
    default:return"Not verified";
  }
}

export function recorderCardRows(d){
  const data=d&&typeof d==="object"?d:{};
  const recorders=Array.isArray(data.recorders)?data.recorders:[];
  if(recorders.length>1)return[
    ...recorders.map((r,i)=>({key:"recorder-"+i,label:r?.name||"Recorder "+(i+1),value:recorderState(r?.state)})),
    {key:"support",label:"WatchLog support",value:"Not yet confirmed"}
  ];
  const x=data.recorder&&typeof data.recorder==="object"?data.recorder:data;
  return[
    {key:"system",label:"System",value:[x.vendor,x.model].filter(Boolean).join(" ")||"Not identified"},
    {key:"support",label:"WatchLog support",value:data.capability_known===true?"Checked":"Not yet confirmed"}
  ];
}
