"use client";
import {withSite} from "../site-context";
import useCustomerControl from "./use-customer-control";
import {OwnerPage,Lead,Section,Row,Status,RailSection,Stat,Summary,Empty,Loading,Notice,AskLinks} from "../owner/ui";
import {capView,evidenceLabel,recorderGroups} from "./recorder-control";

const CAPABILITY_LABELS={human_vehicle_classification:"People and vehicle recognition",sensitivity_config:"Detection sensitivity",line_crossing:"Line crossing",intrusion:"Area intrusion",video_loss:"Camera signal monitoring",tamper:"Camera tamper detection",channel_title:"Camera names",time_ntp_config:"Time synchronization",snapshot:"Camera images",event_stream:"Camera-system alerts",storage_health:"Storage health",recording_mode:"Recording status",clip_export:"Saved-video export",face_detection:"Face detection",anpr_lpr:"Number-plate recognition",people_counting:"People counting",heatmap:"Heatmaps"};
// Site types accepted by the site business context (wl_upsert_site_context).
const SITE_TYPES=[["office","Office"],["retail","Retail"],["restaurant","Restaurant / cafe"],["warehouse","Warehouse"],["factory","Factory / industrial"],["clinic","Clinic"],["other","Other"]];
function plain(v){const key=String(v||"").toLowerCase();if(CAPABILITY_LABELS[key])return CAPABILITY_LABELS[key];return String(v||"Feature").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase()).replace(/\bNtp\b/g,"time").replace(/\bAnpr Lpr\b/g,"Number-plate recognition")}
function human(v){if(!v)return"Not checked";return String(v).replaceAll("_"," ").replace(/^./,c=>c.toUpperCase())}
function area(v){return v?String(v).replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase()):"Purpose not set"}

export default function CustomerControl(){
  const c=useCustomerControl(),d=c.data||{},system=d.recorder||{},conn=d.connectivity||{},caps=d.capabilities||[],tiers=d.tiers||{},role=d.role||"viewer";
  const enabled=(d.site_control_enabled??d.flags?.site_control_enabled??d.enabled)!==false,connected=Boolean(conn.agent_online),seen=Boolean(conn.last_seen);
  const canApprove=tiers.approve,canRecommend=tiers.recommend;
  // Cameras per recorder, keyed by camera identity (MNVR-048). A multi-recorder site has no site-wide
  // recorder profile: settings are confirmed on each recorder separately.
  const groups=recorderGroups(d),multiRecorder=groups.length>1;
  const cameraCount=groups.reduce((n,g)=>n+g.cameras.length,0);
  // Every change is initiated through the governed WatchLog AI conversation, which proposes the write
  // server-side (wl_site_command_propose_write) and never runs it without approval. The portal only
  // surfaces the capability truth and role-gates who may recommend versus approve.
  // A change is about one recorder (and camera): the link names them for the customer and carries
  // their ids so the proposal is targeted, never site-wide on a multi-recorder site.
  function askLink(cap,target){const what=cap?plain(cap.capability):"a camera-system setting";const on=multiRecorder&&target?.recorder?` on ${target.recorder}`:"",where=target?.camera?` for ${target.camera}${on}`:on;const verb=canApprove?"recommend the change and, once you approve it, apply it":"recommend the change for an Owner or Admin to approve";const prompt=`I want to change ${what}${where} at this site. Confirm it is safely supported here, then ${verb}. Do not make any change without explicit approval.`;let href=`/ai/?prompt=${encodeURIComponent(prompt)}`;if(target?.recorderId)href+="&recorder="+encodeURIComponent(target.recorderId);if(target?.cameraId)href+="&camera="+encodeURIComponent(target.cameraId);return withSite(href,c.siteId);}
  const single=multiRecorder?null:{recorder:groups[0]?.name,recorderId:groups[0]?.recorderId};

  const views=(multiRecorder?[]:caps).map(cap=>({cap,view:capView(cap)}));
  const configurable=views.filter(x=>x.view.action==="configure").length;
  const unverified=views.filter(x=>x.view.action==="check"||x.view.action==="verify").length;
  const accessWord=canApprove?"You can approve changes":canRecommend?"You can recommend changes":"Read only";
  const connectionTone=connected?"ok":seen?"bad":"unknown";
  const connectionWord=connected?"Connected":seen?"Disconnected":"Not connected yet";
  const systemName=multiRecorder?"":groups[0]?.system||"";
  const identifiedRecorders=groups.filter(g=>g.identified).length;

  let leadTone="unknown",leadTitle="No settings are verified for change yet",leadBody="WatchLog will not guess what this camera system supports.";
  if(!seen&&!connected){leadTitle="This camera system is not connected yet";leadBody="Settings appear here after WatchLog connects to the site.";}
  else if(!connected){leadTone="warn";leadTitle="Site connection lost · changes wait until it reconnects";leadBody="Approved changes are carried out only while the site is connected.";}
  else if(configurable){leadTone="ok";leadTitle=configurable+" setting"+(configurable===1?" is":"s are")+" verified for change";leadBody=accessWord+"."+(unverified?" "+unverified+" more "+(unverified===1?"is":"are")+" Not verified yet.":"");}

  const rail=c.data&&enabled?<>
    <RailSection label="Camera system">
      <Stat label="Connection" value={<Status tone={connectionTone}>{connectionWord}</Status>}/>
      {multiRecorder?<Stat label="Recorders identified" value={identifiedRecorders+" of "+groups.length}/>
        :<Stat label="Recorder" note={systemName||null} value={<Status tone={system.identified?"verified":"unknown"}>{system.identified?"Identified":"Not confirmed"}</Status>}/>}
      <Stat label="Settings verified for change" value={caps.length&&!multiRecorder?configurable+" of "+caps.length:null}/>
    </RailSection>
    <RailSection label="Your access">
      <Stat label="Role" value={human(role)}/>
      <Stat label="Changes" value={accessWord}/>
    </RailSection>
    <RailSection label="Ask WatchLog">
      <AskLinks siteId={c.siteId} prompts={["Which camera-system settings can WatchLog change here?","Is the recorder clock in sync?"]}/>
    </RailSection>
  </>:null;

  const summary=c.data&&enabled?<Summary items={[
    {value:connectionWord,label:"Connection",muted:!connected},
    {value:caps.length&&!multiRecorder?configurable+"/"+caps.length:"—",label:"Verified for change",muted:!caps.length||multiRecorder},
    {value:canApprove?"Approve":canRecommend?"Recommend":"Read only",label:"Your access"}
  ]}/>:null;

  return <OwnerPage active="Site Control" email={c.email} siteId={c.siteId}
    kicker={["Camera Settings",c.site?.name]}
    title="Camera settings"
    actions={c.data&&enabled?<a className="ow-btn quiet" href={askLink(null,single)}>Ask WatchLog to change something</a>:null}
    rail={rail} summary={summary}>
    {c.error&&<Notice tone="bad">{c.error}</Notice>}
    {c.note&&<Notice tone="ok">{c.note}</Notice>}
    {!c.data?(c.error?null:<Loading label="Checking this camera system"/>):!enabled?<>
      <Lead tone="unknown" title="Camera-system changes are not enabled for this site." body="Monitoring, reports and recommendations still work. Direct camera-system changes remain unavailable."/>
    </>:<>
      <Lead tone={leadTone} title={leadTitle} body={leadBody}/>

      <Section first title="Camera channels" count={cameraCount||null} note="Monitored cameras and their areas are chosen in Setup & Support." action={<a href={withSite("/setup/",c.siteId)}>Change cameras or areas</a>}>
        {cameraCount?<table className="ow-table">
          <thead><tr><th>Camera</th><th>Channel</th><th>Area</th>{canRecommend&&<th><span className="ow-muted">Change</span></th>}</tr></thead>
          {groups.map(g=><tbody key={g.key}>
            {multiRecorder&&<tr><th colSpan={canRecommend?4:3}>{g.name} <span className="ow-muted">· {g.identified?(g.system||"Identified"):"Recorder not confirmed yet"}</span></th></tr>}
            {g.cameras.map(cam=><tr key={cam.key}>
              <td><b>{cam.name}</b></td>
              <td className="ow-muted">{cam.channel??"—"}</td>
              <td className={cam.purpose?"":"ow-muted"}>{area(cam.purpose)}</td>
              {canRecommend&&<td><a className="ow-linkbtn" href={askLink(null,{recorder:g.name,recorderId:g.recorderId,camera:cam.name,cameraId:cam.cameraId})}>Ask to change</a></td>}
            </tr>)}
          </tbody>)}
        </table>:<Empty title="No cameras found yet.">Cameras appear here after WatchLog connects to the camera system.</Empty>}
      </Section>

      <Section title="Camera-system settings" count={multiRecorder?null:caps.length||null} note={multiRecorder?"Settings are confirmed separately on each recorder. Only settings WatchLog can confirm are offered for change.":"Only settings WatchLog can confirm are offered for change."}>
        {caps.length&&!multiRecorder?<table className="ow-table">
          <thead><tr><th>Setting</th><th>Status</th><th>What WatchLog knows</th><th>Change</th></tr></thead>
          <tbody>{views.map(({cap,view})=><tr key={cap.capability}>
            <td><b>{plain(cap.capability)}</b><small>{view.note}</small></td>
            <td><Status tone={view.tone}>{view.label}</Status></td>
            <td className="ow-muted">{evidenceLabel(cap)}</td>
            <td>{view.action==="configure"?(canApprove?<a className="ow-btn quiet small" href={askLink(cap,single)}>Recommend &amp; approve</a>:canRecommend?<a className="ow-btn quiet small" href={askLink(cap,single)}>Recommend</a>:<span className="ow-muted">Read only</span>)
              :view.action==="verify"||view.action==="check"?<a className="ow-linkbtn" href={askLink(cap,single)}>Check on system</a>
              :view.action==="disabled"?<button type="button" className="ow-btn quiet small" disabled title={view.note}>Not available</button>
              :<span className="ow-muted">On the camera</span>}</td>
          </tr>)}</tbody>
        </table>:multiRecorder?<div className="ow-rows">{groups.map(g=><Row key={g.key} compact tone="unknown" title={g.name}
          body={!g.identified?"This recorder is not confirmed yet. WatchLog will not guess what it supports.":g.capabilityKnown?"Settings for this recorder type are on record. WatchLog confirms each one on this recorder before proposing a change.":"No settings are on record for this recorder yet. WatchLog will not guess what it supports."}
          action={<a className="ow-linkbtn" href={askLink(null,{recorder:g.name,recorderId:g.recorderId})}>Check on this recorder</a>}/>)}</div>
        :<Empty title="No configurable settings are confirmed yet.">WatchLog will not guess what this recorder supports.</Empty>}
      </Section>

      <Section title="Business context" note="Helps WatchLog tailor safe recommendations for this location.">
        <div className="ow-rows">
          <Row compact tone={c.context?.site_type?"neutral":"unknown"} title="Site type" body={c.context?.site_type?null:"Not set"}
            action={<select aria-label="Site type" value={c.context?.site_type||""} disabled={!canRecommend} onChange={e=>c.saveSiteType(e.target.value)}><option value="">Not set</option>{SITE_TYPES.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>}/>
          {c.onboarding?.setup_state&&<Row compact tone="unknown" title="Setup state" body="Current connection and setup status for this site." action={<Status tone="unknown">{human(c.onboarding.setup_state)}</Status>}/>}
        </div>
      </Section>

      <Section title="How changes are made" note="Read → Recommend → Approve. Nothing changes without approval.">
        <div className="ow-rows">
          <Row compact tone="neutral" title={accessWord}
            body={canApprove?"You can recommend a change and approve it. WatchLog carries out only the approved change.":canRecommend?"You can recommend a change; only an Owner or Admin can approve it.":"You can see what this camera system supports. An Owner, Admin or Manager can recommend changes."}
            action={<a href={askLink(null,single)}>Ask WatchLog</a>}/>
        </div>
      </Section>

      <Section title="Ask WatchLog" className="ow-narrow-only">
        <AskLinks siteId={c.siteId} prompts={["Which camera-system settings can WatchLog change here?"]}/>
      </Section>
    </>}
  </OwnerPage>;
}
