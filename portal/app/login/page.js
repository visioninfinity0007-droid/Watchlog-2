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
    <div className="center">
      <form className="auth-card" onSubmit={onSubmit}>
        <div className="brand">
          <Mark />
          <span className="brand-name">WatchLog</span>
        </div>

        <h1>Sign in</h1>
        <p className="sub">See what your cameras saw.</p>

        {error && <div className="err">{error}</div>}
        {notice && <div className="ok-note">{notice}</div>}

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

        <p className="alt"><a href="/forgot-password/">Forgot password?</a></p>
        <button className="ghost" type="button" onClick={resendConfirmation} disabled={resending}>
          {resending ? "Sending..." : "Resend confirmation email"}
        </button>

        <p className="alt">
          No account yet? <a href="/signup/">Create one</a>
        </p>
      </form>
    </div>
  );
}
