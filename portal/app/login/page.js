"use client";

import { useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";

function confirmRedirect() {
  return `${window.location.origin}/auth/confirm/`;
}

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");

    const { error } = await supabase().auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (error) {
      setError(say(error));
      setBusy(false);
      return;
    }
    location.replace("/");
  }

  async function resendConfirmation() {
    if (!email.trim()) {
      setError("Enter your email first.");
      return;
    }
    setResending(true);
    setError("");
    setNotice("");
    const { error } = await supabase().auth.resend({
      type: "signup",
      email: email.trim(),
      options: { emailRedirectTo: confirmRedirect() },
    });
    if (error) setError(say(error));
    else setNotice("If this account is awaiting confirmation, a new link has been sent. Use the newest email only.");
    setResending(false);
  }

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
        <form className="wl-auth-panel" onSubmit={onSubmit} aria-busy={busy}>
          <div className="wl-auth-head">
            <h1>Sign in</h1>
            <p className="wl-auth-lede">See what happened, what needs attention, and what WatchLog can verify.</p>
          </div>

          {error && <div className="wl-auth-note bad" role="alert">{error}</div>}
          {notice && <div className="wl-auth-note ok" role="status">{notice}</div>}

          <div className="wl-auth-fields">
            <label htmlFor="email">Email
              <input id="email" type="email" autoComplete="email" required
                     value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label htmlFor="password">Password
              <input id="password" type="password" autoComplete="current-password"
                     required value={password}
                     onChange={(e) => setPassword(e.target.value)} />
            </label>
          </div>

          <div className="wl-auth-actions">
            <button className="wl-auth-btn" type="submit" disabled={busy}>
              {busy ? "Signing in..." : "Sign in"}
            </button>
          </div>

          <div className="wl-auth-foot">
            <a href="/forgot-password/">Forgot password?</a>
            <button className="wl-auth-linkbtn" type="button" onClick={resendConfirmation} disabled={resending}>
              {resending ? "Sending..." : "Resend confirmation email"}
            </button>
            <p>No account yet? <a href="/signup/">Create one</a></p>
          </div>
        </form>
      </main>
    </div>
  );
}
