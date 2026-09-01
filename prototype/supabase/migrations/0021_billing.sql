-- =====================================================================
-- 0021 — Billing (M3), built around a provider boundary.
--
-- SOW invariant D, enforced structurally: a customer can REQUEST a plan and
-- START a checkout, but the authoritative paid state is written ONLY by the
-- payment webhook path. Every billing table is read-only to members (for the
-- portal to SHOW billing); all writes go through SECURITY DEFINER RPCs. The
-- one RPC that activates paid state (wl_billing_apply_event) is granted to
-- NO client role — only the billing webhook service (postgres) calls it, and
-- only after verifying a provider signature.
--
-- Provider-agnostic: 'mock' is the deterministic test/sandbox provider used
-- for E2E and demo; 'switch' is the real gateway (its signing/parsing lives
-- in the billing service's adapter, CLIENT-BLOCKED on Switch's API contract).
--
-- Pricing here is DRAFT/placeholder (client pricing not yet approved) — the
-- billing_plans rows are marked accordingly and must be confirmed before any
-- real charge.
--
-- Rollback: drop the six tables + the wl_billing_* functions.
-- =====================================================================

-- --- pricing config (draft) ------------------------------------------
create table if not exists public.billing_plans (
  plan         text primary key check (plan in ('starter','growth','enterprise')),
  amount_minor int  not null,             -- in the currency's minor unit (paisa)
  currency     text not null default 'PKR',
  interval     text not null default 'month',
  active       boolean not null default true,
  is_draft     boolean not null default true,   -- pricing not client-approved
  updated_at   timestamptz not null default now()
);
insert into public.billing_plans (plan, amount_minor, currency) values
  ('starter',    250000, 'PKR'),
  ('growth',     500000, 'PKR'),
  ('enterprise', 900000, 'PKR')
on conflict (plan) do nothing;

-- --- provider customer mapping ---------------------------------------
create table if not exists public.billing_customers (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants(id) on delete cascade,
  provider              text not null,
  provider_customer_id  text,
  created_at            timestamptz not null default now(),
  unique (tenant_id, provider)
);

-- --- the authoritative subscription ----------------------------------
create table if not exists public.subscriptions (
  id                      uuid primary key default gen_random_uuid(),
  tenant_id               uuid not null references public.tenants(id) on delete cascade,
  provider                text not null,
  provider_subscription_id text,
  plan                    text not null check (plan in ('starter','growth','enterprise')),
  status                  text not null check (status in ('active','past_due','cancelled','expired')),
  current_period_end      timestamptz,
  cancel_at_period_end    boolean not null default false,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  unique (tenant_id)
);

-- --- payment ledger --------------------------------------------------
create table if not exists public.payment_transactions (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  provider         text not null,
  provider_txn_id  text,
  amount_minor     int,
  currency         text default 'PKR',
  status           text not null check (status in ('pending','succeeded','failed','refunded')),
  plan             text,
  description      text,
  created_at       timestamptz not null default now()
);
create index if not exists payment_transactions_tenant_idx
  on public.payment_transactions (tenant_id, created_at desc);

-- --- idempotent webhook ledger ---------------------------------------
create table if not exists public.billing_webhook_events (
  id                 uuid primary key default gen_random_uuid(),
  provider           text not null,
  provider_event_id  text not null,
  event_type         text,
  payload            jsonb,
  signature_valid    boolean not null default false,
  processed_at       timestamptz,
  created_at         timestamptz not null default now(),
  unique (provider, provider_event_id)     -- the idempotency key
);

-- --- checkout sessions -----------------------------------------------
create table if not exists public.billing_checkouts (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references public.tenants(id) on delete cascade,
  provider              text not null,
  provider_checkout_id  text,
  plan                  text not null,
  amount_minor          int,
  currency              text default 'PKR',
  status                text not null default 'created'
                          check (status in ('created','completed','expired','cancelled')),
  created_at            timestamptz not null default now()
);
create index if not exists billing_checkouts_tenant_idx
  on public.billing_checkouts (tenant_id, created_at desc);

-- --- RLS: members may READ their tenant's billing; nobody writes directly
do $$
declare t text;
begin
  foreach t in array array['billing_customers','subscriptions','payment_transactions',
                           'billing_checkouts'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists portal_read_%I on public.%I', t, t);
    execute format('create policy portal_read_%I on public.%I for select to authenticated using (public.wl_is_member(tenant_id))', t, t);
    execute format('revoke insert, update, delete on public.%I from anon, authenticated', t);
  end loop;
  -- webhook ledger is internal only: RLS on, no policy, no client grants.
  execute 'alter table public.billing_webhook_events enable row level security';
  execute 'revoke all on public.billing_webhook_events from anon, authenticated';
  -- plans are public-ish config: readable by authenticated, no writes.
  execute 'alter table public.billing_plans enable row level security';
  execute 'drop policy if exists read_plans on public.billing_plans';
  execute 'create policy read_plans on public.billing_plans for select to authenticated using (true)';
  execute 'revoke insert, update, delete on public.billing_plans from anon, authenticated';
end $$;


-- ---------------------------------------------------------------------
-- RPCs
-- ---------------------------------------------------------------------

-- Active plans + (draft) prices, for the plan picker.
create or replace function public.wl_billing_plans()
returns jsonb language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object(
           'plan', plan, 'amount_minor', amount_minor, 'currency', currency,
           'interval', interval, 'is_draft', is_draft) order by amount_minor)
         , '[]'::jsonb)
    from billing_plans where active;
$$;

-- The caller tenant's billing picture: plan/status/trial + subscription +
-- recent payments. Read-only.
create or replace function public.wl_billing_overview()
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); t tenants;
begin
  if v_tenant is null then return jsonb_build_object('tenant', null); end if;
  select * into t from tenants where id = v_tenant;
  return jsonb_build_object(
    'plan', t.plan,
    'subscription_status', t.subscription_status,
    'requested_plan', t.requested_plan,
    'trial', wl_trial_status(),
    'subscription', (select to_jsonb(s) from subscriptions s where s.tenant_id = v_tenant),
    'transactions', coalesce((select jsonb_agg(jsonb_build_object(
        'amount_minor', p.amount_minor, 'currency', p.currency, 'status', p.status,
        'plan', p.plan, 'description', p.description, 'created_at', p.created_at)
        order by p.created_at desc)
      from payment_transactions p where p.tenant_id = v_tenant), '[]'::jsonb));
end $$;

-- Owner starts a checkout. Records intent + a checkout row; grants NOTHING.
-- Returns a reference the portal turns into a provider checkout URL. Paid
-- state is only reached later, via the verified webhook.
create or replace function public.wl_billing_start_checkout(
  p_plan text, p_provider text default 'mock')
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_tenant uuid := wl_require_role(array['owner']);
  v_price  billing_plans;
  v_id     uuid;
begin
  select * into v_price from billing_plans where plan = p_plan and active;
  if v_price.plan is null then raise exception 'unknown or inactive plan'; end if;
  if p_provider not in ('mock','switch') then raise exception 'unknown provider'; end if;

  insert into billing_checkouts (tenant_id, provider, plan, amount_minor, currency,
                                 provider_checkout_id, status)
  values (v_tenant, p_provider, p_plan, v_price.amount_minor, v_price.currency,
          'chk_' || replace(gen_random_uuid()::text, '-', ''), 'created')
  returning id into v_id;

  update tenants set requested_plan = p_plan where id = v_tenant;

  return jsonb_build_object(
    'ok', true, 'checkout_id', v_id, 'provider', p_provider, 'plan', p_plan,
    'amount_minor', v_price.amount_minor, 'currency', v_price.currency,
    'provider_checkout_id', (select provider_checkout_id from billing_checkouts where id = v_id),
    -- the billing service turns this into a hosted-checkout URL; for 'mock'
    -- it is the sandbox pay page, for 'switch' the real gateway (CLIENT-BLOCKED).
    'checkout_path', '/pay/' || p_provider || '/' ||
                     (select provider_checkout_id from billing_checkouts where id = v_id),
    'note', 'Checkout created. Payment is confirmed by the provider webhook; '
            || 'your plan activates only then.');
end $$;

-- Owner requests cancellation (relinquishing paid access is allowed; it does
-- not grant anything). The provider webhook finalises to 'cancelled'.
create or replace function public.wl_billing_cancel()
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_tenant uuid := wl_require_role(array['owner']); v_hit int;
begin
  update subscriptions set cancel_at_period_end = true, updated_at = now()
   where tenant_id = v_tenant;
  get diagnostics v_hit = row_count;
  return jsonb_build_object('ok', true, 'had_subscription', v_hit > 0,
    'note', 'Cancellation requested; access continues until the period ends.');
end $$;

-- THE authoritative event processor. Called ONLY by the billing webhook
-- service (postgres), after it has verified the provider signature. Idempotent
-- on (provider, provider_event_id). Granted to no client role.
create or replace function public.wl_billing_apply_event(
  p_provider text, p_event_id text, p_event_type text, p_payload jsonb)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_chk    billing_checkouts;
  v_tenant uuid;
  v_plan   text;
  v_amt    int;
  v_cur    text;
  v_new    boolean;
begin
  insert into billing_webhook_events (provider, provider_event_id, event_type,
                                      payload, signature_valid)
  values (p_provider, p_event_id, p_event_type, p_payload, true)
  on conflict (provider, provider_event_id) do nothing;
  get diagnostics v_new = row_count;
  if not v_new then
    return jsonb_build_object('ok', true, 'idempotent', true,
                             'note', 'event already processed');
  end if;

  -- resolve the checkout this event refers to
  if p_payload ? 'provider_checkout_id' then
    select * into v_chk from billing_checkouts
     where provider = p_provider
       and provider_checkout_id = (p_payload->>'provider_checkout_id');
  end if;

  if p_event_type in ('checkout.completed','payment.succeeded') then
    if v_chk.id is null then
      update billing_webhook_events set processed_at = now()
       where provider = p_provider and provider_event_id = p_event_id;
      return jsonb_build_object('ok', false, 'note', 'no matching checkout');
    end if;
    v_tenant := v_chk.tenant_id; v_plan := v_chk.plan;
    v_amt := v_chk.amount_minor; v_cur := v_chk.currency;

    insert into payment_transactions (tenant_id, provider, provider_txn_id,
      amount_minor, currency, status, plan, description)
    values (v_tenant, p_provider, coalesce(p_payload->>'txn_id', p_event_id),
      v_amt, v_cur, 'succeeded', v_plan, 'Subscription payment');

    insert into subscriptions (tenant_id, provider, provider_subscription_id,
      plan, status, current_period_end, cancel_at_period_end, updated_at)
    values (v_tenant, p_provider, coalesce(p_payload->>'subscription_id', 'sub_'||v_chk.provider_checkout_id),
      v_plan, 'active', now() + interval '30 days', false, now())
    on conflict (tenant_id) do update set
      plan = excluded.plan, status = 'active',
      current_period_end = excluded.current_period_end,
      cancel_at_period_end = false, updated_at = now();

    update billing_checkouts set status = 'completed' where id = v_chk.id;
    perform wl_billing_set_subscription(v_tenant, v_plan, 'active');

  elsif p_event_type = 'subscription.past_due' and v_chk.id is not null then
    update subscriptions set status = 'past_due', updated_at = now() where tenant_id = v_chk.tenant_id;
    perform wl_billing_set_subscription(v_chk.tenant_id, v_chk.plan, 'past_due');

  elsif p_event_type = 'subscription.cancelled' and v_chk.id is not null then
    update subscriptions set status = 'cancelled', updated_at = now() where tenant_id = v_chk.tenant_id;
    perform wl_billing_set_subscription(v_chk.tenant_id, 'trial', 'cancelled');
  end if;

  update billing_webhook_events set processed_at = now()
   where provider = p_provider and provider_event_id = p_event_id;
  return jsonb_build_object('ok', true, 'processed', p_event_type,
                           'tenant', v_tenant, 'plan', v_plan);
end $$;

-- grants: reads to authenticated; start_checkout/cancel to authenticated
-- (they self-check owner via wl_require_role). apply_event to NOBODY.
revoke all on function public.wl_billing_plans()               from public, anon;
revoke all on function public.wl_billing_overview()            from public, anon;
revoke all on function public.wl_billing_start_checkout(text,text) from public, anon;
revoke all on function public.wl_billing_cancel()              from public, anon;
revoke all on function public.wl_billing_apply_event(text,text,text,jsonb) from public, anon, authenticated;
grant execute on function public.wl_billing_plans()            to authenticated;
grant execute on function public.wl_billing_overview()         to authenticated;
grant execute on function public.wl_billing_start_checkout(text,text) to authenticated;
grant execute on function public.wl_billing_cancel()           to authenticated;
