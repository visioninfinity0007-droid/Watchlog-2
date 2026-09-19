"use client";

const KEY_ID="watchlog:selected-site-id";
const KEY_NAME="watchlog:selected-site-name";

export function selectedSiteId(){
  if(typeof window==="undefined")return "";
  const params=new URLSearchParams(window.location.search);
  return params.get("site")||window.localStorage.getItem(KEY_ID)||"";
}

export function selectedSiteName(){
  if(typeof window==="undefined")return "";
  return window.localStorage.getItem(KEY_NAME)||"";
}

export function rememberSite(id,name=""){
  if(typeof window==="undefined"||!id)return;
  window.localStorage.setItem(KEY_ID,String(id));
  if(name)window.localStorage.setItem(KEY_NAME,String(name));
}

export function clearRememberedSite(){
  if(typeof window==="undefined")return;
  window.localStorage.removeItem(KEY_ID);
  window.localStorage.removeItem(KEY_NAME);
}

export function withSite(href,id=selectedSiteId()){
  if(!id||!href||href.startsWith("http"))return href;
  const [path,hash=""]=href.split("#");
  const join=path.includes("?")?"&":"?";
  return `${path}${join}site=${encodeURIComponent(id)}${hash?`#${hash}`:""}`;
}
