-- =====================================================================
-- 0036 - Billing owner policy execution fix
--
-- 0035 correctly restricted detailed billing rows to Owners, but its RLS
-- policies called wl_is_owner(tenant_id) after revoking EXECUTE on that helper
-- from authenticated. PostgreSQL evaluates policy expressions with the rights
-- of the querying user, so an Owner could receive "permission denied for
-- function wl_is_owner" instead of seeing their own billing rows.
--
-- Rebuild the four read policies using the already customer-facing,
-- authenticated helpers wl_my_tenant() + wl_my_role(). This keeps detailed
-- financial rows Owner-only without exposing a new helper RPC.
-- =====================================================================

drop policy if exists owner_read_billing_customers on public.billing_customers;
create policy owner_read_billing_customers on public.billing_customers
  for select to authenticated
  using (tenant_id = public.wl_my_tenant() and public.wl_my_role() = 'owner');

drop policy if exists owner_read_subscriptions on public.subscriptions;
create policy owner_read_subscriptions on public.subscriptions
  for select to authenticated
  using (tenant_id = public.wl_my_tenant() and public.wl_my_role() = 'owner');

drop policy if exists owner_read_payment_transactions on public.payment_transactions;
create policy owner_read_payment_transactions on public.payment_transactions
  for select to authenticated
  using (tenant_id = public.wl_my_tenant() and public.wl_my_role() = 'owner');

drop policy if exists owner_read_billing_checkouts on public.billing_checkouts;
create policy owner_read_billing_checkouts on public.billing_checkouts
  for select to authenticated
  using (tenant_id = public.wl_my_tenant() and public.wl_my_role() = 'owner');

-- wl_is_owner is no longer part of an RLS expression. Keep it unavailable to
-- client roles; existing database-owner/internal callers are unaffected.
revoke all on function public.wl_is_owner(uuid) from public, anon, authenticated;
