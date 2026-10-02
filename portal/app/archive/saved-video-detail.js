import {Section,Status,Empty} from "../owner/ui";
import styles from "./archive.module.css";

function when(v,timeZone){if(!v)return"—";try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",year:"numeric",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(v))+" · site time"}catch{return String(v)}}
function human(v){return String(v||"Activity").replaceAll("_"," ").replace(/^./,c=>c.toUpperCase())}
function statusTone(v){return v==="complete"?"verified":v==="failed"?"bad":"unknown"}

export default function SavedVideoDetail({detail,onClose,timeZone}){
  if(!detail)return null;
  const results=detail.results||[];
  return <div className={styles.detail} style={{marginTop:26}}>
    <Section title="Recovered activity" count={results.length||null} action={<button type="button" className={styles.close} onClick={onClose}>Close</button>}>
      <dl className={styles.meta}>
        <div><dt>Video period</dt><dd>{when(detail.scan?.from_ts,timeZone)} → {when(detail.scan?.to_ts,timeZone)}</dd></div>
        <div><dt>Status</dt><dd><Status tone={statusTone(detail.scan?.status)}>{human(detail.scan?.status)}</Status></dd></div>
        <div><dt>Source</dt><dd>Recovered from saved video</dd></div>
      </dl>
      {results.length?<table className="ow-table"><thead><tr><th>Found</th><th>Activity</th><th>Source</th></tr></thead><tbody>{results.map(r=><tr key={r.id}><td>{when(r.recovered_at,timeZone)}</td><td><b>{human(r.result_type)}</b></td><td>Saved video</td></tr>)}</tbody></table>
        :<Empty title="No matching activity recovered yet.">Nothing has been recovered from this video period so far.</Empty>}
    </Section>
  </div>;
}
