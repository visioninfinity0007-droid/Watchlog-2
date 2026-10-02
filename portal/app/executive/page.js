"use client";

import {useEffect} from "react";

export default function ExecutiveRedirect(){
  useEffect(()=>{
    const input=new URLSearchParams(location.search);
    const days=Number(input.get("days")||7);
    const site=input.get("site")||"";
    const output=new URLSearchParams();
    output.set("view",days>=30?"month":days>=7?"week":"daily");
    if(site)output.set("site",site);
    location.replace("/reports/?"+output.toString());
  },[]);
  return <div className="center"><p className="muted">Opening Reports…</p></div>;
}
