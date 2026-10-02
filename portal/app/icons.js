"use client";

const common={fill:"none",stroke:"currentColor",strokeWidth:1.8,strokeLinecap:"round",strokeLinejoin:"round"};

export function PortalIcon({name,size=18,className=""}){
  const key=String(name||"").toLowerCase();
  const props={width:size,height:size,viewBox:"0 0 24 24","aria-hidden":"true",className};
  let body=null;
  if(key==="home")body=<><path {...common} d="M3.8 10.7 12 4l8.2 6.7"/><path {...common} d="M5.7 9.4V20h12.6V9.4"/><path {...common} d="M9.5 20v-5.8h5V20"/></>;
  else if(key==="attention"||key==="notifications"||key==="incidents")body=<><path {...common} d="M12 3.9 21 19.5H3L12 3.9Z"/><path {...common} d="M12 9v4.2"/><path {...common} d="M12 16.8h.01"/></>;
  else if(key==="insights"||key==="analytics")body=<><path {...common} d="M4 19V9"/><path {...common} d="M10 19V5"/><path {...common} d="M16 19v-7"/><path {...common} d="M22 19H2"/></>;
  else if(key==="reports")body=<><path {...common} d="M6 3h8l4 4v14H6z"/><path {...common} d="M14 3v5h4"/><path {...common} d="M9 12h6M9 16h6"/></>;
  else if(key==="ask watchlog"||key==="ask")body=<><path {...common} d="M5 5.5h10a4 4 0 0 1 4 4v3a4 4 0 0 1-4 4H10l-4 3v-3H5a4 4 0 0 1-4-4v-3a4 4 0 0 1 4-4Z"/><path {...common} d="m13.9 8 .45 1.15L15.5 9.6l-1.15.45-.45 1.15-.45-1.15-1.15-.45 1.15-.45L13.9 8Z"/></>;
  else if(key==="cameras & evidence"||key==="cameras"||key==="control room")body=<><rect {...common} x="3" y="6.5" width="14" height="11" rx="2"/><path {...common} d="m17 10 4-2.2v8.4L17 14"/></>;
  else if(key==="system health"||key==="site health")body=<><path {...common} d="M3 12h4l2-5 4 10 2-5h6"/><path {...common} d="M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Z"/></>;
  else if(key==="camera settings"||key==="site control"||key==="account")body=<><path {...common} d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle {...common} cx="16" cy="7" r="2"/><circle {...common} cx="8" cy="17" r="2"/></>;
  else if(key==="saved video"||key==="archive")body=<><rect {...common} x="4" y="5" width="16" height="14" rx="2"/><path {...common} d="M8 3h8M9.5 10.2l5 2.8-5 2.8z"/></>;
  else if(key==="team")body=<><circle {...common} cx="9" cy="8" r="3"/><path {...common} d="M3.5 19c.7-3.2 2.5-4.8 5.5-4.8s4.8 1.6 5.5 4.8"/><path {...common} d="M15.2 6.2a2.7 2.7 0 0 1 0 5.2M16.5 14c2.2.5 3.6 2.1 4 4.4"/></>;
  else if(key==="setup & support"||key==="setup")body=<><path {...common} d="M14.8 6.1a4 4 0 0 0-5.3 5.3L4 16.9 7.1 20l5.5-5.5a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.2-.9-.9-2.2 2.5-2.5Z"/></>;
  else if(key==="more")body=<><circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/></>;
  else if(key==="site"||key==="sites")body=<><path {...common} d="M12 21s6-5.3 6-11a6 6 0 1 0-12 0c0 5.7 6 11 6 11Z"/><circle {...common} cx="12" cy="10" r="2"/></>;
  else body=<circle {...common} cx="12" cy="12" r="8"/>;
  return <svg {...props}>{body}</svg>;
}
