"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import Mark from "../../mark";

function hasConfirmationPayload() {
  if (typeof window === "undefined") return false;
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const query = new URLSearchParams(window.location.search);
  return Boolean(hash.get("access_token") || hash.get("refresh_token") || hash.get("type") || query.get("code") || query.get("token_hash"));
}

function hashError() {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const code = params.get("error_code");
  const description = params.get("error_description");
  if (!code && !description) return "";
  if (code === "otp_expired") return "This confirmation link is invalid or has expired. Return to sign in and send a new confirmation email.";
  return description ? description.replaceAll("+", " ") : "This confirmation link could not be used.";
}

export default function ConfirmEmail() {
  const [error, setError] = useState("");
  const [waiting, setWaiting] = useState(true);

  useEffect(() => {
    const urlError = hashError();
    if (urlError) {
      setError(urlError);
      setWaiting(false);
      return;
    }

    if (!hasConfirmationPayload()) {
      setError("Open the confirmation link from your email to complete confirmation.");
      setWaiting(false);
      return;
    }

    const sb = supabase();
    let active = true;
    let timer;

    const finish = (session) => {
      if (!active || !session) return false;
      location.replace("/");
      return true;
    };

    const { data: listener } = sb.auth.onAuthStateChange((_event, session) => {
      if (finish(session) && timer) clearTimeout(timer);
    });

    sb.auth.getSession().then(({ data, error }) => {
      if (!active) return;
      if (error) {
        setError(say(error));
        setWaiting(false);
        return;
      }
      if (finish(data.session)) return;
      timer = setTimeout(() => {
        if (!active) return;
        setError("This confirmation link could not be verified. Return to sign in and send a new confirmation email.");
        setWaiting(false);
      }, 4000);
    });

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      listener.subscription.unsubscribe();
    };
  }, []);

  return (
    <div className="center">
      <div className="auth-card">
        <div className="brand"><Mark /><span className="brand-name">WatchLog</span></div>
        <h1>{waiting ? "Confirming email" : error ? "Confirmation link problem" : "Confirmation not completed"}</h1>
        <p className="sub">
          {waiting
            ? "Verifying your confirmation and returning you to WatchLog..."
            : error || "Use the confirmation link from your email to continue."}
        </p>
        {error && <div className="err">{error}</div>}
        {!waiting && <a href="/login/"><button type="button">Go to sign in</button></a>}
      </div>
    </div>
  );
}
