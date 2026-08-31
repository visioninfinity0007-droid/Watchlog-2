"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";

export default function Invite() {
  const [state, setState] = useState("working"); // working | ok | error | signin
  const [msg, setMsg] = useState("");

  useEffect(() => {
    (async () => {
      const token = new URLSearchParams(location.search).get("token");
      if (!token) { setState("error"); setMsg("This invitation link is missing its token."); return; }
      const sb = supabase();
      const { data: { session } } = await sb.auth.getSession();
      if (!session) {
        // Remember the invite so the router can resume it after sign-in.
        try { sessionStorage.setItem("wl_invite", token); } catch { /* ignore */ }
        setState("signin");
        return;
      }
      const { data, error } = await sb.rpc("wl_accept_invite", { p_token: token });
      if (error) { setState("error"); setMsg(say(error)); return; }
      if (data && data.ok === false) { setState("error"); setMsg(data.note || "That invitation is not valid."); return; }
      try { sessionStorage.removeItem("wl_invite"); } catch { /* ignore */ }
      setState("ok"); setMsg("You have joined the team.");
    })();
  }, []);

  return (
    <div className="center">
      <div className="auth-card">
        <div className="brand"><span className="brand-mark"><Mark size={30} /></span>
          <span className="brand-name">WatchLog</span></div>
        {state === "working" && <p className="muted">Checking your invitation…</p>}
        {state === "signin" && (
          <>
            <h1>Accept your invitation</h1>
            <p className="sub">Sign in (or create your account) with the email address the invitation
              was sent to, then reopen the invitation link.</p>
            <a href="/login/"><button>Sign in</button></a>
            <p className="alt">New here? <a href="/signup/">Create an account</a> with the invited email.</p>
          </>
        )}
        {state === "ok" && (
          <>
            <h1>You are in</h1>
            <div className="ok-note">{msg}</div>
            <a href="/dashboard/"><button>Go to the dashboard</button></a>
          </>
        )}
        {state === "error" && (
          <>
            <h1>That did not work</h1>
            <div className="err">{msg}</div>
            <a href="/dashboard/"><button className="ghost" style={{ width: "100%" }}>Go to the dashboard</button></a>
          </>
        )}
      </div>
    </div>
  );
}
