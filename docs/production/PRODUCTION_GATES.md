# WatchLog — Production Gates

Re-baselined **2026-09-04** against `docs/audit/CURRENT_STATE_2026-09-04.md`.

A gate is only green for the layer explicitly named in the check. **Repository/CI success is not proof that the deployed production system is running the same schema/build.**

Legend: ✅ verified · 🟡 code ready / live verification pending · ⏳ pending · 🔵 external/client/field input required · ❌ known gap/failure.

## 0. Production truth / parity

| Gate | Check | Status |
|---|---|---|
| TRUTH-1 current repo baseline | `main` commit and latest CI identified | ✅ `4efa9a645cc0c2bffe10f0f5f360195fb2fbf751`; CI run `33611100973` success (2026-09-02) |
| TRUTH-2 current DB boundary | read production schema markers before any DDL | 🟡 latest direct operator evidence showed `0023` boundary; must re-read because another operator/tool may have changed it afterwards |
| TRUTH-3 deployed service revisions | record exact portal/WP/bridge/report/billing deployed revision/image | ⏳ no Coolify connector/live DNS proof in this audit environment |
| TRUTH-4 live route smoke | authenticated smoke of portal + admin + marketing routes | ⏳ live sslip hostnames were not resolvable from this audit environment |
| TRUTH-5 source-of-truth docs | current-state, gates, dependencies and claims agree | ✅ re-baselined on Sprint 0 branch; branch CI run `33863464289` succeeded |

## 1. Security / database

Historical security gates through migration `0023` remain valid evidence for the time they were run, but the full set must be re-run after the pending production migration train is reconciled.

| Gate | Check | Status |
|---|---|---|
| SEC-1 tenant isolation baseline | `prototype/tests/test_tenant_isolation.py` → 9/9 on appropriate target | ✅ historical baseline; **rerun after production parity** |
| SEC-2 anon customer-data | anon PostgREST access denied/empty on tenant data | ✅ historical baseline; rerun after parity |
| SEC-3 app migration ledger locked | `public.schema_migrations` RLS + anon/public grants removed | ✅ migration `0016` exists and was previously verified live |
| SEC-4 no self-service paid state | tenant owner cannot forge authoritative paid state | ✅ historical billing-authz verification; rerun after parity |
| SEC-5 SECURITY DEFINER hygiene | no unsafe definer functions / pinned search path where required | 🟡 code contracts exist; re-audit after `0024–0036` parity |
| SEC-6 write grants minimized | authenticated/anon do not gain unintended direct app-table writes | 🟡 re-audit after parity |
| DB-1 Analytics Studio schema | `monitoring_rules` + 0024 columns/functions exist in production | ⏳ absent in latest direct SQL evidence |
| DB-2 platform admin schema | `platform_admins` + `wl_platform_me()` exist in production | ⏳ absent in latest direct SQL evidence |
| DB-3 Site Health details | later Site Health RPC exists in production | ⏳ absent in latest direct SQL evidence |
| DB-4 later authz | enrollment/billing authz from `0034–0036` exists in production | ⏳ absent in latest direct SQL evidence |
| DB-5 migration parity | production schema == current migrations through `0036` | ⏳ Sprint 1; backup + boundary proof required before apply |
| DB-6 post-DDL advisor | Supabase security advisor reviewed after parity | ⏳ Sprint 1 |

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
| AI-2 packaged self-test | `watchlog-agent.exe --selftest` passes | ✅ prior full AI build evidence; new release workflow repeats this gate |
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
| PORT-1 static production build | Next.js production export | ✅ latest main CI |
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
| INS-1 fast NSIS contract | CI compiles NSIS with an explicit stub payload to validate manifest syntax only | ✅ latest main CI; **not a distributable release** |
| INS-2 release-script parser | authoritative PowerShell script parses on Windows | 🟡 `$Path:` parser defect fixed on Sprint 0 branch; full release workflow still needs first run |
| INS-3 genuine AI release workflow | Windows runner builds full agent, self-tests, packages, size-checks, checksums and uploads | 🟡 workflow added on Sprint 0 branch; repository public-release variables + first green run pending |
| INS-4 customer GUI setup | no terminal/input() wizard in normal customer setup | ❌ current baseline is terminal-driven; Sprint 2 |
| INS-5 silent background agent | no console window during normal background runtime | ❌ current PyInstaller production entry uses `--console`; Sprint 2 architecture change |
| INS-6 secure recorder credential storage | password not persisted as plaintext INI | ❌ current baseline stores local password in INI; Sprint 2 |
| INS-7 stable download | versioned artifact + stable latest URL | ⏳ Sprint 2 |
| INS-8 signed | Authenticode valid for inner agent + final installer | 🔵 code path exists; certificate required |
| INS-9 Win10/11 lifecycle | install/enroll/reboot/upgrade/uninstall acceptance | 🔵 clean Windows test environments/hardware |

## 7. Marketing website / product claims

| Gate | Check | Status |
|---|---|---|
| WEB-1 custom WatchLog theme | current WordPress theme/site architecture exists | ✅ code |
| WEB-2 current core positioning | existing CCTV + incidents + reports + Site Health | ✅ code/content |
| WEB-3 Analytics Studio claims | public claims match implemented analytics without exaggeration | 🟡 claims matrix corrected on Sprint 0 branch; website copy update is Sprint 3 |
| WEB-4 broader product positioning | Video Analytics & CCTV Intelligence + maturity labels | ⏳ Sprint 3 |
| WEB-5 customer/partner honesty | no KFC/McDonald's/AWS/manufacturer false customer/partner claims | ✅ guardrail documented; verify during site update |
| WEB-6 sitemap | custom `/sitemap.xml` route returns 200 and `robots.txt` points to it | 🟡 code implements the custom route because WP core `/wp-sitemap.xml` is unreliable on this install; live `/sitemap.xml` verification pending |
| WEB-7 canonical/OG/robots | canonical/OG/robots are implemented in theme; verify output live | 🟡 code verified; live production verification pending |
| WEB-8 final domain | production DNS/TLS/Auth/canonical/installer URLs | 🔵 final domain + DNS control |
| PRICE-1 current website == billing | Starter 6,000 / Growth 12,000 / Enterprise contact-only | ✅ code contract/CI |
| PRICE-2 Starter vs Standard naming | one approved naming convention everywhere | 🔵 latest meeting differs from current published naming; client decision required |

## 8. CI / release operations

| Gate | Check | Status |
|---|---|---|
| CI-1 normal PR/push CI | backend tests + portal build + NSIS manifest contract | ✅ latest Sprint 0 branch run `33863464289` succeeded; latest main run `33611100973` also succeeded |
| CI-2 secret scan | tracked tree scan | ✅ in CI |
| CI-3 migration lint | migration lint | ✅ in CI |
| CI-4 real Windows release | separate full AI artifact workflow | 🟡 added Sprint 0; first green run pending |
| CI-5 branch protection | required checks / protected `main` | ⏳ `main` currently not protected per GitHub branch metadata |
| REL-1 stable release distribution | durable versioned customer download + latest pointer | ⏳ Sprint 2 |

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
6. website claims match maturity;
7. an actual installer download path exists;
8. no critical security/RLS regression is open;
9. a demo/pilot tenant can be demonstrated end to end.

Fire/smoke, true live streaming, POS/Shopify, attendance identity and every vertical custom solution are **not** staging prerequisites.
