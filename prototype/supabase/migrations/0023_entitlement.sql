-- =====================================================================
-- 0023 — Entitlement: one authoritative answer to "may this tenant get
-- reports right now?", so the promise on the marketing site ("reporting
-- stops when the trial ends") is actually enforced.
--
-- Centralized ON PURPOSE: the reporter, the portal, and any future
-- ingestion path all consult the SAME function, so the rule cannot drift.
--
-- Policy:
--   active                       -> enabled
--   past_due                     -> enabled (documented grace: do not cut a
--                                    security service off mid payment-retry)
--   trialing + trial not expired -> enabled
--   trialing + trial expired     -> DISABLED
--   cancelled / expired          -> DISABLED
--
-- Enforcement gates the COMMERCIAL function (report delivery) only. It never
-- deletes events or snapshots — data keeps flowing and history is preserved,
-- so paying later restores reporting with nothing lost.
--
-- Rollback: drop the two functions; the reporter falls back to sending for
-- every site (its pre-0023 behaviour).
-- =====================================================================

create or replace function public.wl_reporting_enabled(p_tenant uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select case
    when t.subscription_status = 'active'   then true
    when t.subscription_status = 'past_due' then true
    when t.subscription_status = 'trialing'
      then (t.trial_started_at + make_interval(days => t.trial_days)) > now()
    else false
  end
  from tenants t where t.id = p_tenant;
$$;

create or replace function public.wl_entitlement()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v uuid := wl_my_tenant(); t tenants; v_ends timestamptz;
begin
  if v is null then
    return jsonb_build_object('reporting_enabled', false, 'reason', 'no tenant');
  end if;
  select * into t from tenants where id = v;
  v_ends := t.trial_started_at + make_interval(days => t.trial_days);
  return jsonb_build_object(
    'reporting_enabled', wl_reporting_enabled(v),
    'status', t.subscription_status,
    'plan', t.plan,
    'trial_ends_at', v_ends,
    'reason', case
      when t.subscription_status = 'active'   then 'active subscription'
      when t.subscription_status = 'past_due' then 'past due (grace period)'
      when t.subscription_status = 'trialing' and v_ends > now() then 'trial'
      when t.subscription_status = 'trialing' then 'trial expired'
      when t.subscription_status = 'cancelled' then 'subscription cancelled'
      else 'inactive'
    end);
end $$;

revoke all on function public.wl_reporting_enabled(uuid) from public, anon;
revoke all on function public.wl_entitlement()          from public, anon;
grant execute on function public.wl_reporting_enabled(uuid) to authenticated;
grant execute on function public.wl_entitlement()          to authenticated;
