# WatchLog — full product demo (15–25 min)

Run this start-to-finish from an **incognito browser**. It traverses the whole product
— marketing → signup → onboarding → agent/AI → portal → reports → team → trial → billing —
using production code plus the clearly-labelled demo/sandbox fixtures. Every step lists the
**expected visible behavior** and a **fallback** for the parts that need a real external
integration (recorder hardware, a live WhatsApp send, the live Switch gateway).

## Environment (demo/staging)
- Marketing: `https://watchlogsite.161.97.175.15.sslip.io`
- Portal: `https://watchlog.161.97.175.15.sslip.io`
- Billing sandbox: `https://watchlog-billing.161.97.175.15.sslip.io` (mock provider, labelled SANDBOX)
- Report runner: `https://watchlog-report.161.97.175.15.sslip.io` (dry-run)
- **Demo login** (populated tenant): `demo@watchlog.test` / password in `projects/watchlog/.env`
  (`PORTAL_DEMO_PASSWORD`). This tenant is "WatchLog Demo (sample data)" — data is synthetic.

Re-seed the demo tenant before a demo: `python tools/seed_demo.py`.

> The demo has two short tracks: **A. a fresh signup** (shows the real onboarding journey and
> the live setup stepper), then **B. the pre-populated demo login** (shows a running account:
> incidents, reports, team, billing). Use A to prove "self-serve from zero", B to show "what it
> looks like in production". Total ~20 minutes.

---

## Track A — from the website to a new account (~8 min)

1. **Marketing site.** Open the marketing URL.
   *Expected:* the WatchLog homepage — "your cameras already see everything…", the
   "you might already have…" ledger, supported recorders (Hikvision/Dahua/ONVIF), a **Start free**
   button. Legal pages (privacy/terms) in the footer.

2. **Start free → Sign up.** Click **Start free** (or /signup).
   *Expected:* the signup form (company, work email, password). Create an account with a fresh
   email. *Fallback:* if email confirmation is enabled and blocks first login, use the demo login
   (Track B) for the rest — note that confirmation is a one-click email step in production.

3. **Onboarding — first site.** After sign-in you land on onboarding.
   *Expected:* "Set up your first site" (company + site name) → Continue → the **Connect your
   recorder** screen with a **one-time enrollment code**, the requirements banner (Windows PC on
   the same network, recorder password stays on site), and a **Live setup progress** stepper:
   *Waiting for the site PC → Agent enrolled → Recorder connected → Cameras discovered →
   Reporting.* It says it updates on its own.

4. **Download + install the agent.**
   *Expected (when the installer is published):* the **Download for Windows** button serves
   `WatchLog-Setup.exe`; the installer runs a wizard, asks for the recorder login **on the PC**
   (never in the browser) and the enrollment code, tests the connection, discovers cameras, and
   starts the agent as a background service.
   *Fallback (no client hardware here):* show the AI + agent are real without a recorder —
   `python tools/verify_agent_ai.py` (or run the built `watchlog-agent.exe --selftest`): it loads
   the ONNX model, runs inference, and **discards a junk frame as a false alarm** (RESULT: PASS).
   That is the on-site AI decision the product is sold on. Then continue on the demo login (Track
   B), where the stepper has already reached **Reporting**.

---

## Track B — a running account (the demo login) (~12 min)

Sign in as `demo@watchlog.test`.

5. **Overview / dashboard.**
   *Expected:* tenant "WatchLog Demo (sample data)", KPI tiles (events, cameras, agents online,
   sites), the **Sites & agents** table with the demo agent **online**, a "Recent incidents" strip
   with camera stills, event-type bars, latest events. Nothing needs attention (or a small,
   explained list).

6. **Incidents.** Open **Incidents**.
   *Expected:* a filterable history (window / site / type) — ~27 incidents across Main Gate /
   Loading Bay / Rear Perimeter / Reception, most with a still thumbnail. Filter to **person**,
   then **vehicle**; the list updates. Click a still to see the frame.
   *Talking point:* these are the events the on-site AI **kept** — rain, headlights and animals
   were discarded at the site and never reached this list.

7. **Health.** Point out the agent status (online/stale/offline) and any faults (tamper /
   video-loss) surfaced separately from incidents — equipment problems, not intruders.

8. **Reports & recipients.** Open **Reports**.
   *Expected:* add a recipient (WhatsApp / Email / both), a recipients list you can pause/resume/
   remove, and a **Delivery history** table.
   *Prove the pipeline (dry-run):* the daily report runs on a schedule (n8n → the report runner).
   To show it live without messaging anyone:
   `curl -X POST https://watchlog-report.161.97.175.15.sslip.io/run/$REPORT_RUNNER_TOKEN`
   → returns `mode: dry-run`, a per-site summary ("N sent, M skipped").
   *Fallback (real send is CLIENT-BLOCKED):* an actual WhatsApp/email needs the customer's
   go-ahead + provider credentials; flip `REPORT_SEND=true` on the runner and add the SendGrid key
   to deliver for real.

9. **Team.** Open **Team**.
   *Expected:* the member list with roles; invite a colleague (viewer/admin) → an invitation link
   is generated; the pending-invitations list with revoke. (Owners can change roles.)

10. **Trial & plan.** Open **Settings**.
    *Expected:* Plan & billing shows the tenant on **trial** with days remaining, and a
    **"Daily reports active / paused"** pill that reflects entitlement (reason shown, plus the
    reassurance *"your recorded events are kept"*). The plan picker shows the **published** tiers —
    **Starter PKR 6,000/mo**, **Growth PKR 12,000/mo**, and **Enterprise — Talk to us** (contact-only,
    no self-checkout). These are the same numbers as the marketing site; `test_pricing_alignment`
    fails CI if they ever diverge. The sites table shows each site's **setup status**; enrollment
    codes can be re-issued; the agent-install card.
    *Entitlement note:* if a trial lapses (or a subscription is cancelled), the daily-report job
    **pauses** for that tenant and the pill flips to **paused** — but **no events or snapshots are
    deleted**; paying resumes reporting. (`wl_reporting_enabled` is the one authority the reporter and
    the portal both read; `test_entitlement` covers all six states.)

11. **Billing checkout (sandbox).** In Settings → Plan & billing, pick **Growth (PKR 12,000/mo)**.
    *Expected:* you are taken to the hosted checkout at the billing service, clearly banded
    **SANDBOX — TEST PAYMENT**. Click **Pay (test)**. You return to Settings and the plan shows
    **active**, with a renewal date and a **Payment history** row; the reports pill shows **active**.
    *Prove the guardrail:* a customer can request a plan but cannot mark themselves paid — the
    activation happened only via the signed provider webhook (the portal has no way to set paid
    state; `test_billing_authz` + the E2E harness prove `apply_event`/`set_subscription` are denied
    to customers).
    *Fallback (real Switch is CLIENT-BLOCKED):* production uses the same flow with `provider=switch`;
    that adapter needs Switch's API/signing contract. Everything up to the provider handshake is
    complete and E2E-tested with the sandbox.

12. **Security boundary (optional, for a technical audience).**
    Run `python prototype/tests/test_tenant_isolation.py` → **9/9**: anon sees nothing, one tenant
    cannot read another's cameras/events/stills, destructive functions are denied. Or the full
    `python prototype/tests/e2e_harness.py` → 14/14 against a throwaway tenant.

13. **Ongoing operation.** Note the agent restarts on boot and after crashes, buffers events
    through an internet outage, and reports every morning — then **Sign out**.

---

## Reset between demos
- `python tools/seed_demo.py` — re-populates the demo tenant and clears any sandbox subscription.
- The demo tenant is isolated; nothing here affects AKSS or any real customer.

## What is real vs fixture in this demo
| Real production code | Demo/sandbox fixture |
|---|---|
| Portal, RLS/tenant isolation, agent, ONNX filter, reporting engine, billing schema + authorization, NSIS installer source | Sample incidents/snapshots (synthetic, tagged), the **mock** payment provider (sandbox), report **dry-run** instead of a live send |

## Client-blocked for a *production* (not demo) acceptance
Real recorder hardware (2×10 cameras, field validation) · a production domain · SendGrid key ·
authorized live WhatsApp send · Switch merchant credentials + API contract · code-signing
certificate · GitHub Actions minutes. See `docs/production/CLIENT_DEPENDENCIES.md`.
