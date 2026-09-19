export const MAIN_TABS=[["Reports","/reports/","Reports"],["Setup","/setup/","Setup"]];
export const MORE_TABS=[["Cameras","/control-room/","Control Room"],["Incidents","/incidents/","Incidents"],["Site Health","/site-health/","Site Health"],["Activity","/analytics/","Analytics"],["Saved Video","/archive/","Archive"],["Settings","/settings/","Settings"]];
export const MORE_ACTIVE=new Set(MORE_TABS.map(x=>x[2]));
export const ACTIVE_ROUTE={"WatchLog AI":"/ai/",Reports:"/reports/",Setup:"/setup/","Control Room":"/control-room/",Incidents:"/incidents/","Site Health":"/site-health/",Analytics:"/analytics/",Archive:"/archive/",Settings:"/settings/","Site Control":"/site-control/"};
