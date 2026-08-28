"use client";

import { useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");

    const { error } = await supabase().auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (error) {
      setError(say(error));
      setBusy(false);
      return;
    }
    // Let the entry page decide between onboarding and dashboard, so the
    // routing rule lives in exactly one place.
    location.replace("/");
  }

  return (
    <div className="center">
      <form className="auth-card" onSubmit={onSubmit}>
        <div className="brand">
          <Mark />
          <span className="brand-name">WatchLog</span>
        </div>

        <h1>Sign in</h1>
        <p className="sub">See what your cameras saw.</p>

        {error && <div className="err">{error}</div>}

        <label htmlFor="email">Email</label>
        <input id="email" type="email" autoComplete="email" required
               value={email} onChange={(e) => setEmail(e.target.value)} />

        <label htmlFor="password">Password</label>
        <input id="password" type="password" autoComplete="current-password"
               required value={password}
               onChange={(e) => setPassword(e.target.value)} />

        <button type="submit" disabled={busy}>
          {busy ? "Signing in..." : "Sign in"}
        </button>

        <p className="alt">
          No account yet? <a href="/signup/">Create one</a>
        </p>
      </form>
    </div>
  );
}
