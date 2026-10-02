import {withSite} from "../site-context";
import {Section,Status} from "../owner/ui";
import styles from "./archive.module.css";

export default function SavedVideoForm({v}){
  if(!v.available)return <Section first title="Search saved video">
    <div className={styles.unavailable}>
      <Status tone="unknown">Not available at this site</Status>
      <h3>Saved-video search is not enabled for this camera system.</h3>
      <p>Search controls appear here once this site supports it. Home, Attention and Reports stay available.</p>
      <a href={withSite(`/ai/?prompt=${encodeURIComponent("Can this site support saved-video search, and what would be required to enable it?")}`,v.siteId)}>Ask WatchLog about availability →</a>
    </div>
  </Section>;

  return <Section first title="Search saved video" note="Pick cameras and a time window of up to seven days.">
    <div className={styles.form}>
      <div className={styles.siteLine}>
        <b>{v.site?.name||"This site"}</b>
        <Status tone="verified">Saved-video search available</Status>
      </div>
      <fieldset className={styles.fieldset}>
        <legend>Cameras</legend>
        {v.cameras.length?<div className={styles.checks}>{v.cameras.map(c=><label key={c.id} className="ow-check">
          <input type="checkbox" checked={v.form.cams.includes(c.id)} onChange={()=>v.toggle("cams",c.id)}/>
          <span>{c.name||`Camera ${c.channel}`}</span>
        </label>)}</div>:<span className={styles.emptyInline}>No cameras are available for this site.</span>}
      </fieldset>
      <div className={styles.row2}>
        <label className="ow-field">Start<input type="datetime-local" value={v.form.from} onChange={e=>v.setForm({...v.form,from:e.target.value})}/></label>
        <label className="ow-field">End<input type="datetime-local" value={v.form.to} onChange={e=>v.setForm({...v.form,to:e.target.value})}/></label>
      </div>
      <p className={styles.helper}>Times are interpreted in this site’s local time{v.site?.timezone?` (${v.site.timezone})`:""}.</p>
      {v.rules.length>0&&<fieldset className={styles.fieldset}>
        <legend>Activity filters<em>optional</em></legend>
        <div className={styles.checks}>{v.rules.map(r=><label key={r.id} className="ow-check">
          <input type="checkbox" checked={v.form.rules.includes(r.id)} onChange={()=>v.toggle("rules",r.id)}/>
          <span>{r.name}</span>
        </label>)}</div>
      </fieldset>}
      <button type="button" className={styles.searchButton} disabled={v.busy} aria-busy={v.busy} onClick={v.request}>{v.busy?"Searching…":"Search saved video"}</button>
    </div>
  </Section>;
}
