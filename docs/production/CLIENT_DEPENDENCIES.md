# WatchLog — Client / External Dependencies

Re-baselined **2026-09-04**. This file lists work that cannot be completed by repository changes alone.

A dependency is not permission to mark the related feature complete. When the input arrives, run the acceptance procedure in `PRODUCTION_GATES.md` and record evidence.

Status: 🔵 waiting on client/external access · 🟡 access/tooling limitation in this ChatGPT session · ✅ resolved.

---

## 1. 🔵 Real-hardware field validation

- **Blocked:** stable recorder soak, real camera sync, people/vehicle/dwell measurement accuracy, clip/playback research and camera-placement calibration.
- **Needs:** live recorder/site access, recorder admin credentials provided through an approved secure channel, representative cameras, and permission to collect labelled test footage where required.
- **Known historical field issue:** SM-HP Site Agent previously stopped after roughly three minutes and synced zero cameras; this must be re-tested rather than assumed fixed.
- **Built around it:** recorder drivers, discovery, local analytics engine, simulator tests, Site Health, configuration snapshot requests and field-validation documentation.
- **Acceptance:** install/enroll → recorder proven → cameras synced → >1h soak → restart/reboot survives → events/analytics reach portal → measured FP/FN/count/dwell results recorded.

## 2. 🟡 WatchLog production Supabase access from this connector

- **Blocked here:** direct execution/re-verification of WatchLog production migrations/auth/schema.
- **Current connector visibility:** only the unrelated `Al khalid` project (`jssitaduuhjvyznldfoc`) is available. The WatchLog project is not exposed to this ChatGPT Supabase connector.
- **Do not:** apply WatchLog migrations to the Al khalid project.
- **Latest direct operator evidence:** WatchLog production was at the `0023` boundary when queried on 2026-09-04; later migrations were absent at that time.
- **Acceptance when access is available:** backup → prove boundary → apply only pending migrations → security/RLS/role verification → route smoke → advisor review.
- **Handoff path:** Claude/local Supabase admin can perform this bounded procedure if connector access remains unavailable.

## 3. 🟡 Coolify / deployed-service access

- **Blocked here:** proving exact deployed portal/WordPress/bridge/report/billing revision, environment variables and service health.
- **Current limitation:** no Coolify connector is available in this session; sslip hostnames were not resolvable from the audit runtime on 2026-09-04.
- **Acceptance:** record deployed revision/image for each service, verify health endpoints, verify portal build vars/installer URL, and run authenticated route smoke.
- **Handoff path:** Claude/local server admin if this access remains unavailable.

## 4. 🔵 Final production domain and DNS

- **Blocked:** final customer domain, TLS cutover, canonical/OG/Auth redirect URLs and durable installer publisher/download URLs.
- **Needs:** approved domain + DNS control/delegation.
- **Built around it:** production URLs are intended to remain configuration-driven rather than hardcoded to the temporary sslip host.
- **Acceptance:** DNS/TLS live, Auth redirects correct, canonical/OG correct, installer/portal links correct, no stale temporary production URLs.

## 5. 🔵 Windows code-signing certificate

- **Blocked:** Authenticode-signed inner agent and final `WatchLog-Setup.exe`, reduced SmartScreen friction.
- **Needs:** appropriate OV/EV code-signing certificate and secure signing credentials/process.
- **Built around it:** release script supports optional fail-closed signing and signature verification.
- **Acceptance:** inner agent and final installer show valid Authenticode signature; timestamp verifies.

## 6. 🔵 Clean Windows 10/11 acceptance environments

- **Blocked:** customer lifecycle acceptance for graphical installer/background agent.
- **Needs:** clean Windows 10 and Windows 11 machines/VMs; real hardware for recorder-specific paths where necessary.
- **Acceptance:** fresh install, enrollment, discovery, background start, logon/reboot, crash recovery, upgrade, uninstall/reinstall, DPI 100/125/150%, failure/cancel paths.

## 7. 🔵 Live payment-provider contract and credentials

- **Blocked:** real Switch/payment-provider sandbox/live checkout and webhook handshake.
- **Needs:** exact provider API/signing contract + sandbox/live credentials.
- **Built around it:** billing model, provider abstraction, idempotent webhook model, entitlement path and mock/sandbox flow.
- **Acceptance:** real sandbox checkout → verified webhook → authoritative active state → replay no-op → tenant cannot forge paid state.

## 8. 🔵 Interim bank-transfer workflow decision

- **Blocked:** whether WatchLog should ship payment-proof upload/admin verification before the final gateway.
- **Needs:** client decision on whether this interim commercial path is required.
- **If approved:** build authenticated private proof upload, amount/reference/status, admin verification, audit trail and backend-only subscription activation.
- **If not approved:** do not create temporary payment complexity; proceed directly with provider integration.

## 9. 🔵 SendGrid account/domain authentication

- **Blocked:** authorized real branded email send.
- **Needs:** SendGrid API key and verified sender/domain/DKIM as appropriate.
- **Built around it:** branded HTML + plain-text fallback and rendering tests.
- **Acceptance:** approved test recipient receives the message and delivery is recorded as sent.

## 10. 🔵 Live WhatsApp send authorization/provider state

- **Blocked:** real scheduled delivery to an approved number.
- **Needs:** explicit go-ahead, test destination, and working Evolution/provider credentials/state.
- **Acceptance:** scheduled run sends once, delivery log records success, no duplicate same-day delivery.

## 11. 🔵 Pricing package naming approval

- **Current verified public/code alignment:** Starter PKR 6,000/month, Growth PKR 12,000/month, Enterprise “Talk to us”.
- **New ambiguity:** latest client meeting used “Standard / Growth / Enterprise”.
- **Needs:** decision whether Starter is renamed Standard and whether any packaging/allowance changes accompany it.
- **Do not:** rename one surface in isolation. Pricing website, billing seed/config, portal and tests must change together.

## 12. 🔵 Production camera allowances if hard-enforced

- **Current website positioning:** up to 8 / 24 / unlimited cameras.
- **Needs:** client confirmation if these are contractual hard limits and, if so, enforcement behavior.
- **Acceptance:** one authoritative plan definition used by website, portal, billing and backend entitlement checks.

## 13. 🔵 QSR / Control Room pilot site

- **Blocked:** field acceptance of the first QSR/control-room configuration.
- **Needs:** pilot site, camera map/purposes, recorder access, operating hours, analytics questions and designated stakeholders.
- **Acceptance:** saved camera layout, real health/event state, configured people/dwell rules, report output and measured field behavior.
- **Important:** KFC/McDonald's are target/example use cases unless an actual customer engagement is formally confirmed. Do not claim customer status.

## 14. 🔵 Fire/smoke dataset and safety positioning

- **Blocked:** fire/smoke R&D validation.
- **Needs:** approved dataset/model strategy, representative CCTV test footage and product/legal decision on safety positioning.
- **Acceptance:** defined model, held-out test set, measured precision/recall, failure semantics, controlled pilot.
- **Never:** market WatchLog as a certified fire-safety system without the relevant certification and evidence.

## 15. 🔵 Integration pilot choice and system access

- **Blocked:** first production connector.
- **Candidate systems:** POS, Shopify, attendance machine, CRM/API.
- **Needs:** one prioritized signed pilot, API/docs/sandbox credentials, data ownership/reconciliation rules and acceptance criteria.
- **Do not:** build every connector simultaneously.
- **Shopify note:** stock updates should start as a reconciliation/approval workflow, not autonomous mutation based only on vision confidence.

## 16. 🔵 Formal partnerships / logo permissions

- **AWS:** do not claim partnership or use partner badges until formal status is confirmed.
- **Camera/manufacturer companies:** compatibility does not equal partnership.
- **Customers/case studies:** logos, names and case studies require verified status/permission.

---

## Resolved since the older dependency register

### ✅ Google Drive project-folder access

The shared WatchLog directory is now visible to `muhammad.awais@codup.co`, and the master audit/sprint plan has been moved into it.

### ✅ GitHub Actions availability (current evidence)

The older register said hosted Actions minutes were exhausted. That is no longer the current evidence: `main` CI run `33611100973` completed successfully on 2026-09-02. Do not list Actions minutes as an active blocker unless a new run proves the quota problem has returned.

### ✅ Published pricing exists

The older register described final pricing as unpublished/draft. Current code/content is already aligned to Starter 6,000 / Growth 12,000 / Enterprise contact-only. The open item is now **package naming/approval**, especially Starter vs Standard, not absence of pricing.

---

## Work controlled by the repository team (not a client blocker)

The following should continue without waiting on the client unless they reach one of the external gates above:

- source-of-truth cleanup;
- release workflow hardening;
- graphical installer implementation;
- secure local credential-storage implementation;
- portal/website changes;
- Control Room v1 code;
- reporting hierarchy code;
- support/admin operations code;
- integration-framework code;
- tests/contracts/runbooks;
- product maturity labels and honest public copy.
