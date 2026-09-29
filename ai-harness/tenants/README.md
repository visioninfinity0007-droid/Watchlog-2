# Tenant Registry

This file maps each harness tenant folder to its production site, by name only. Live IDs stay in the
database (`runtime_facts.do_not_duplicate_here`). It was last reconciled against production on
2026-09-28.

## Active tenants: governed here

| Folder | Tenant / site (production names) | Site type | Report profile |
|---|---|---|---|
| `chaiwala-chota-bukhari/` | chaiwala / Chai Wala - Chota Bukhari | restaurant | `chaiwala_restaurant_ops_v1` |
| `hasco-steel-head-office/` | HASCO Steel / Head Office | office | `office_ops_v1` |
| `al-khalid-main-site/` | Al-Khalid Security Services / Main site | office | `office_ops_v1` |

Each active folder must contain:
- `context.yaml`;
- `reporting/README.md`;
- `reporting/methods/visual-snapshot-analysis.md`;
- `reporting/daily-reports/README.md`;
- a dated `reporting/internal-audits/*-harness-compliance.md`.

## Other production sites: never analyse or report as active tenants

These sites exist in production but have no governed context. Never merge their data or names with an
active tenant.

| Tenant / site | Why it is not active |
|---|---|
| Chai Wala / Chai Wala Restaurant | Empty duplicate: no cameras and no agent, but it has a business-context row. It is **not** Chai Wala - Chota Bukhari. |
| Al-Khalid Security Services / HASCO Office | A 3-hour test on 22 Sep 2026 from the Al-Khalid site PC and recorder. It is **not** HASCO Steel's Head Office. |
| AKSS (prototype) / AKSS Head Office (live test) | Prototype, last data 5 Sep 2026. |
| Muhammad / Main site | Empty: no cameras or agent. Not the Al-Khalid Main site. |
| WatchLog Demo (sample data) / Karachi Head Office | Demo sample data. |

## Adding a tenant

Follow `../skills/tenant-intelligence-setup.md`. Add the row above only when its completion gate passes,
and record the audit under the tenant's `reporting/internal-audits/`.
