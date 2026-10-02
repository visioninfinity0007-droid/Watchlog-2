export const MAIN_TABS=[
  ["Home","/home/","Home"],
  ["Attention","/notifications/","Notifications"],
  ["Insights","/analytics/","Analytics"],
  ["Reports","/reports/?view=yesterday","Reports"],
  ["Ask WatchLog","/ai/","WatchLog AI"]
];
export const MORE_TABS=[
  ["Cameras & Evidence","/control-room/","Control Room"],
  ["System Health","/site-health/","Site Health"],
  ["Camera Settings","/site-control/","Site Control"],
  ["Saved Video","/archive/","Archive"],
  ["Team","/team/","Team"],
  ["Setup & Support","/setup/","Setup"],
  ["Account","/settings/","Settings"]
];
export const MORE_ACTIVE=new Set(MORE_TABS.map(x=>x[2]));
export const ACTIVE_ROUTE={
  Home:"/home/",
  Executive:"/home/",
  "WatchLog AI":"/ai/",
  Notifications:"/notifications/",
  Reports:"/reports/?view=yesterday",
  Setup:"/setup/",
  "Control Room":"/control-room/",
  Incidents:"/incidents/",
  "Site Health":"/site-health/",
  Analytics:"/analytics/",
  Archive:"/archive/",
  Settings:"/settings/",
  "Site Control":"/site-control/",
  Team:"/team/"
};
