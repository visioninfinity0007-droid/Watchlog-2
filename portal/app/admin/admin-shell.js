"use client";

import Mark from "../mark";
import { supabase } from "../../lib/supabase";

const TABS = [
  ["Overview", "/admin/"],
  ["Customers", "/admin/tenants/"],
  ["Operations", "/admin/operations/"],
  ["Commercial", "/admin/billing/"],
  ["Support", "/admin/support/"],
  ["Audit", "/admin/audit/"],
  ["Admins", "/admin/admins/"],
];

export async function requirePlatformAdmin() {
  const sb = supabase();
  const { data: { session } } = await sb.auth.getSession();
  if (!session) { location.replace("/login/"); return null; }
  const { data, error } = await sb.rpc("wl_platform_me");
  if (error || !data?.role) { location.replace("/dashboard/"); return null; }
  return { session, admin: data };
}

export function canOperate(role) {
  return role === "platform_owner" || role === "platform_admin";
}

export function isOwner(role) {
  return role === "platform_owner";
}

export function roleLabel(role) {
  return ({
    platform_owner: "Platform owner",
    platform_admin: "Platform admin",
    platform_support: "Platform support",
  })[role] || "Platform user";
}

export function AdminNav({ active, admin }) {
  async function signOut() {
    await supabase().auth.signOut();
    location.replace("/login/");
  }
  return <header className="topbar">
    <a href="/admin/" className="brandlink">
      <Mark size={26} />
      <b>WatchLog</b>
      <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".08em", color: "var(--color-violet-bright)" }}>OPERATIONS</span>
    </a>
    <nav className="nav">
      {TABS.filter(([label]) => label !== "Admins" || admin?.role === "platform_owner").map(([label, href]) =>
        <a key={href} href={href} className={"navlink" + (active === label ? " active" : "")}>{label}</a>)}
    </nav>
    <span className="spacer" />
    {admin?.has_tenant && <a href="/dashboard/" className="navlink hide-sm">My customer account</a>}
    <span className="muted hide-sm" style={{ fontSize: "var(--font-size-xs)" }}>{admin?.email}</span>
    <button className="ghost small" onClick={signOut}>Sign out</button>
  </header>;
}
