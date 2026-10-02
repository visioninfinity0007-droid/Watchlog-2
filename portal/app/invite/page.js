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
    <div className="wl-auth">
      <header className="wl-auth-brand">
        <a className="wl-auth-logo" href="/"><Mark size={20} /><span>WatchLog</span></a>
        <div className="wl-auth-pitch">
          <p>Know what happened at your business without watching hours of CCTV.</p>
          <ul><li>What happened</li><li>What needs attention</li><li>What WatchLog can verify</li></ul>
        </div>
      </header>
      <main className="wl-auth-main">
        <div className="wl-auth-panel" aria-busy={state === "working"}>
          {state === "working" && <p className="wl-auth-status" role="status">Checking your invitation…</p>}
          {state === "signin" && (
            <>
              <div className="wl-auth-head">
                <div className="wl-auth-kicker">Team invitation</div>
                <h1>Accept your invitation</h1>
                <p className="wl-auth-lede">Sign in (or create your account) with the email address the invitation
                  was sent to, then reopen the invitation link.</p>
              </div>
              <div className="wl-auth-actions"><a className="wl-auth-btn" href="/login/">Sign in</a></div>
              <div className="wl-auth-foot"><p>New here? <a href="/signup/">Create an account</a> with the invited email.</p></div>
            </>
          )}
          {state === "ok" && (
            <>
              <div className="wl-auth-head">
                <div className="wl-auth-kicker">Team invitation</div>
                <h1>You are in</h1>
              </div>
              <div className="wl-auth-note ok" role="status">{msg}</div>
              <div className="wl-auth-actions"><a className="wl-auth-btn" href="/home/">Open WatchLog</a></div>
            </>
          )}
          {state === "error" && (
            <>
              <div className="wl-auth-head">
                <div className="wl-auth-kicker">Team invitation</div>
                <h1>That did not work</h1>
              </div>
              <div className="wl-auth-note bad" role="alert">{msg}</div>
              <div className="wl-auth-actions"><a className="wl-auth-btn quiet" href="/home/">Go to WatchLog Home</a></div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
