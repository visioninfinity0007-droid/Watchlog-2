-- =====================================================================
-- 0022 — Single-source pricing: align billing DB to the published website.
--
-- The marketing site publishes Starter PKR 6,000/site/mo, Growth PKR
-- 12,000/site/mo, and Enterprise "Talk to us". The 0021 seed used draft
-- placeholders (2,500/5,000/9,000), so the portal's Plan & Billing screen
-- showed different numbers than the website — a contradiction in a
-- whole-product demo.
--
-- This makes the billing plans match the published figures exactly, and
-- makes Enterprise contact-only (inactive => no self-serve checkout, which
-- is what "Talk to us" means). test_pricing_alignment.py fails the build if
-- the website source and these amounts ever diverge again.
--
-- Rollback: restore the 0021 amounts + reactivate enterprise.
-- =====================================================================

update public.billing_plans set amount_minor = 600000,  is_draft = false, updated_at = now() where plan = 'starter';
update public.billing_plans set amount_minor = 1200000, is_draft = false, updated_at = now() where plan = 'growth';
-- Enterprise is quoted, not self-serve. Deactivate so wl_billing_start_checkout
-- refuses it and the portal renders it as "Talk to us".
update public.billing_plans set active = false, is_draft = false, updated_at = now() where plan = 'enterprise';
