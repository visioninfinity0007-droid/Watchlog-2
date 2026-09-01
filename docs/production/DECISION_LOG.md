# WatchLog — Decision Log

Concise architecture decisions taken during production completion. Newest first.
Format: Decision · Reason · Evidence · Rollback.

---

### 2026-09-01 · P11/closure — One authoritative pricing source (website == billing), CI-enforced
- **Decision:** treat the **published website** as the single source of truth for demo pricing —
  Starter PKR 6,000/mo, Growth PKR 12,000/mo, Enterprise "Talk to us" (contact-only). `0022_pricing_align.sql`
  sets `billing_plans` to match (Starter/Growth non-draft; Enterprise `active=false`); the portal already
  renders `wl_billing_plans`, so it inherits the aligned numbers. `test_pricing_alignment.py` parses the WP
  templates and the migration chain and **fails CI** if they diverge (while non-draft).
- **Reason:** the review found the site advertised 6,000/12,000 while the DB seeded draft 2,500/5,000/9,000 —
  a contradiction a client would see. We do **not** invent final pricing; we align the demo to what is
  already published and make drift a test failure.
- **Evidence:** `wl_billing_plans` → Starter 6,000 / Growth 12,000; `wl_billing_start_checkout('enterprise')`
  → 400 (contact-only); `test_pricing_alignment` PASS; portal build renders the same.
- **Rollback:** `0022` is data-only (`update billing_plans …`); a one-line migration restores prior amounts.

### 2026-09-01 · P11/closure — Central entitlement (`wl_reporting_enabled`); reporter enforces, data preserved
- **Decision:** add one authoritative `wl_reporting_enabled(tenant)` + `wl_entitlement()` (`0023`). Reporting
  is enabled for `active`, `past_due` (documented grace), and `trialing` within the trial window; disabled for
  expired/cancelled/lapsed trials. `daily_report.py` consults it and **skips** disabled tenants; the portal
  shows the same active/paused state. Trial expiry enforces only the **commercial** function — it never
  deletes events/snapshots/data.
- **Reason:** marketing says reporting stops when the trial ends, but the reporter processed every tenant
  regardless. Enforcement must be one function used by both reporter and portal, and driven by billing state.
- **Evidence:** `test_entitlement.py` 6/6 (active/past_due/trial-valid → enabled; trial-expired/cancelled/
  expired → disabled); `e2e_http.py` step 7 shows the runner reporting `skipped` under enforcement; portal
  build renders the pill.
- **Rollback:** drop the two functions and remove the reporter's `if not enabled: continue` guard; behaviour
  reverts to "report for all" (no data implications either way).

### 2026-09-01 · closure — Remove temp-URL fallbacks from shippable code; keep sslip only as demo default
- **Decision:** remove every hardcoded `sslip.io`/`161.97.175.15` from code that ships (reporter portal-link,
  Inno `AppPublisherURL`, WP theme portal-URL default). They now read env / build-define with a neutral
  `watchlog.example` placeholder. The only remaining sslip is an **env-overridable** demo default in
  `deploy/wordpress/site-content.sh` (`${WATCHLOG_PORTAL_URL:-…}`), which is demo tooling, not a prod artifact.
- **Reason:** DOM-1 must be able to pass on "no temporary hostname baked into the production build" without
  waiting on the client's final domain (a separate block).
- **Evidence:** repo grep for the two tokens shows only the demo default + docs; DOM-1 flipped to PASS with the
  final-domain block kept explicitly separate.
- **Rollback:** none needed; env vars restore any host for the demo environment.

### 2026-09-01 · closure — True external-boundary E2E; fix the dead cross-tenant probe
- **Decision:** add `e2e_http.py` — a higher-level E2E that drives the **deployed** interfaces over HTTP
  (GoTrue auth → tenant/enroll/ingest/incidents RPC → report-runner HTTP `/run` → billing **via the deployed
  billing service** checkout→pay page→signed webhook→active → cross-tenant PostgREST denial), keeping the
  privileged-Postgres `e2e_harness.py` as the disposable-tenant journey. Replace the harness's dead
  `if False:` cross-tenant block with a real PostgREST read asserting 0 foreign rows.
- **Reason:** the review correctly noted the harness used privileged Postgres for key transitions (so it did
  not test the shipped boundary) and contained an unreachable probe. Don't claim a boundary the code bypasses.
- **Evidence:** `e2e_http.py` 9/9 (billing step: `page 200, active True` through the service); `e2e_harness.py`
  14/14 with the real probe (`own only, 0 foreign`). Signup uses the real endpoint; when GoTrue returns 429
  (rate-limited) the test seeds a confirmed user via DB **only to continue the remaining HTTP steps**, and
  labels that it did so — the signup endpoint itself is still exercised every run.
- **Rollback:** tests only; no product code.

### 2026-09-01 · P9 — Real NSIS installer (contract tech), reusing the proven service registration
- **Decision:** add `prototype/installer/nsis/watchlog.nsi` (a real NSIS/MUI2 installer) + a
  deterministic `tools/build_windows_release.ps1` that builds the AI exe, stages the payload,
  compiles with `makensis`, and emits `WatchLog-Setup.exe` + a SHA256. It reuses the existing,
  proven `register-service.ps1` (SYSTEM scheduled task + power hardening) and `run-agent.cmd`
  rather than reimplementing them. Publisher URL is a build `-D` define (no hardcoded host);
  `signtool` runs only when a cert is supplied. NSIS was installed via `winget install NSIS.NSIS`.
- **Reason:** the signed scope names **NSIS**; the audit found only an uncompiled Inno script +
  a ZIP fallback (contract-tech mismatch). NSIS closes that. Reusing the tested service scripts
  keeps the boot/restart behaviour identical to what already worked.
- **Evidence:** audit §N — "Inno/PowerShell, ships ZIP, no `.nsi`, no Setup.exe".
- **Rollback:** the Inno `.iss` and the ZIP packager remain; nothing removed.
- **Still CLIENT-BLOCKED:** code-signing cert (SmartScreen) and Win10/11 lifecycle acceptance
  (clean VMs) — the installer builds and is structurally complete regardless.

### 2026-09-01 · P2 — Ship the AI filter via onnxruntime; commit the exported model
- **Decision:** the shippable agent build (`build_exe.ps1 -WithAI`) bundles **onnxruntime + numpy +
  PIL + `prototype/models/yolov8n.onnx`** and excludes torch/ultralytics. The exported model is
  **committed to git** (un-ignored) so the AI build is reproducible on any machine with onnxruntime —
  no torch/ultralytics needed at build time.
- **Reason:** torch is >1 GB and (here) fails to unpack under Windows' 260-char path limit; onnxruntime
  is ~15 MB and the model ~12 MB. The `OnnxDetector` already implements the full YOLOv8 decode. Model
  is a fixed official artifact, so committing it is safe and makes releases deterministic.
- **Evidence:** audit proved the shipped exe carried no runtime/model (strings scan). Torch install
  failed with a long-path OSError; a venv at `C:\wlv` (short root) installs torch fine — used only to
  export the model once, never shipped.
- **Verification:** `--selftest` runs the model through onnxruntime on a junk frame and must discard it
  (exit 0); `tools/verify_agent_ai.py` runs it against the frozen exe. Proof by execution, not strings.
- **Rollback:** the lean build (default) still works — the filter fails open — so reverting is just
  building without `-WithAI`.

### 2026-08-31 · P0 — Merge audit PR #1 into main before building
- **Decision:** merge the docs-only audit PR into `main`, branch `production/watchlog-end-to-end` from it.
- **Reason:** the audit is the agreed baseline; every phase references it. Keeping it on `main` makes
  the baseline canonical and the working branch traceable.
- **Evidence:** PR #1 was clean, docs-only, mergeable (3 files, 0 code).
- **Rollback:** revert the merge commit `70cd7d6`; audit content is immutable history regardless.

### 2026-08-31 · P1 — Kill self-service paid state; make billing state webhook-authoritative
- **Decision:** `wl_set_plan` will become **request-only** (a tenant owner may request a plan / start a
  checkout) and will **not** be able to set `subscription_status` to a paid/active value. Authoritative
  paid state is written only by an internal, non-customer path (Switch webhook / admin), keyed to
  verified payment events.
- **Reason:** SOW billing-security invariant D — customers never authoritatively mark themselves paid.
- **Evidence:** audit showed `wl_set_plan(p_plan,p_status)` lets any owner set `subscription_status`
  (AKSS tenant is already `starter/active`, no payment).
- **Rollback:** the superseding migration keeps the old function body in a comment; a one-line migration
  restores it if billing is not yet live and manual plan-setting is temporarily needed.

### 2026-08-31 · P1 — Reconcile migrations 0010–0015 into the ledger WITHOUT replay
- **Decision:** insert ledger rows for 0010–0015 (recording their real file sha256) rather than
  re-running them; make `apply_migrations.py` treat an already-present object set as applied.
- **Reason:** the objects are already live; replaying `create`/`alter` could error or, worse, be
  destructive. The ledger must simply reflect reality.
- **Evidence:** audit — ledger stops at 0009; 0010–0015 objects all exist live.
- **Rollback:** ledger rows are additive; deleting the 6 inserted rows restores the prior ledger.

### 2026-08-31 · P1 — Tighten 4 `public`-role RLS policies to `authenticated`
- **Decision:** re-declare the SELECT policies on `push_sources`, `report_recipients`, `invitations`,
  `report_deliveries` for role `authenticated` (same `wl_is_member` USING clause).
- **Reason:** defense in depth + consistency with the other 8 policies; today they rely solely on the
  USING clause holding for anon.
- **Evidence:** audit — these 4 are the only `public`-role policies; anon already returns `[]` but the
  grant surface is unnecessarily broad.
- **Rollback:** re-declare for `public`; behavior is identical for real users.

### 2026-08-31 · P1 — Lock `schema_migrations` (RLS on + revoke anon/public writes)
- **Decision:** enable RLS with no policy on `schema_migrations` and `REVOKE ALL … FROM anon, public`
  (keep owner/service access for the migration runner).
- **Reason:** anon must not read the schema-evolution ledger or write to any table.
- **Evidence:** audit — `has_table_privilege('anon',…,'INSERT')` was true.
- **Rollback:** re-grant; low risk (table holds only migration filenames/hashes).
