-- =====================================================================
-- 0017 — Billing authorization (P1, SOW invariant D)
--
-- "Customers NEVER authoritatively mark themselves paid."
--
-- Before this, wl_set_plan let any tenant OWNER set both `plan` and
-- `subscription_status` to any value — so an owner could self-assign
-- 'enterprise' / 'active' with no payment (the audit found the AKSS tenant
-- already sitting at starter/active). `plan` is financially meaningful on
-- its own because it drives snapshot retention (wl_plan_retention_days).
--
-- After this:
--   * wl_set_plan is REQUEST-ONLY. An owner may downgrade their own account
--     to the free tier (giving up paid access), or register intent to
--     upgrade (requested_plan) — but cannot grant themselves a paid plan or
--     a paid subscription_status.
--   * wl_billing_set_subscription is the ONLY authoritative writer of paid
--     state. It is granted to no client role; it is invoked by the Switch
--     webhook path (P8) or by ops via the DB owner, keyed to a verified
--     payment event.
--
-- Existing tenants keep their current plan/status (no data is rewritten).
-- Rollback: the pre-0017 wl_set_plan body is preserved in DECISION_LOG and
-- git history; restoring it is a one-function migration.
-- =====================================================================

-- Non-authoritative "the customer wants this tier" marker the checkout
-- flow reads. Never grants access on its own.
alter table public.tenants
  add column if not exists requested_plan text;
alter table public.tenants drop constraint if exists tenants_requested_plan_check;
alter table public.tenants add constraint tenants_requested_plan_check
  check (requested_plan is null or requested_plan in ('starter','growth','enterprise'));

create or replace function public.wl_set_plan(
  p_plan text, p_status text default null)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_require_role(array['owner']);
begin
  if p_plan not in ('trial','starter','growth','enterprise') then
    raise exception 'unknown plan';
  end if;

  -- Downgrade to the free tier is customer-initiated and allowed: it
  -- removes paid access, it does not grant it.
  if p_plan = 'trial' then
    update tenants
       set plan = 'trial',
           subscription_status = 'cancelled',
           requested_plan = null
     where id = v_tenant;
    return jsonb_build_object('ok', true, 'plan', 'trial',
      'note', 'Downgraded to the free tier.');
  end if;

  -- Paid tier requested: record intent only. p_status is ignored — a
  -- customer cannot move themselves to a paid subscription_status. The plan
  -- activates only when the billing webhook confirms a real payment.
  update tenants set requested_plan = p_plan where id = v_tenant;
  return jsonb_build_object('ok', true, 'requested_plan', p_plan,
    'status', (select subscription_status from tenants where id = v_tenant),
    'note', 'Upgrade requires checkout. Your plan activates once payment is confirmed.');
end $$;

-- The authoritative paid-state writer. Called by the billing webhook (P8)
-- or ops — NEVER by a customer. Granted to no client role.
create or replace function public.wl_billing_set_subscription(
  p_tenant uuid, p_plan text, p_status text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_plan not in ('trial','starter','growth','enterprise') then
    raise exception 'unknown plan';
  end if;
  if p_status not in ('trialing','active','past_due','cancelled','expired') then
    raise exception 'unknown subscription status';
  end if;
  update tenants
     set plan = p_plan,
         subscription_status = p_status,
         requested_plan = null
   where id = p_tenant;
  if not found then
    raise exception 'no such tenant %', p_tenant;
  end if;
  return jsonb_build_object('ok', true, 'tenant', p_tenant,
    'plan', p_plan, 'status', p_status);
end $$;

revoke all on function public.wl_set_plan(text, text) from public, anon;
grant execute on function public.wl_set_plan(text, text) to authenticated;

revoke all on function public.wl_billing_set_subscription(uuid, text, text)
  from public, anon, authenticated;
