# WatchLog: instructions for every AI working in this repository

This file is read automatically by ChatGPT/Codex and other agents. `CLAUDE.md`, `GEMINI.md`,
`.github/copilot-instructions.md` and `.cursorrules` point here. It applies to every task, including
code, reports, data fixes, deployments and reviews.

## 1. Route through the AI harness first (always)

Before acting, open **`ai-harness/WATCHLOG.md`** and follow the one row of its "Start here" table that
matches your task. The harness is the product intelligence of WatchLog: rules, customer language,
site types, each tenant's context, and the governed methods for counting, recognition, incidents and
reporting. Do not work from memory or from older copies of these rules.

## 2. Never disclose WatchLog's internal workings to a customer

Anything a customer or tenant can see, in any surface:
- describes what was seen, when, and how certain it is;
- never describes how WatchLog captures, samples, stores, processes or reviews it;
- never names models, AI providers, vendors or infrastructure.

Surfaces covered: chat, reports, WhatsApp/email, portal, website. The exact word list is
`ai-harness/core/customer-vocabulary.yaml`, enforced in the chat, in the vision workers, and in the
database on every report save. Write it correctly at the source anyway.

## 3. Keep the runtime AI aligned with the harness

- Every harness change must be compiled into the runtime AI:
  `python prototype/scripts/compile_harness_brief.py`.
  - It writes the chat brief, the vision-worker rules and the database vocabulary seed.
  - The governance tests fail if any output is stale.
  - After a vocabulary change, apply `prototype/supabase/sql/customer_vocabulary_seed.generated.sql`.
- One tenant never sees another tenant's context, examples or data. Shared harness text names no
  tenant.
- The active tenants and the test/empty sites to ignore are listed in `ai-harness/tenants/README.md`.

## 4. Engineering rules

- Secrets live only in gitignored `.env` files: never in code, commits, logs or chat.
- Migrations:
  - use the next free number in `prototype/supabase/migrations/`, checking open branches too;
  - record each one applied to production in `public.schema_migrations`.
- Cloud-AI permission (full, or text-only) belongs to the tenant; see migrations 0106/0140. Do not
  switch it to clear a backlog.
- Verify against the live database and real HTTP behaviour, not only type checks.

## 5. Operator workspace

If an untracked `AGENTS.local.md` exists next to this file, it is the operator's private workspace
law. Read it after this file and follow it too. It never overrides sections 1-3.
