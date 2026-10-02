"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import Mark from "../../mark";

function hashError() {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const code = params.get("error_code");
  const description = params.get("error_description");
  if (!code && !description) return "";
  if (code === "otp_expired") return "This recovery link is invalid or has expired. Request a new password reset email.";
  return description ? description.replaceAll("+", " ") : "This recovery link could not be used.";
}

export default function ResetPassword() {
  const [ready, setReady] = useState(false);
  const [checking, setChecking] = useState(true);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const urlError = hashError();
    if (urlError) {
      setError(urlError);
      setChecking(false);
      return;
    }

    const sb = supabase();
    let active = true;
    let timer;

    const accept = (event, session) => {
      if (!active) return;
      if (event === "PASSWORD_RECOVERY" || session) {
        setReady(true);
        setChecking(false);
        if (timer) clearTimeout(timer);
      }
    };

    const { data: listener } = sb.auth.onAuthStateChange((event, session) => accept(event, session));

    sb.auth.getSession().then(({ data, error }) => {
      if (!active) return;
      if (error) {
        setError(say(error));
        setChecking(false);
        return;
      }
      if (data.session) {
        setReady(true);
        setChecking(false);
        return;
      }
      timer = setTimeout(() => {
        if (!active) return;
        setChecking(false);
        setError("Recovery session not available. Request a new password reset email and use the newest link.");
      }, 4000);
    });

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      listener.subscription.unsubscribe();
    };
  }, []);

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setBusy(true);
    const { error } = await supabase().auth.updateUser({ password });
    if (error) {
      setError(say(error));
      setBusy(false);
      return;
    }

    await supabase().auth.signOut();
    location.replace("/login/");
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
        <form className="wl-auth-panel" onSubmit={onSubmit} aria-busy={busy || checking}>
          <div className="wl-auth-head">
            <h1>Choose a new password</h1>
            <p className="wl-auth-lede" role="status">
              {checking ? "Verifying your recovery link..." : ready ? "Set a new password for your WatchLog account." : "Request a fresh recovery email to continue."}
            </p>
          </div>

          {error && <div className="wl-auth-note bad" role="alert">{error}</div>}

          {ready && (
            <>
              <div className="wl-auth-fields">
                <label htmlFor="password">New password
                  <input id="password" type="password" autoComplete="new-password" required minLength={6}
                         value={password} onChange={(e) => setPassword(e.target.value)} />
                </label>
                <label htmlFor="confirm-password">Confirm new password
                  <input id="confirm-password" type="password" autoComplete="new-password" required minLength={6}
                         value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} />
                </label>
              </div>
              <div className="wl-auth-actions">
                <button className="wl-auth-btn" type="submit" disabled={busy}>{busy ? "Updating..." : "Update password"}</button>
              </div>
            </>
          )}

          <div className="wl-auth-foot">
            {!ready && !checking && <a href="/forgot-password/">Send a new recovery link</a>}
            <a href="/login/">Back to sign in</a>
          </div>
        </form>
      </main>
    </div>
  );
}
