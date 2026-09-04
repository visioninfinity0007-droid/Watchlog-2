"use client";

import { useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";

function confirmRedirect() {
  return `${window.location.origin}/auth/confirm/`;
}

export default function SignUp() {
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");

    const sb = supabase();
    const { data, error } = await sb.auth.signUp({
      email: email.trim(),
      password,
      options: {
        data: { company: company.trim() },
        emailRedirectTo: confirmRedirect(),
      },
    });

    if (error) {
      setError(say(error));
      setBusy(false);
      return;
    }

    if (!data.session) {
      setConfirm(true);
      setBusy(false);
      return;
    }
    location.replace("/onboarding/");
  }

  async function resendConfirmation() {
    setResending(true);
    setError("");
    setNotice("");
    const { error } = await supabase().auth.resend({
      type: "signup",
      email: email.trim(),
      options: { emailRedirectTo: confirmRedirect() },
    });
    if (error) setError(say(error));
    else setNotice("A new confirmation link has been sent. Use the newest email only.");
    setResending(false);
  }

  if (confirm) {
    return (
      <div className="center">
        <div className="auth-card">
          <div className="brand"><Mark /><span className="brand-name">WatchLog</span></div>
          <h1>Check your email</h1>
          <p className="sub">
            We sent a confirmation link to <b>{email}</b>. Open the newest link,
            then WatchLog will bring you back to the portal.
          </p>
          {error && <div className="err">{error}</div>}
          {notice && <div className="ok-note">{notice}</div>}
          <button type="button" onClick={resendConfirmation} disabled={resending}>
            {resending ? "Sending..." : "Resend confirmation"}
          </button>
          <p className="alt"><a href="/login/">Go to sign in</a></p>
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
          Works with compatible CCTV recorders already installed at your site.
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
