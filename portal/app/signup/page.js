"use client";

import { useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";

export default function SignUp() {
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");

    const sb = supabase();
    const { data, error } = await sb.auth.signUp({
      email: email.trim(),
      password,
      // Carried through so onboarding can name the tenant without asking
      // twice, whether the account is confirmed now or by email later.
      options: { data: { company: company.trim() } },
    });

    if (error) {
      setError(say(error));
      setBusy(false);
      return;
    }

    // With email confirmation switched on, signUp returns a user but no
    // session. Say so plainly rather than dropping them on a page that
    // silently does nothing.
    if (!data.session) {
      setConfirm(true);
      setBusy(false);
      return;
    }
    location.replace("/onboarding/");
  }

  if (confirm) {
    return (
      <div className="center">
        <div className="auth-card">
          <div className="brand"><Mark /><span className="brand-name">WatchLog</span></div>
          <h1>Check your email</h1>
          <p className="sub">
            We sent a confirmation link to <b>{email}</b>. Open it, then sign
            in and we will get your first site connected.
          </p>
          <a href="/login/"><button type="button">Go to sign in</button></a>
        </div>
      </div>
    );
  }

  return (
    <div className="center">
      <form className="auth-card" onSubmit={onSubmit}>
        <div className="brand">
          <Mark />
          <span className="brand-name">WatchLog</span>
        </div>

        <h1>Create your account</h1>
        <p className="sub">
          Works with the Hikvision and Dahua recorders you already own.
        </p>

        {error && <div className="err">{error}</div>}

        <label htmlFor="company">Company name</label>
        <input id="company" required value={company}
               onChange={(e) => setCompany(e.target.value)} />

        <label htmlFor="email">Work email</label>
        <input id="email" type="email" autoComplete="email" required
               value={email} onChange={(e) => setEmail(e.target.value)} />

        <label htmlFor="password">Password</label>
        <input id="password" type="password" autoComplete="new-password"
               required minLength={6} value={password}
               onChange={(e) => setPassword(e.target.value)} />

        <button type="submit" disabled={busy}>
          {busy ? "Creating account..." : "Create account"}
        </button>

        <p className="alt">
          Already have an account? <a href="/login/">Sign in</a>
        </p>
      </form>
    </div>
  );
}
