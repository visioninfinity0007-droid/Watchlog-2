import {siteState} from "./customer-prompts";
export default function CustomerHeader({site,ctx,styles}){const s=siteState(ctx);return <header className={styles.topbar}><div><b>{site?.name||"WatchLog AI"}</b><span>Ask WatchLog</span></div><div className={styles.topActions}><span className={styles.statusChip}><span className={`${styles.dot} ${styles[s[1]]}`}/>{s[0]}</span></div></header>}
