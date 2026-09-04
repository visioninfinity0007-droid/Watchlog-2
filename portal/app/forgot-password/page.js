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
    <div className="center">
      <form className="auth-card" onSubmit={onSubmit}>
        <div className="brand">
          <Mark />
          <span className="brand-name">WatchLog</span>
        </div>

        <h1>Reset password</h1>
        <p className="sub">We will email you a secure link to choose a new password.</p>

        {error && <div className="err">{error}</div>}
        {sent && <div className="ok-note">If an account exists for that email, a recovery link has been sent. Use the newest email only.</div>}

        <label htmlFor="email">Email</label>
        <input id="email" type="email" autoComplete="email" required
               value={email} onChange={(e) => setEmail(e.target.value)} />

        <button type="submit" disabled={busy}>
          {busy ? "Sending..." : "Send recovery link"}
        </button>

        <p className="alt"><a href="/login/">Back to sign in</a></p>
      </form>
    </div>
  );
}
