"use client";
import Mark from "./mark";
export default function MobileLauncher({open,onToggle}){return <div className="productMobileBar"><div className="productMobileBrand"><Mark size={25}/><b>WatchLog</b></div><button className="productMobileLauncher" onClick={onToggle} aria-label={open?"Close WatchLog navigation":"Open WatchLog navigation"} aria-expanded={open}>{open?"×":"☰"}</button></div>}
