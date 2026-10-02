import {Section,Status,Empty} from "../owner/ui";
import styles from "./archive.module.css";

function when(v,timeZone){if(!v)return"—";try{return new Intl.DateTimeFormat("en-PK",{timeZone:timeZone||"Asia/Karachi",day:"numeric",month:"short",hour:"numeric",minute:"2-digit",hour12:true}).format(new Date(v))+" · site time"}catch{return String(v)}}
function label(v){return String(v||"Requested").replaceAll("_"," ").replace(/^./,c=>c.toUpperCase())}
function statusTone(v){return v==="complete"?"verified":v==="failed"?"bad":"unknown"}

export default function SavedVideoHistory({items,onOpen,timeZone}){
  const recent=(items||[]).slice(0,8);
  return <Section first title="Recent searches" count={recent.length||null}>
    {recent.length?<div className={styles.history}>{recent.map(x=>{const n=(x.camera_ids||[]).length;return <article className={styles.historyItem} key={x.id}>
      <i className={`${styles.tick} ${styles[statusTone(x.status)]||""}`} aria-hidden="true"/>
      <div className={styles.historyMain}>
        <div className={styles.historyTop}><b>{when(x.from_ts,timeZone)} <span aria-hidden="true">→</span> {when(x.to_ts,timeZone)}</b><Status tone={statusTone(x.status)}>{label(x.status)}</Status></div>
        <small>{n} camera{n===1?"":"s"} · requested {when(x.requested_at,timeZone)}</small>
      </div>
      <button type="button" className="ghost small" onClick={()=>onOpen(x.id)} aria-label={"View results for the search requested "+when(x.requested_at,timeZone)}>View</button>
    </article>})}</div>:<Empty title="No saved-video searches for this site yet.">Searches you run appear here with their status.</Empty>}
  </Section>;
}
