# Analytics Studio v1 implementation log

This file records concise engineering decisions and verification evidence. It is not a substitute for tests.

## 2026-09-01

- Started implementation from current `main` on branch `product/analytics-studio-v1`.
- Product contract: site type + camera purpose + monitoring rules + local tracking + analytic events + aggregates + portal/reporting.
- Installer remains responsible for hardware discovery and coarse classification; advanced zone/rule configuration belongs in the portal.
- Exact till transaction counting is explicitly excluded from v1 without POS integration.
