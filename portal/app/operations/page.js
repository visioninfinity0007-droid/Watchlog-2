"use client";

import {useEffect} from "react";

export default function OperationsRedirect(){
  useEffect(()=>{
    const params=new URLSearchParams(location.search);
    location.replace("/incidents/"+(params.toString()?"?"+params.toString():""));
  },[]);
  return <div className="center"><p className="muted">Opening Incident Review…</p></div>;
}
