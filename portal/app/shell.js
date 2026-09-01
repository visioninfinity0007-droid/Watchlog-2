"use client";

import { useEffect, useState } from "react";
import Mark from "./mark";
import { supabase } from "../lib/supabase";

const TABS = [
  ["Overview", "/dashboard/"],
  ["Incidents", "/incidents/"],
  ["Site Health", "/site-health/"],
  ["Analytics", "/analytics/"],
  ["Reports", "/reports/"],
  ["Team", "/team/"],
  ["Settings", "/settings/"],
];

/** The shared top bar + primary navigation. Used on every signed-in page so
 *  the portal reads as one product, not a set of disconnected screens. */
export function Nav({ active, email, right }) {
  const [platform,setPlatform]=useState(null);
  useEffect(()=>{let live=true;(async()=>{const {data}=await supabase().rpc("wl_platform_me");if(live&&data?.role)setPlatform(data);})();return()=>{live=false;};},[]);
  async function signOut() {
    await supabase().auth.signOut();
    location.replace("/login/");
  }
  return (
    <header className="topbar">
      <a href="/dashboard/" className="brandlink"><Mark size={26} /><b>WatchLog</b></a>
      <nav className="nav">
        {TABS.map(([label, href]) => <a key={href} href={href} className={"navlink" + (active === label ? " active" : "")}>{label}</a>)}
      </nav>
      <span className="spacer" />
      {platform&&<a href="/admin/" className="navlink hide-sm" style={{color:"var(--wl-ice)"}}>Platform</a>}
      {email && <span className="muted hide-sm" style={{ fontSize: "var(--font-size-xs)" }}>{email}</span>}
      {right}
      <button className="ghost small" onClick={signOut}>Sign out</button>
    </header>
  );
}

export const SETUP_STEPS = [
  ["awaiting_agent", "Waiting for the site PC"],
  ["enrolled", "Agent enrolled"],
  ["recorder_connected", "Recorder connected"],
  ["cameras_discovered", "Cameras discovered"],
  ["ready", "Reporting"],
];

export function setupPill(state, online) {
  const idx = SETUP_STEPS.findIndex(([k]) => k === state);
  const label = idx >= 0 ? SETUP_STEPS[idx][1] : "Unknown";
  if (state === "ready") return [label, online ? "s-ok" : "s-warn"];
  if (state === "awaiting_agent") return [label, "s-unk"];
  return [label, "s-warn"];
}

export async function requireTenant() {
  const sb = supabase();
  const { data: { session } } = await sb.auth.getSession();
  if (!session) { location.replace("/login/"); return null; }
  const { data: tenant, error } = await sb.rpc("wl_my_tenant");
  if (error || !tenant) { location.replace("/onboarding/"); return null; }
  return { session, tenant };
}
