"use client";

import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import Mark from "./mark";

// Entry point. Decides where a visitor belongs:
//   no session               -> /login
//   platform administrator   -> /admin
//   session, no tenant       -> /onboarding
//   session and tenant       -> /dashboard
//
// This client-side routing is convenience only. Authorization is enforced by
// the RPCs/RLS behind each destination.
export default function Home() {
  const [note, setNote] = useState("Checking your session...");

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const sb = supabase();
      const { data: { session } } = await sb.auth.getSession();
      if (cancelled) return;
      if (!session) { location.replace("/login/"); return; }

      // Platform role is intentionally separate from tenant membership.
      const { data: platform, error: platformError } = await sb.rpc("wl_platform_me");
      if (cancelled) return;
      if (!platformError && platform?.role) { location.replace("/admin/"); return; }

      const { data: tenant, error } = await sb.rpc("wl_my_tenant");
      if (cancelled) return;
      if (error) { setNote("Could not reach WatchLog: " + error.message); return; }
      location.replace(tenant ? "/dashboard/" : "/onboarding/");
    })();

    return () => { cancelled = true; };
  }, []);

  return (
    <div className="center">
      <div style={{ textAlign: "center" }}>
        <div style={{ display: "inline-block", marginBottom: 16 }}><Mark size={40} /></div>
        <p className="muted">{note}</p>
      </div>
    </div>
  );
}
