"use client";

import { useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";

function resetRedirect() {
  return `${window.location.origin}/auth/reset/`;
}

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");

    const { error } = await supabase().auth.resetPasswordForEmail(email.trim(), {
      redirectTo: resetRedirect(),
    });

    if (error) {
      setError(say(error));
      setBusy(false);
      return;
    }

    setSent(true);
    setBusy(false);
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
            <h1>Reset password</h1>
            <p className="wl-auth-lede">We will email you a secure link to choose a new password.</p>
          </div>

          {error && <div className="wl-auth-note bad" role="alert">{error}</div>}
          {sent && <div className="wl-auth-note ok" role="status">If an account exists for that email, a recovery link has been sent. Use the newest email only.</div>}

          <div className="wl-auth-fields">
            <label htmlFor="email">Email
              <input id="email" type="email" autoComplete="email" required
                     value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
          </div>

          <div className="wl-auth-actions">
            <button className="wl-auth-btn" type="submit" disabled={busy}>
              {busy ? "Sending..." : "Send recovery link"}
            </button>
          </div>

          <div className="wl-auth-foot"><a href="/login/">Back to sign in</a></div>
        </form>
      </main>
    </div>
  );
}
