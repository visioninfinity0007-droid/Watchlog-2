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
| AI-1 runtime packaged | frozen exe self-test: onnxruntime import OK | ⏳ |
| AI-2 model packaged+loads | `yolov8n.onnx` bundled, session init OK | ⏳ |
| AI-3 inference | self-test inference completes on a synthetic frame | ⏳ |
| AI-4 classes | only person/car/motorcycle retained; others dropped | ⏳ |
| AI-5 fail-open | runtime/model/inference failure → event kept | ✅ (unit 12/12) |

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
| ONB-1 no dead-end | onboarding installer download is real (no alert stub) | ⏳ |
| ONB-2 setup-state | portal reflects agent state (enrolled→…→ready) from cloud | ⏳ |
| PORT-1..N surfaces | overview/sites/incidents/reports/recipients/team/plan/settings live | ⏳ |

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
| WP-1 sitemap | `/wp-sitemap.xml` → 200 | ⏳ |
| CI-1 PR pipeline | Actions runs compile/tests/build/secret-scan | ⏳ |
| HLTH-1 healthchecks | portal/site/bridge health endpoints wired in Coolify | ⏳ |

## Field / hardware (P3)
| Gate | Check | Status |
|---|---|---|
| FLD-1 soak >1h | one real recorder, cameras synced, stable >1h | 🔵 (SM-HP access) |
| FLD-2 2×10 cameras | two sites, 10 real cameras each | 🔵 (sites) |
| FLD-3 FP/FN tuning | measured on real labelled footage | 🔵 (footage) |
