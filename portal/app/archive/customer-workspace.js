"use client";
import {OwnerPage,Lead,Notice} from "../owner/ui";
import useSavedVideo from "./use-saved-video";
import SavedVideoForm from "./saved-video-form";
import SavedVideoHistory from "./saved-video-history";
import SavedVideoDetail from "./saved-video-detail";
import styles from "./archive.module.css";

export default function CustomerArchive(){
  const v=useSavedVideo();
  const tz=v.site?.timezone;
  const pending=(v.items||[]).filter(x=>x.status!=="complete"&&x.status!=="failed").length;
  return <OwnerPage active="Archive" email={v.email} siteId={v.siteId}
    kicker={["Saved Video",tz?"Site time "+tz.replace(/^.*\//,"").replace(/_/g," "):null]}
    title={`Find earlier activity at ${v.site?.name||"this site"}`}>
    {v.error&&<Notice tone="bad">{v.error}</Notice>}
    {v.available&&pending>0&&<Lead tone="unknown" title={pending+" saved-video search"+(pending===1?" is":"es are")+" in progress"} body="Results appear under Recent searches when ready."/>}
    <div className={styles.grid}>
      <SavedVideoForm v={v}/>
      <SavedVideoHistory items={v.items} onOpen={v.open} timeZone={tz}/>
    </div>
    <SavedVideoDetail detail={v.detail} onClose={()=>v.setDetail(null)} timeZone={tz}/>
  </OwnerPage>;
}
