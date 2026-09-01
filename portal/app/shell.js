"use client";

import Mark from "./mark";
import { supabase } from "../lib/supabase";

const TABS = [
  ["Overview", "/dashboard/"],
  ["Incidents", "/incidents/"],
  ["Reports", "/reports/"],
  ["Team", "/team/"],
  ["Settings", "/settings/"],
];

/** The shared top bar + primary navigation. Used on every signed-in page so
 *  the portal reads as one product, not a set of disconnected screens. */
export function Nav({ active, email, right }) {
  async function signOut() {
    await supabase().auth.signOut();
    location.replace("/login/");
  }
  return (
    <header className="topbar">
      <a href="/dashboard/" className="brandlink">
        <Mark size={26} />
        <b>WatchLog</b>
      </a>
      <nav className="nav">
        {TABS.map(([label, href]) => (
          <a key={href} href={href}
             className={"navlink" + (active === label ? " active" : "")}>
            {label}
          </a>
        ))}
      </nav>
      <span className="spacer" />
      {email && (
        <span className="muted hide-sm" style={{ fontSize: "var(--font-size-xs)" }}>
          {email}
        </span>
      )}
      {right}
      <button className="ghost small" onClick={signOut}>Sign out</button>
    </header>
  );
}

// The ordered agent setup states, derived server-side in wl_sites() from
// what the agent reports to the cloud (enrollment, heartbeat, cameras,
// events). Used by onboarding (a live stepper) and Settings (a status pill).
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

/** Page guard: ensures a session and a tenant, or redirects. Returns
 *  { session, tenant } (tenant is the uuid) or null after redirecting. */
export async function requireTenant() {
  const sb = supabase();
  const { data: { session } } = await sb.auth.getSession();
  if (!session) { location.replace("/login/"); return null; }
  const { data: tenant, error } = await sb.rpc("wl_my_tenant");
  if (error || !tenant) { location.replace("/onboarding/"); return null; }
  return { session, tenant };
}
