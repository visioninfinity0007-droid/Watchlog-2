-- =====================================================================
-- 0035 - Billing read authorization
--
-- The role model established in 0012 is explicit:
--   owner  = billing + team + full account control
--   admin  = operational configuration
--   viewer = read-only operational/report visibility
--
-- 0021 correctly made billing WRITES Owner-only, but its read policies used
-- wl_is_member(), so Admin/Viewer could still query provider customer IDs,
-- subscriptions, checkout rows and payment transactions directly through the
-- REST API. Hiding those rows in the UI is not authorization.
--
-- Keep the non-sensitive tenant plan/status visible through wl_trial_status()
-- and entitlement APIs. Restrict detailed financial records to Owner.
-- =====================================================================

create or replace function public.wl_is_owner(p_tenant uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists(
    select 1 from memberships m
     where m.tenant_id = p_tenant
       and m.user_id = auth.uid()
       and m.role = 'owner'
  )
$$;
revoke all on function public.wl_is_owner(uuid) from public, anon, authenticated;

-- Replace member-readable financial policies with owner-only reads.
do $$
declare t text;
begin
  foreach t in array array['billing_customers','subscriptions','payment_transactions','billing_checkouts'] loop
    execute format('drop policy if exists portal_read_%I on public.%I', t, t);
    execute format('drop policy if exists owner_read_%I on public.%I', t, t);
    execute format(
      'create policy owner_read_%I on public.%I for select to authenticated using (public.wl_is_owner(tenant_id))',
      t, t
    );
  end loop;
end $$;

-- Detailed billing picture is owner-only even through SECURITY DEFINER.
create or replace function public.wl_billing_overview()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner']);
  t tenants;
begin
  select * into t from tenants where id = v_tenant;
  return jsonb_build_object(
    'plan', t.plan,
    'subscription_status', t.subscription_status,
    'requested_plan', t.requested_plan,
    'trial', wl_trial_status(),
    'subscription', (select to_jsonb(s) from subscriptions s where s.tenant_id = v_tenant),
    'transactions', coalesce((select jsonb_agg(jsonb_build_object(
        'amount_minor', p.amount_minor,
        'currency', p.currency,
        'status', p.status,
        'plan', p.plan,
        'description', p.description,
        'created_at', p.created_at
      ) order by p.created_at desc)
      from payment_transactions p where p.tenant_id = v_tenant), '[]'::jsonb)
  );
end $$;

revoke all on function public.wl_billing_overview() from public, anon;
grant execute on function public.wl_billing_overview() to authenticated;
