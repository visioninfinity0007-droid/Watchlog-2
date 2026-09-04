# WatchLog — Production Gates

Re-baselined **2026-09-04** against the current repository state after the merged Sprint 2, Sprint 3 and Sprint 1 read-only preflight work.

A gate is only green for the layer explicitly named in the check. **Repository/CI success is not proof that the deployed production system is running the same schema/build.**

Legend: ✅ verified at the stated layer · 🟡 code ready / live verification pending · ⏳ pending · 🔵 external/client/field input required · ❌ known gap/failure.

## 0. Production truth / parity

| Gate | Check | Status |
|---|---|---|
| TRUTH-1 current repo baseline | `main` commit and latest CI identified | ✅ `c8aad36816d3a0593cd6da7e76df0924f41aeec8`; PR #18 CI `33870598028` success; post-merge main CI `33871119061` running at this update |
| TRUTH-2 current DB boundary | read production schema markers before any DDL | 🟡 latest direct operator evidence showed the `0023` boundary; `tools/watchlog_production_preflight.py` is now merged for a fail-closed fresh read, but the WatchLog project remains permission-denied in this connector |
| TRUTH-3 deployed service revisions | record exact portal/WP/bridge/report/billing deployed revision/image | ⏳ no usable Coolify integration in this session; deployment revision remains unverified |
| TRUTH-4 live route smoke | authenticated smoke of portal + admin + marketing routes | ⏳ live deployment/auth smoke still required |
| TRUTH-5 source-of-truth docs | current-state, gates, dependencies, master plan and claims agree | 🟡 master plan/claims are current; this gate file is being synchronized after Sprint 2/3 and the merged preflight work |

## 1. Security / database

Historical security gates through migration `0023` remain valid evidence for the time they were run, but the full set must be re-run after the production migration train is reconciled.

| Gate | Check | Status |
|---|---|---|
| SEC-1 tenant isolation baseline | `prototype/tests/test_tenant_isolation.py` on appropriate target | ✅ historical baseline; **rerun after production parity** |
| SEC-2 anon customer-data | anon PostgREST access denied/empty on tenant data | ✅ historical baseline; rerun after parity |
| SEC-3 app migration ledger locked | `public.schema_migrations` RLS + anon/public grants removed | ✅ migration `0016` exists and was previously verified live; current ledger existence/shape must still be re-read |
| SEC-4 no self-service paid state | tenant owner cannot forge authoritative paid state | ✅ historical billing-authz verification; rerun after parity |
| SEC-5 SECURITY DEFINER hygiene | no unsafe definer functions / pinned search path where required | 🟡 code contracts exist; re-audit after `0024–0036` parity |
| SEC-6 write grants minimized | authenticated/anon do not gain unintended direct app-table writes | 🟡 re-audit after parity |
| DB-0 read-only production preflight | wrong project rejected, transaction forced read-only, schema/ledger/admin evidence collected without DDL | ✅ code + CI contract merged in PR #18; live execution requires authorized WatchLog DB access |
| DB-1 Analytics Studio schema | `monitoring_rules` + 0024 columns/functions exist in production | ⏳ absent in latest direct SQL evidence |
| DB-2 platform admin schema | `platform_admins` + `wl_platform_me()` exist in production | ⏳ absent in latest direct SQL evidence |
| DB-3 Site Health details | later Site Health RPC exists in production | ⏳ absent in latest direct SQL evidence |
| DB-4 later authz | enrollment/billing authz from `0034–0036` exists in production | ⏳ absent in latest direct SQL evidence |
| DB-5 migration parity | production schema == current migrations through `0036` | ⏳ Sprint 1; authorized environment must run the read-only preflight first, then backup + bounded migration runbook |
| DB-6 post-DDL advisor | Supabase Security Advisor reviewed after parity | ⏳ Sprint 1 |

## 2. Reporting

| Gate | Check | Status |
|---|---|---|
| REP-1 branded HTML | HTML + plain fallback render tests | ✅ code/tests |
| REP-2 idempotency | same site/day does not create duplicate sent delivery | ✅ code/design tests |
| REP-3 WatchLog-owned config | reporter uses WatchLog env, not sibling app config | ✅ code |
| REP-4 Analytics Studio report section | report renderer supports analytics aggregates | ✅ code/tests |
| REP-5 deployed report runner | current deployed revision/health verified | ⏳ must reverify live deployment |
| REP-6 authorized external email | real SendGrid delivery to approved recipient | 🔵 SendGrid credential/domain input |
| REP-7 authorized external WhatsApp | real scheduled Evolution delivery to approved number | 🔵 authorization/test destination/provider state |
| REP-8 collective/QSR reporting | camera → site → multi-site/executive report hierarchy | ⏳ Sprint 4/7 extension |

## 3. Agent / AI / analytics

| Gate | Check | Status |
|---|---|---|
| AI-1 ONNX runtime/model packaged | production AI build includes runtime/model | ✅ code + prior frozen-exe evidence |
| AI-2 packaged self-test | `watchlog-agent.exe --selftest` passes | ✅ prior full AI build evidence; real release workflow repeats this gate |
| AI-3 current detector classes | person/car/motorcycle only | ✅ explicit product/code scope |
| AI-4 fail-open incident filtering | detector failure keeps event | ✅ tests |
| ANA-1 Analytics Studio engine | line/zone/dwell/schedule rules deterministic | ✅ code/tests |
| ANA-2 configured analytics | Visitor Flow, Vehicle Flow, Boundary, Zone, Dwell, After-Hours | ✅ code; production DB parity pending |
| ANA-3 field people-count accuracy | measured on real target camera placements | 🔵 field footage/site access |
| ANA-4 field dwell/loitering accuracy | measured error/threshold behavior | 🔵 field footage/site access |
| ANA-5 fire/smoke | dedicated model + dataset + acceptance evidence | ❌ not implemented; Sprint 6 R&D |
| ANA-6 cross-camera identity | re-identification/facial identity | ❌ intentionally not in current product |

## 4. Billing / entitlement

| Gate | Check | Status |
|---|---|---|
| BILL-1 billing model | plans/customers/subscriptions/transactions/webhook/checkouts | ✅ code; migration precedes verified `0023` boundary |
| BILL-2 webhook idempotency/signature | replay no-op + invalid signature rejected | ✅ code/historical deployed test |
| BILL-3 authority | customer cannot directly forge subscription state | ✅ code/historical live test; rerun after parity |
| BILL-4 mock/sandbox path | checkout → webhook → active | ✅ code/historical deployed test |
| BILL-5 entitlement | reporter/portal honor `wl_entitlement` / `wl_reporting_enabled` | ✅ `0023` directly confirmed in production |
| BILL-6 real payment provider | real Switch/provider sandbox/live handshake | 🔵 provider API/signing contract + credentials |
| BILL-7 interim bank transfer | authenticated proof/verification workflow if selected | ⏳ client commercial decision + Sprint 7 if chosen |

## 5. Customer portal / onboarding

| Gate | Check | Status |
|---|---|---|
| PORT-1 static production build | Next.js production export | ✅ PR #18 CI and preceding main CI |
| PORT-2 core navigation | Overview / Incidents / Site Health / Analytics / Reports / Team / Settings | ✅ code |
| PORT-3 platform admin routes | Overview / Tenants / Operations / Billing / Admins / Audit | ✅ code; production DB schema pending |
| PORT-4 Analytics Studio UI | site/camera/rule/schedule configuration | ✅ code |
| PORT-5 onboarding state | tenant/site creation + enrollment code + setup-state polling | ✅ code |
| PORT-6 permanent installer URL | `NEXT_PUBLIC_INSTALLER_URL` resolves to customer release | ⏳ release publishing/distribution not finalized |
| PORT-7 authenticated production smoke | every route works against current production RPCs | ⏳ blocked by DB/deployment parity verification |
| PORT-8 tenant/platform denial | tenant user cannot use platform-admin APIs | 🟡 contract exists; rerun on parity target |

## 6. Windows installer / release

| Gate | Check | Status |
|---|---|---|
| INS-0 NSIS packaging source | real `.nsi` and authoritative release script exist | ✅ |
| INS-1 fast NSIS contract | CI compiles NSIS with explicit stub payloads to validate manifest syntax only | ✅ main/PR CI; **not a distributable release** |
| INS-2 release-script source | authoritative PowerShell release script parser defect fixed | ✅ merged Sprint 0 source |
| INS-3 genuine AI release workflow | Windows runner builds full agent, self-tests, packages, size-checks, checksums and uploads | 🟡 workflow exists; repository public-release variables + first configured green run still required |
| INS-4 customer GUI setup | branded graphical WatchLog setup replaces terminal/input() customer path | ✅ merged Sprint 2; Windows `setup-ui-build` repeatedly green |
| INS-5 silent background agent | no customer-visible console during normal background runtime | ✅ merged Sprint 2 hidden SYSTEM launcher/background path; field lifecycle acceptance remains external |
| INS-6 secure recorder credential storage | password not persisted in plaintext INI | ✅ machine-scoped DPAPI storage + SYSTEM/Administrators ACL + legacy migration; CI DPAPI round-trip passed |
| INS-7 stable download | durable customer-accessible versioned artifact + stable latest URL | ⏳ hosting/distribution not finalized; private Actions artifacts are not a customer download channel |
| INS-8 signed | Authenticode valid for inner agent + final installer | 🔵 code path exists; certificate required |
| INS-9 Win10/11 lifecycle | install/enroll/reboot/upgrade/uninstall acceptance | 🔵 clean Windows test environments/hardware |

## 7. Marketing website / product claims

| Gate | Check | Status |
|---|---|---|
| WEB-1 custom WatchLog theme | current WordPress theme/site architecture exists | ✅ code |
| WEB-2 core + analytics positioning | existing CCTV, incidents, analytics, reports and Site Health are represented | ✅ Sprint 3 code/content |
| WEB-3 Analytics Studio claims | public claims match implemented analytics without exaggeration | ✅ Sprint 3 claims matrix + public-claims CI contract |
| WEB-4 broader product positioning | Video Analytics & CCTV Intelligence with current vs roadmap separation | ✅ Sprint 3 repository content; live WordPress deployment still unverified |
| WEB-5 customer/partner honesty | no false KFC/McDonald's/AWS/manufacturer customer/partner claims | ✅ guarded by source + public-claims contract |
| WEB-6 sitemap | custom `/sitemap.xml` route returns 200 and `robots.txt` points to it | 🟡 code implements the custom route; live verification pending |
| WEB-7 canonical/OG/robots | canonical/OG/robots implemented in theme | 🟡 code verified; live production verification pending |
| WEB-8 final domain | production DNS/TLS/Auth/canonical/installer URLs | 🔵 final domain + DNS control |
| PRICE-1 current website == billing | Starter 6,000 / Growth 12,000 / Enterprise contact-only | ✅ code contract/CI |
| PRICE-2 Starter vs Standard naming | one approved naming convention everywhere | 🔵 latest meeting differs from current published naming; client decision required |

## 8. CI / release operations

| Gate | Check | Status |
|---|---|---|
| CI-1 normal PR/push CI | backend tests + PHP lint + preflight contract + portal build + NSIS + setup UI/DPAPI | ✅ PR #18 CI `33870598028`; preceding main run `33870037809` succeeded |
| CI-2 secret scan | tracked tree scan | ✅ in CI |
| CI-3 migration lint | migration lint | ✅ in CI |
| CI-4 real Windows release | separate full AI artifact workflow | 🟡 workflow exists; first configured green `Windows Release` run not yet evidenced |
| CI-5 branch protection | required checks / protected `main` | ⏳ not currently proven/enforced from available metadata |
| REL-1 stable release distribution | durable versioned customer download + latest pointer | ⏳ external hosting/distribution path still required |

## 9. Control Room / QSR pilot

| Gate | Check | Status |
|---|---|---|
| CTRL-1 dedicated route | `/control-room/` exists | ❌ Sprint 4 |
| CTRL-2 saved layouts | 2×2 / 3×3 / 4×4 layouts | ❌ Sprint 4 |
| CTRL-3 real camera state | health/last-seen/purpose/events shown from real data | ❌ Sprint 4 |
| CTRL-4 still semantics | latest/current still clearly distinguished from live stream | ❌ Sprint 4 |
| CTRL-5 QSR analytics preset | visitor flow/dwell/occupancy/schedule presets | ⏳ built from existing engine in Sprint 4 |
| CTRL-6 collective report | pilot camera/site/multi-site report | ⏳ Sprint 4/7 |
| CTRL-7 live video wall | actual live streaming architecture | ❌ separate R&D decision; not implied by Control Room v1 |

## 10. Integrations / custom solutions

| Gate | Check | Status |
|---|---|---|
| INT-1 integration foundation | connector account/config + outbox/webhook/retry/idempotency/audit | ❌ Sprint 8 |
| INT-2 POS | signed pilot connector | ❌ not implemented |
| INT-3 Shopify | reconciliation-first connector | ❌ not implemented |
| INT-4 attendance system | external attendance connector | ❌ not implemented |
| INT-5 CRM/API | scoped connector/API | ❌ not implemented |

## 11. Field / hardware

| Gate | Check | Status |
|---|---|---|
| FLD-1 stable recorder soak >1h | real recorder/cameras sync and survive restart | 🔵 real hardware/site access |
| FLD-2 multi-site camera acceptance | target pilot sites/camera matrix | 🔵 sites/hardware |
| FLD-3 FP/FN/measurement tuning | labelled real footage + measured errors | 🔵 footage/field access |
| FLD-4 clip extraction | recorder-specific snapshot/playback/clip evidence | 🔵 field validation + adapter work |
| FLD-5 camera-placement guide | placement/FOV guidance from real pilot | 🔵 field evidence |

## September staging gate

The September staging checkpoint is green only when all of the following are true:

1. production DB parity is proven;
2. platform admin/auth works without bypasses;
3. core portal routes smoke clean against production;
4. Analytics Studio current capabilities are available against production;
5. reporting and entitlement paths are coherent;
6. the updated website is deployed and its claims match maturity;
7. an actual customer-accessible installer download path exists;
8. no critical security/RLS regression is open;
9. a demo/pilot tenant can be demonstrated end to end.

Current staging-critical blockers are therefore **production DB parity, deployed-service/live-route verification, installer distribution, and end-to-end field/demo acceptance**. Fire/smoke, true live streaming, POS/Shopify, attendance identity and every vertical custom solution are **not** staging prerequisites.
