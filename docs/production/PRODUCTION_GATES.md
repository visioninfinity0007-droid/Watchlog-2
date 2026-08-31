# WatchLog — Production Gates

Machine-checkable acceptance gates. Each gate is DONE only when its check passes against the
**deployed** system. `tools/release_check.py` (P14) automates the scriptable ones.

Legend: ✅ pass · ⏳ pending · 🔵 client-blocked · ❌ fail.

## Security / DB (P1)
| Gate | Check | Status |
|---|---|---|
| SEC-1 tenant isolation | `python prototype/tests/test_tenant_isolation.py` → 9/9, exit 0 | ✅ (baseline 2026-08-31) |
| SEC-2 anon customer-data | anon PostgREST GET on every tenant table → `[]`/denied | ✅ (baseline) |
| SEC-3 schema_migrations locked | `has_table_privilege('anon','schema_migrations','INSERT')` = false; RLS on | ✅ (0016, 2026-09-01) |
| SEC-4 no self-service paid state | `wl_set_plan` cannot activate paid status; authoritative writer ungranted | ✅ (0017 + `test_billing_authz` 4/4) |
| SEC-5 migration ledger authoritative | ledger lists 0001–0018 matching git files | ✅ (2026-09-01) |
| SEC-6 SECURITY DEFINER hygiene | 0 definer funcs without pinned search_path | ✅ (baseline; new funcs pin) |
| SEC-7 FK indexes | events.camera_id, snapshots.site_id/camera_id, agents.site_id | ✅ (0016) |
| SEC-8 write grants minimized | anon+authenticated have no direct INSERT/UPDATE/DELETE on app tables | ✅ (0018) |

## Reporting (P4/P7)
| Gate | Check | Status |
|---|---|---|
| REP-1 branded HTML | rendered HTML for 0/normal/high/fault days, escaping safe | ✅ (`test_email_template` 6/6, 2026-09-01) |
| REP-2 scheduled send | n8n schedule → `report_deliveries.status='sent'` (test destination) | ⏳ |
| REP-3 idempotent | re-run same day → no duplicate `sent` row | ⏳ (unit proven; live pending) |
| REP-4 WatchLog-owned Evolution config | reporter reads WatchLog env, not another project's `.env` | ⏳ |

## Agent / AI (P2)
| Gate | Check | Status |
|---|---|---|
| AI-1 runtime packaged | onnxruntime import OK | ✅ (source `--selftest`; frozen-exe build verifies too) |
| AI-2 model packaged+loads | `yolov8n.onnx` (12.8 MB) committed, session init OK | ✅ (loads in 0.2s) |
| AI-3 inference | inference completes on a frame | ✅ (~130ms/frame) |
| AI-4 classes | only person/car/motorcycle retained; junk dropped | ✅ (`test_vision_onnx`: gray→discarded, bus.jpg→person kept) |
| AI-5 fail-open | runtime/model/inference failure → event kept | ✅ (unit 12/12 + live FAIL-OPEN exit 3) |
| AI-6 shipped in exe | frozen `watchlog-agent.exe --selftest` → RESULT PASS | ✅ (build_exe.ps1 -WithAI → 111 MB exe; --selftest loaded bundled model, inference ran, junk discarded, RESULT PASS, 2026-09-01) |

## Billing (P8)
| Gate | Check | Status |
|---|---|---|
| BILL-1 model | billing tables exist; migration applied | ⏳ |
| BILL-2 webhook idempotent | replaying a webhook event id → single effect | ⏳ |
| BILL-3 signature verify | invalid signature rejected | ⏳ |
| BILL-4 authority | subscription state only from verified events; owner cannot forge | ⏳ |
| BILL-5 sandbox path | mock checkout→webhook→active subscription | ⏳ |
| BILL-6 live Switch | real sandbox/live payment | 🔵 (creds) |

## Onboarding / Portal (P5/P6)
| Gate | Check | Status |
|---|---|---|
| ONB-1 no dead-end | onboarding installer download is real (no alert stub) | ✅ (alert removed; config-driven download, honest fallback) |
| ONB-2 setup-state | portal reflects agent state (enrolled→…→ready) from cloud | ⏳ (TODO — needs a setup-state model) |
| PORT-team | members/invite/roles/revoke + invite-accept live | ✅ (contracts verified; build+guard OK) |
| PORT-reports | recipients CRUD + channel prefs + delivery history | ✅ (contracts verified) |
| PORT-settings | plan/trial + sites + add-site + issue-code | ✅ (contracts verified; wl_sites live) |
| PORT-nav | shared nav across Overview/Reports/Team/Settings | ✅ |
| PORT-incidents | dedicated filterable incident history page | ⏳ (overview shows recent; dedicated page TODO) |

## Installer (P9/P10)
| Gate | Check | Status |
|---|---|---|
| INS-1 NSIS builds | `WatchLog-Setup.exe` produced from `.nsi` | ⏳ |
| INS-2 lifecycle | install→enroll→start; uninstall; reinstall | 🔵 (clean Win VM) |
| INS-3 signed | signtool with a real cert | 🔵 (cert) |

## Platform (P12/P13/P14/P15)
| Gate | Check | Status |
|---|---|---|
| DOM-1 no temp URLs in prod build | grep `sslip.io`/`161.97.175.15` in shipped artifacts = 0 (demo excepted) | ⏳ |
| WP-1 sitemap | `/wp-sitemap.xml` → 200 | ⏳ DEFERRED (minor SEO): WP core sitemap server is configured (index has 1 entry per WP-CLI, providers populated, no plugin/theme override, cache+rewrite flushed) yet web render 404s on WP 7.1 in this env. Not a config error introduced by us; low priority vs portal/billing. |
| CI-1 PR pipeline | Actions runs compile/tests/build/secret-scan | ✅ configured + proven green (run 33429616057); 🔵 hosted runner now quota-blocked (account free Actions minutes exhausted — CLIENT_DEPENDENCIES §8). All steps pass LOCALLY. |
| HLTH-1 healthchecks | portal/site/bridge health endpoints wired in Coolify | ⏳ |

## Field / hardware (P3)
| Gate | Check | Status |
|---|---|---|
| FLD-1 soak >1h | one real recorder, cameras synced, stable >1h | 🔵 (SM-HP access) |
| FLD-2 2×10 cameras | two sites, 10 real cameras each | 🔵 (sites) |
| FLD-3 FP/FN tuning | measured on real labelled footage | 🔵 (footage) |
