"use client";
import Mark from "./mark";

export default function MobileLauncher({href="/home/"}){
  return <div className="productMobileBar">
    <a className="productMobileBrand" href={href}><Mark size={25}/><b>WatchLog</b></a>
  </div>;
}
