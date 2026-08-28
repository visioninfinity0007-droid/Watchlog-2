"use client";

import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import Mark from "./mark";

// Entry point. Decides where a visitor belongs:
//   no session          -> /login
//   session, no tenant  -> /onboarding
//   session and tenant  -> /dashboard
//
// The check runs client-side because this is a static export. That is
// safe: it is routing, not authorisation. Someone who skips straight to
// /dashboard gets an empty page, because RLS returns them no rows.
export default function Home() {
  const [note, setNote] = useState("Checking your session...");

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const sb = supabase();
      const { data: { session } } = await sb.auth.getSession();

      if (cancelled) return;
      if (!session) {
        location.replace("/login/");
        return;
      }

      const { data, error } = await sb.rpc("wl_my_tenant");
      if (cancelled) return;

      if (error) {
        setNote("Could not reach WatchLog: " + error.message);
        return;
      }
      location.replace(data ? "/dashboard/" : "/onboarding/");
    })();

    return () => { cancelled = true; };
  }, []);

  return (
    <div className="center">
      <div style={{ textAlign: "center" }}>
        <div style={{ display: "inline-block", marginBottom: 16 }}>
          <Mark size={40} />
        </div>
        <p className="muted">{note}</p>
      </div>
    </div>
  );
}
