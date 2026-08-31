"use client";

import { useEffect, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import Mark from "../mark";

// The moment that decides whether a customer succeeds or refunds.
//
// It states the requirements BEFORE handing over the code, because the
// single most expensive discovery is "I signed up and then found out I
// need a PC at the site". Everything learned in the field is here: same
// network as the recorder, the recorder's own password, and the fact
// that a Windows 11 machine with Smart App Control will refuse to run an
// unsigned build.
export default function Onboarding() {
  const [stage, setStage] = useState("loading");
  const [company, setCompany] = useState("");
  const [site, setSite] = useState("Main site");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [code, setCode] = useState("");

  useEffect(() => {
    (async () => {
      const sb = supabase();
      const { data: { session } } = await sb.auth.getSession();
      if (!session) { location.replace("/login/"); return; }

      const { data: tenant } = await sb.rpc("wl_my_tenant");
      if (tenant) { location.replace("/dashboard/"); return; }

      setCompany(session.user?.user_metadata?.company || "");
      setStage("form");
    })();
  }, []);

  async function createTenant(e) {
    e.preventDefault();
    setBusy(true);
    setError("");

    const { data, error } = await supabase().rpc("wl_bootstrap_tenant", {
      p_company: company,
      p_site_name: site,
      p_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Karachi",
    });

    if (error) { setError(say(error)); setBusy(false); return; }
    setCode(data.enrollment_code || "");
    setStage("connect");
    setBusy(false);
  }

  if (stage === "loading") {
    return <div className="center"><p className="muted">Loading...</p></div>;
  }

  if (stage === "form") {
    return (
      <div className="center">
        <form className="auth-card" onSubmit={createTenant}>
          <div className="brand"><Mark /><span className="brand-name">WatchLog</span></div>
          <h1>Set up your first site</h1>
          <p className="sub">A site is one location with one recorder.</p>

          {error && <div className="err">{error}</div>}

          <label htmlFor="company">Company</label>
          <input id="company" required value={company}
                 onChange={(e) => setCompany(e.target.value)} />

          <label htmlFor="site">Site name</label>
          <input id="site" required value={site}
                 onChange={(e) => setSite(e.target.value)}
                 placeholder="Head office, Warehouse, Plant..." />

          <button type="submit" disabled={busy}>
            {busy ? "Creating..." : "Continue"}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="center">
      <div className="auth-card" style={{ maxWidth: 560 }}>
        <div className="brand"><Mark /><span className="brand-name">WatchLog</span></div>
        <h1>Connect your recorder</h1>
        <p className="sub">About ten minutes, on a PC at the site.</p>

        <div className="banner" style={{ marginBottom: 24 }}>
          <b style={{ fontSize: "var(--font-size-sm)" }}>Before you start, you need</b>
          <ul>
            <li>A Windows PC at the site, left switched on, on the
                <b> same network</b> as the recorder</li>
            <li>The recorder&apos;s own username and password</li>
          </ul>
          <p style={{ fontSize: "var(--font-size-sm)", margin: "10px 0 0" }}
             className="muted">
            The recorder&apos;s password stays on that PC. It is never sent to us.
          </p>
        </div>

        <ol className="steps">
          <li>
            <b>Download the agent</b>
            <p>A single program. Nothing to install.</p>
            <p style={{ marginTop: 8 }}>
              {process.env.NEXT_PUBLIC_INSTALLER_URL ? (
                <a href={process.env.NEXT_PUBLIC_INSTALLER_URL}>
                  <button className="small" type="button" style={{ width: "auto" }}>
                    Download for Windows
                  </button>
                </a>
              ) : (
                <span className="muted" style={{ fontSize: "var(--font-size-sm)" }}>
                  The signed installer download appears here once published; until then your setup
                  engineer provides <span className="mono">WatchLog-Setup.exe</span>.
                </span>
              )}
            </p>
          </li>
          <li>
            <b>Run it and enter this code</b>
            <p>It finds your recorder on the network by itself.</p>
            <div className="code-box">{code || "—"}</div>
            <p>Single use, valid 14 days.</p>
          </li>
          <li>
            <b>Watch it appear</b>
            <p>Your dashboard updates within a minute of the agent starting.</p>
          </li>
        </ol>

        <p style={{ marginTop: 24 }}>
          <a href="/dashboard/"><button type="button">Go to dashboard</button></a>
        </p>
      </div>
    </div>
  );
}
