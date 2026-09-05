"use client";

import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import Mark from "./mark";

async function accountState(sb) {
  const { data, error } = await sb.rpc("wl_my_account");
  if (!error) return data;
  if (/wl_my_account|schema cache|function/i.test(error.message || "")) return undefined;
  throw error;
}

// Entry point. Authorization remains server-enforced by the RPC/RLS layer.
export default function Home() {
  const [note, setNote] = useState("Checking your account...");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const sb = supabase();
      const { data: { session } } = await sb.auth.getSession();
      if (cancelled) return;
      if (!session) { location.replace("/login/"); return; }

      const { data: platform, error: platformError } = await sb.rpc("wl_platform_me");
      if (cancelled) return;
      if (!platformError && platform?.role) { location.replace("/admin/"); return; }

      try {
        const account = await accountState(sb);
        if (cancelled) return;
        if (account?.account_status === "suspended") { location.replace("/account-suspended/"); return; }
        if (account === null) { location.replace("/onboarding/"); return; }
      } catch {
        setNote("WatchLog could not verify your account. Please try again.");
        return;
      }

      const { data: tenant, error } = await sb.rpc("wl_my_tenant");
      if (cancelled) return;
      if (error) { setNote("WatchLog could not verify your account. Please try again."); return; }
      location.replace(tenant ? "/dashboard/" : "/onboarding/");
    })();
    return () => { cancelled = true; };
  }, []);

  return <div className="center"><div style={{textAlign:"center"}}><div style={{display:"inline-block",marginBottom:16}}><Mark size={40}/></div><p className="muted">{note}</p></div></div>;
}
