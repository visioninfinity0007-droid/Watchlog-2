# WatchLog Portal Finalization — 4 September 2026

## Directive
Finalize the Platform Admin and customer portal before Control Room or broader website expansion.

Principle:

> Platform Admin must be able to operate the SaaS business. The customer portal must let customers use WatchLog without exposing internal implementation details.

## Current finding

The Platform Admin already has cross-tenant visibility, platform roles, audit, trial controls and a platform-owner-only manual subscription override in the selected-tenant detail view. The package-change capability therefore exists but is poorly surfaced. Billing is primarily read-only and there is no first-class invoice, contract, support-session, customer-document or internal support-note model.

The customer portal works across Overview, Incidents, Site Health, Analytics, Reports, Team and Settings, but several labels and explanations expose engineering implementation terms such as Site Agent, monitoring rules, enrollment mechanics, recorder internals and delivery endpoints. These should be rewritten as customer-facing WatchLog product/support language while preserving required security/setup instructions.

## Target Platform Admin information architecture

- Overview
- Customers
- Operations
- Commercial
- Support
- Admins
- Audit
- Platform Settings

### Customer 360
Each customer gets a dedicated detail workspace with:
- Summary
- Sites & connections
- Users & access
- Plan & entitlements
- Billing
- Invoices
- Contracts
- Reports
- Support notes
- Activity / audit

Header actions:
- Open customer workspace
- Change plan
- Grant/extend trial
- Generate invoice
- Create contract
- Send password reset
- Invite/resend user
- Suspend/reactivate account

## Safe customer-workspace support mode

Do not forge or replace the customer user's Auth session. Implement an audited WatchLog Support session:
- platform admin/owner selects customer and provides a reason;
- short-lived support session bound to admin user + tenant + reason + expiry;
- persistent banner: "Viewing <customer> as WatchLog Support";
- explicit Exit support mode;
- all actions remain attributed to the platform admin, never to the customer;
- read-only by default for support role;
- mutations require the platform role already authorized for that action;
- sensitive commercial/security actions remain outside support mode or require explicit re-confirmation;
- no password, recorder credential, service key or customer secret exposure.

## Commercial management

Add authoritative models and workflows for:
- billing profile / legal details;
- plan and subscription changes with effective dates;
- add-ons / entitlement overrides;
- manual payment recording;
- invoice numbering, draft/issued/paid/void/overdue states;
- invoice line items, tax, due date, payment terms and PDF;
- contracts with version/status/start/end/renewal dates and signed-document storage;
- customer billing contacts;
- payment/contract reminders;
- all commercial mutations audited.

PDF/document generation must be server-side/authoritative, not fabricated in client-only portal code.

## Support operations

Add:
- internal customer notes and tags;
- account owner / implementation status;
- onboarding incomplete queue;
- offline/stale site queue;
- failed report queue and resend/test controls where supported;
- password reset / invite resend actions through sanctioned Supabase Auth flows;
- connection/setup-code regeneration;
- diagnostics request/status where supported by the existing agent architecture;
- customer support-mode session history.

## Customer portal language standard

Customer-facing pages must speak as WatchLog, not as engineering documentation.

Examples:
- Tenant -> Account / Customer (never show tenant jargon to customer)
- Site Agent -> WatchLog connection / WatchLog app where a label is needed
- Agent offline -> WatchLog offline / Site connection offline
- heartbeat -> last connection
- monitoring rule -> analytics setup / insight
- measurement -> activity insight / analytics result
- delivery endpoint -> report recipient
- enrollment code -> setup code / connection code
- recorder internals -> CCTV system unless the customer must identify the recorder
- "validated on site" -> verified detection or omit technical source wording

Keep implementation terms out of customer copy unless they are required for a specific support/setup action. Do not expose RPC, RLS, Supabase internals, service roles, DPAPI implementation, SECURITY DEFINER, internal table/function names or deployment architecture.

Security/value statements may remain when useful to the customer, but phrase them as outcomes (for example, "Your CCTV login stays on your site" and "No inbound network access is required") rather than implementation documentation.

## Sprint sequence

### Portal Sprint A — Admin SaaS Operations (P0)
- Restructure admin IA around Customers / Commercial / Support.
- Make existing plan/trial controls obvious from Customer 360.
- Add customer lifecycle controls and audited reasons.
- Implement safe Support Mode / Open customer workspace.
- Add internal support notes.
- Add auth/user support actions.

### Portal Sprint B — Commercial Documents (P0/P1)
- Billing profile.
- Invoice + invoice items + numbering + PDF.
- Contract record + document lifecycle.
- Manual payment and invoice status controls.
- Billing contacts and email delivery.
- Commercial timeline in Customer 360.

### Portal Sprint C — Customer Experience Rewrite (P0)
- Rewrite Overview, Incidents, Site Health, Analytics, Reports, Team and Settings copy.
- Remove engineering/developer language from customer-facing UI.
- Rename technical health labels to customer language.
- Preserve only setup/security details the customer actually needs.
- Add CI contract against banned implementation terminology in customer pages.

### Portal Sprint D — Operational Closure (P1)
- Report resend/test flows.
- Connection/setup support actions.
- Platform task queues.
- Audit export/filtering.
- Customer/account status and suspension lifecycle.
- Visual/interaction consistency QA for admin and customer portals.

## Acceptance

Portal finalization is complete when:
- platform owner can manage customer lifecycle, package, trial, commercial documents and support actions without SQL;
- invoices and contracts are tenant-scoped and auditable;
- support can safely enter a customer workspace without impersonating the customer's identity;
- customer account cannot access Platform Admin;
- customer-facing language does not reveal internal implementation details;
- normal customer setup and support can be completed without developer intervention;
- all privileged changes require authorization, reason and audit evidence.
