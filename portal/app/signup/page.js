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
      <div className="wl-auth">
      <header className="wl-auth-brand">
        <a className="wl-auth-logo" href="/"><Mark size={20} /><span>WatchLog</span></a>
        <div className="wl-auth-pitch">
          <p>Know what happened at your business without watching hours of CCTV.</p>
          <ul><li>What happened</li><li>What needs attention</li><li>What WatchLog can verify</li></ul>
        </div>
      </header>
        <main className="wl-auth-main">
          <div className="wl-auth-panel">
            <div className="wl-auth-head">
              <h1>Check your email</h1>
              <p className="wl-auth-lede">
                We sent a confirmation link to <b>{email}</b>. Open the newest link,
                then WatchLog will bring you back to the portal.
              </p>
            </div>
            {error && <div className="wl-auth-note bad" role="alert">{error}</div>}
            {notice && <div className="wl-auth-note ok" role="status">{notice}</div>}
            <button className="wl-auth-btn quiet" type="button" onClick={resendConfirmation} disabled={resending}>
              {resending ? "Sending..." : "Resend confirmation"}
            </button>
            <div className="wl-auth-foot"><a href="/login/">Go to sign in</a></div>
          </div>
        </main>
      </div>
    );
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
            <h1>Create your account</h1>
            <p className="wl-auth-lede">
              Use compatible CCTV already installed at your site to get useful business and security information.
            </p>
          </div>

          {error && <div className="wl-auth-note bad" role="alert">{error}</div>}

          <div className="wl-auth-fields">
            <label htmlFor="company">Company name
              <input id="company" required value={company}
                     onChange={(e) => setCompany(e.target.value)} />
            </label>
            <label htmlFor="email">Work email
              <input id="email" type="email" autoComplete="email" required
                     value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label htmlFor="password">Password
              <input id="password" type="password" autoComplete="new-password"
                     required minLength={6} value={password}
                     onChange={(e) => setPassword(e.target.value)} />
            </label>
          </div>

          <div className="wl-auth-actions">
            <button className="wl-auth-btn" type="submit" disabled={busy}>
              {busy ? "Creating account..." : "Create account"}
            </button>
          </div>

          <div className="wl-auth-foot">
            <p>Already have an account? <a href="/login/">Sign in</a></p>
          </div>
        </form>
      </main>
    </div>
  );
}
