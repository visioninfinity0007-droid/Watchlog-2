"use client";
import {withSite} from "../site-context";
import useCustomerControl from "./use-customer-control";
import {OwnerPage,Lead,Section,Row,Status,RailSection,Stat,Summary,Empty,Loading,Notice,AskLinks} from "../owner/ui";

const CAPABILITY_LABELS={human_vehicle_classification:"People and vehicle recognition",sensitivity_config:"Detection sensitivity",line_crossing:"Line crossing",intrusion:"Area intrusion",video_loss:"Camera signal monitoring",tamper:"Camera tamper detection",channel_title:"Camera names",time_ntp_config:"Time synchronization",snapshot:"Camera images",event_stream:"Camera-system alerts",storage_health:"Storage health",recording_mode:"Recording status",clip_export:"Saved-video export",face_detection:"Face detection",anpr_lpr:"Number-plate recognition",people_counting:"People counting",heatmap:"Heatmaps"};
// Site types accepted by the site business context (wl_upsert_site_context).
const SITE_TYPES=[["office","Office"],["retail","Retail"],["restaurant","Restaurant / cafe"],["warehouse","Warehouse"],["factory","Factory / industrial"],["clinic","Clinic"],["other","Other"]];
function plain(v){const key=String(v||"").toLowerCase();if(CAPABILITY_LABELS[key])return CAPABILITY_LABELS[key];return String(v||"Feature").replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase()).replace(/\bNtp\b/g,"time").replace(/\bAnpr Lpr\b/g,"Number-plate recognition")}
function human(v){if(!v)return"Not checked";return String(v).replaceAll("_"," ").replace(/^./,c=>c.toUpperCase())}
function area(v){return v?String(v).replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase()):"Purpose not set"}
function evidenceLabel(v){switch(String(v||"").toUpperCase()){case"FIELD_VERIFIED":return"Verified on this system";case"OFFICIAL_DOCUMENTED":return"Documented by the maker";case"IMPLEMENTED_UNVERIFIED":return"Needs verification";case"UNSUPPORTED":return"Not available";default:return"Not verified"}}
// Capability-aware treatment of every verdict x evidence pairing. UNKNOWN stays honestly "Not verified";
// documented support is never presented as field-verified; by_camera is camera-side, not recorder-
// configurable; unsupported is disabled with the recorder's own reason. Only a verified capability
// carries colour; every other state stays neutral with its word.
function capView(cap){const v=cap.verdict,e=cap.evidence_class;if(v==="supported"&&e==="FIELD_VERIFIED")return{tone:"verified",label:"Configurable",note:"Verified on your camera system",action:"configure"};if(v==="supported")return{tone:"unknown",label:"Documented",note:"Listed by the maker — WatchLog will confirm on your system first",action:"verify"};if(v==="by_camera")return{tone:"unknown",label:"Camera-side",note:"Set on the camera itself, not the recorder",action:"none"};if(v==="unsupported")return{tone:"unknown",label:"Not available",note:cap.constraints||"This camera system does not support this setting",action:"disabled"};return{tone:"unknown",label:"Not verified",note:"WatchLog will confirm this on your system before proposing a change",action:"check"};}

export default function CustomerControl(){
  const c=useCustomerControl(),d=c.data||{},system=d.recorder||{},conn=d.connectivity||{},caps=d.capabilities||[],tiers=d.tiers||{},role=d.role||"viewer";
  const enabled=(d.site_control_enabled??d.flags?.site_control_enabled??d.enabled)!==false,connected=Boolean(conn.agent_online),seen=Boolean(conn.last_seen);
  const canApprove=tiers.approve,canRecommend=tiers.recommend;
  const cameras=d.cameras||[];
  // Every change is initiated through the governed WatchLog AI conversation, which proposes the write
  // server-side (wl_site_command_propose_write) and never runs it without approval. The portal only
  // surfaces the capability truth and role-gates who may recommend versus approve.
  function askLink(cap){const what=cap?plain(cap.capability):"a camera-system setting";const verb=canApprove?"recommend the change and, once you approve it, apply it":"recommend the change for an Owner or Admin to approve";const prompt=`I want to change ${what} at this site. Confirm it is safely supported here, then ${verb}. Do not make any change without explicit approval.`;return withSite(`/ai/?prompt=${encodeURIComponent(prompt)}`,c.siteId);}

  const views=caps.map(cap=>({cap,view:capView(cap)}));
  const configurable=views.filter(x=>x.view.action==="configure").length;
  const unverified=views.filter(x=>x.view.action==="check"||x.view.action==="verify").length;
  const accessWord=canApprove?"You can approve changes":canRecommend?"You can recommend changes":"Read only";
  const connectionTone=connected?"ok":seen?"bad":"unknown";
  const connectionWord=connected?"Connected":seen?"Disconnected":"Not connected yet";
  const systemName=[system.vendor,system.model].filter(Boolean).join(" ");

  let leadTone="unknown",leadTitle="No settings are verified for change yet",leadBody="WatchLog will not guess what this camera system supports.";
  if(!seen&&!connected){leadTitle="This camera system is not connected yet";leadBody="Settings appear here after WatchLog connects to the site.";}
  else if(!connected){leadTone="warn";leadTitle="Site connection lost · changes wait until it reconnects";leadBody="Approved changes are carried out only while the site is connected.";}
  else if(configurable){leadTone="ok";leadTitle=configurable+" setting"+(configurable===1?" is":"s are")+" verified for change";leadBody=accessWord+"."+(unverified?" "+unverified+" more "+(unverified===1?"is":"are")+" Not verified yet.":"");}

  const rail=c.data&&enabled?<>
    <RailSection label="Camera system">
      <Stat label="Connection" value={<Status tone={connectionTone}>{connectionWord}</Status>}/>
      <Stat label="Recorder" note={systemName||null} value={<Status tone={system.identified?"verified":"unknown"}>{system.identified?"Identified":"Not confirmed"}</Status>}/>
      <Stat label="Settings verified for change" value={caps.length?configurable+" of "+caps.length:null}/>
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
    {value:caps.length?configurable+"/"+caps.length:"—",label:"Verified for change",muted:!caps.length},
    {value:canApprove?"Approve":canRecommend?"Recommend":"Read only",label:"Your access"}
  ]}/>:null;

  return <OwnerPage active="Site Control" email={c.email} siteId={c.siteId}
    kicker={["Camera Settings",c.site?.name]}
    title="Camera settings"
    actions={c.data&&enabled?<a className="ow-btn quiet" href={askLink(null)}>Ask WatchLog to change something</a>:null}
    rail={rail} summary={summary}>
    {c.error&&<Notice tone="bad">{c.error}</Notice>}
    {c.note&&<Notice tone="ok">{c.note}</Notice>}
    {!c.data?(c.error?null:<Loading label="Checking this camera system"/>):!enabled?<>
      <Lead tone="unknown" title="Camera-system changes are not enabled for this site." body="Monitoring, reports and recommendations still work. Direct camera-system changes remain unavailable."/>
    </>:<>
      <Lead tone={leadTone} title={leadTitle} body={leadBody}/>

      <Section first title="Camera channels" count={cameras.length||null} note="Monitored cameras and their areas are chosen in Setup & Support." action={<a href={withSite("/setup/",c.siteId)}>Change cameras or areas</a>}>
        {cameras.length?<table className="ow-table">
          <thead><tr><th>Camera</th><th>Channel</th><th>Area</th></tr></thead>
          <tbody>{cameras.map((cam,i)=><tr key={(cam.channel??i)+"-"+(cam.name||"")}>
            <td><b>{cam.name||"Camera "+cam.channel}</b></td>
            <td className="ow-muted">{cam.channel??"—"}</td>
            <td className={cam.purpose?"":"ow-muted"}>{area(cam.purpose)}</td>
          </tr>)}</tbody>
        </table>:<Empty title="No cameras found yet.">Cameras appear here after WatchLog connects to the camera system.</Empty>}
      </Section>

      <Section title="Camera-system settings" count={caps.length||null} note="Only settings WatchLog can confirm are offered for change.">
        {caps.length?<table className="ow-table">
          <thead><tr><th>Setting</th><th>Status</th><th>What WatchLog knows</th><th>Change</th></tr></thead>
          <tbody>{views.map(({cap,view})=><tr key={cap.capability}>
            <td><b>{plain(cap.capability)}</b><small>{view.note}</small></td>
            <td><Status tone={view.tone}>{view.label}</Status></td>
            <td className="ow-muted">{evidenceLabel(cap.evidence_class)}</td>
            <td>{view.action==="configure"?(canApprove?<a className="ow-btn quiet small" href={askLink(cap)}>Recommend &amp; approve</a>:canRecommend?<a className="ow-btn quiet small" href={askLink(cap)}>Recommend</a>:<span className="ow-muted">Read only</span>)
              :view.action==="verify"||view.action==="check"?<a className="ow-linkbtn" href={askLink(cap)}>Check on system</a>
              :view.action==="disabled"?<button type="button" className="ow-btn quiet small" disabled title={view.note}>Not available</button>
              :<span className="ow-muted">On the camera</span>}</td>
          </tr>)}</tbody>
        </table>:<Empty title="No configurable settings are confirmed yet.">WatchLog will not guess what this recorder supports.</Empty>}
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
            action={<a href={askLink(null)}>Ask WatchLog</a>}/>
        </div>
      </Section>

      <Section title="Ask WatchLog" className="ow-narrow-only">
        <AskLinks siteId={c.siteId} prompts={["Which camera-system settings can WatchLog change here?"]}/>
      </Section>
    </>}
  </OwnerPage>;
}
