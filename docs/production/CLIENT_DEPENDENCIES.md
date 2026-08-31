# WatchLog — Client Dependencies

Items that **cannot** be completed by Vision Infinity alone. For each: what is blocked, why, the
exact acceptance procedure once the input arrives, and what has been built around it so no other
work is stalled.

Status: 🔵 = waiting on client. When the input arrives, run the acceptance procedure and flip the
matching gate in `PRODUCTION_GATES.md`.

---

## 1. 🔵 Real-hardware M1 field validation
- **Blocked:** M1 field gate (2 sites × 10 real cameras, stable soak, FP/FN tuning).
- **Needs from client:** (a) `C:\ProgramData\WatchLog\agent.log` + Task-Scheduler screenshot from
  **SM-HP** (to fix the 3-min stop + 0-camera sync on the real DH-XVR1B08-I); (b) two live sites with
  10 cameras each and NVR admin access; (c) permission to collect a labelled footage sample.
- **Built around it:** agent is code-complete; real-Dahua `list_channels` fix + simulator kept in sync;
  a controlled soak checklist in `docs/production/M1_FIELD_VALIDATION.md` (created in P3).
- **Acceptance when unblocked:** install → enroll → recorder found → cameras synced → >1 h stable soak →
  events+snapshots+filtered sync → portal shows them → agent survives restart; then 2×10 across two sites.

## 2. 🔵 Switch merchant credentials
- **Blocked:** live Switch payment (BILL-6).
- **Needs from client:** Switch merchant/sandbox API credentials + the exact Switch API/webhook contract
  (or confirmation of the doc set to follow).
- **Built around it:** full billing DB model, provider abstraction, webhook endpoint + signature
  verification, idempotency, portal billing UI, and a deterministic **sandbox fixture** for testing
  (P8). Everything except the live-provider handshake is done and tested.
- **Acceptance when unblocked:** sandbox checkout → signed webhook → subscription `active`; replay →
  single effect; owner cannot forge paid state.

## 3. 🔵 Production domain
- **Blocked:** DOM-1 (final domain), TLS on real domain, canonical/OG/Auth-redirect/installer URLs.
- **Needs from client:** the chosen domain (e.g. `watchlog.pk`) + DNS control (or delegation).
- **Built around it:** every URL is made **config-driven** (P11/P12); a cutover checklist + a repo/runtime
  grep gate for `sslip.io`/`161.97.175.15`; sslip stays only as the **demo** environment.
- **Acceptance when unblocked:** run the cutover checklist; grep gate returns 0 stale URLs in prod build.

## 4. 🔵 SendGrid account + domain authentication
- **Blocked:** live branded-HTML email delivery.
- **Needs from client:** SendGrid API key + a verified sender / DKIM-authenticated domain.
- **Built around it:** branded responsive HTML template + plain-text fallback + render tests + runtime
  config by env-name (P7). Sends `skipped` (logged) until a key exists.
- **Acceptance when unblocked:** one authorized test email delivered; `report_deliveries.status='sent'`.

## 5. 🔵 Live WhatsApp send authorization
- **Blocked:** REP-2 live send to a real recipient.
- **Needs from client:** go-ahead + a designated **test** destination number.
- **Built around it:** full n8n schedule → report → Evolution → delivery-log pipeline; runs to the test
  destination only until authorized (P4).
- **Acceptance when unblocked:** scheduled run delivers to the test number; `report_deliveries.status='sent'`.

## 6. 🔵 Windows code-signing certificate
- **Blocked:** INS-3 (signed installer; no SmartScreen warning).
- **Needs from client:** an OV/EV code-signing certificate (or budget approval to buy one).
- **Built around it:** NSIS build with a `signtool` step gated on cert presence; unsigned build works for
  testing with documented SmartScreen behavior (P9).
- **Acceptance when unblocked:** signed `WatchLog-Setup.exe`; signature validates; no SmartScreen block.

## 7. 🔵 Clean Windows 10/11 machines for acceptance
- **Blocked:** P10 Windows acceptance matrix (reboot/logon/power-loss/crash/uninstall lifecycle).
- **Needs from client (or VI infra):** clean Win10 + Win11 environments (VMs acceptable).
- **Built around it:** installer + agent lifecycle logic complete; `WINDOWS_ACCEPTANCE.md` checklist ready
  to fill with real run evidence (P10).

## 8. 🔵 GitHub Actions minutes (private repo)
- **Blocked:** the CI workflow running on GitHub's hosted runners. CI is correctly configured and
  **passed green** while minutes were available (run 4cc0fb7); every run since dies at startup with
  0 steps — the signature of **exhausted free Actions minutes** on the account (`Alkalid-security`
  is a **User** account, private repo → 2000 free min/month shared across all its private repos).
- **Needs from client:** either add an Actions spending limit / minutes to the account, make the repo
  public (unlimited Actions), or provide a self-hosted runner.
- **Built around it:** the pipeline is done and verified — every CI step passes **locally**
  (`tools/secret_scan.py`, `tools/lint_migrations.py`, the 5 offline suites incl. real ONNX, portal
  build). Merges this session were gated on that local run, not the quota-blocked runner.
- **Acceptance when unblocked:** a pushed commit turns the CI checks green on GitHub.

## 9. 🔵 Final pricing approval
- **Blocked:** publishing production pricing on the marketing site + portal plan picker.
- **Needs from client:** approved final tiers/amounts.
- **Built around it:** pricing is data-driven; the current `03_Design/PRICING.md` is explicitly marked
  draft/unvalidated and will not be presented as production until approved.

---

**Not blocked on the client (VI-controlled, in progress):** security foundation, SendGrid HTML template,
n8n pipeline wiring, agent AI packaging, portal surfaces, billing code+model+sandbox, NSIS source,
CI, runbooks, demo mode, domain-configurability, WordPress polish.
