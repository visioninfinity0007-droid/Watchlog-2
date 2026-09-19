import {withSite} from "../site-context";
const ROUTES={navigate:"/ai/",setup_context:"/setup/",setup_camera:"/setup/",watchlog_rule:"/analytics/studio/",site_control_proposal:"/site-control/"};
export default function CustomerActions({actions=[],siteId,styles}){const safe=(Array.isArray(actions)?actions:[]).filter(a=>ROUTES[a?.kind]).slice(0,4);if(!safe.length)return null;return <div className={styles.suggestionRow}>{safe.map((a,i)=><a key={`${a.kind}-${i}`} href={withSite(ROUTES[a.kind],siteId)}>{a.label||"Continue"} →</a>)}</div>}
