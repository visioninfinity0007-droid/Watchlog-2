-- =====================================================================
-- 0084 — Durable delivery outbox (final integration pass, item 8).
--
-- The sent-row dedupe in report_deliveries (0011) prevents re-sending a report that already
-- recorded 'sent'. It does NOT cover the classic edge: the provider ACCEPTS the WhatsApp
-- message, then the process/DB dies before 'sent' is persisted. On retry that would duplicate.
--
-- This adds a durable outbox with:
--   * one row per (report, channel, destination) — a stable IDEMPOTENCY KEY (unique),
--   * an atomic CLAIM that also RECLAIMS stale 'sending' rows (the crash edge),
--   * the idempotency key passed to the provider on every attempt, so a provider that honors
--     it dedups the crashed-then-retried send (effective-once).
--
-- HONEST SEMANTIC: delivery is AT-LEAST-ONCE. It is effective-once ONLY when the provider
-- honors the idempotency key. Without provider-side idempotency, a crash between provider-accept
-- and status-persist can still duplicate — we do not claim absolute exactly-once.
-- =====================================================================

create table if not exists public.delivery_outbox (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references public.tenants(id) on delete cascade,
  site_id        uuid not null references public.sites(id) on delete cascade,
  report_id      uuid,                          -- -> report_snapshots.id (nullable for ad-hoc)
  report_date    date,
  channel        text not null,
  destination    text not null,
  idempotency_key text not null,
  status         text not null default 'pending' check (status in ('pending','sending','sent','failed')),
  attempts       int not null default 0,
  provider_message_id text,
  last_error     text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  sent_at        timestamptz,
  unique (idempotency_key)
);
create index if not exists delivery_outbox_claim_idx on public.delivery_outbox (channel, status, updated_at);
alter table public.delivery_outbox enable row level security;

-- Enqueue one delivery (idempotent: a duplicate enqueue returns the existing row).
create or replace function public.wl_outbox_enqueue(
  p_report_id uuid, p_channel text, p_destination text
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_key text; v_tenant uuid; v_site uuid; v_date date; v_id uuid; v_status text;
begin
  select tenant_id, site_id, report_date into v_tenant, v_site, v_date from report_snapshots where id = p_report_id;
  if v_tenant is null then raise exception 'no such report' using errcode = '22023'; end if;
  v_key := encode(sha256((p_report_id::text || ':' || p_channel || ':' || p_destination)::bytea), 'hex');
  insert into delivery_outbox (tenant_id, site_id, report_id, report_date, channel, destination, idempotency_key)
  values (v_tenant, v_site, p_report_id, v_date, p_channel, p_destination, v_key)
  on conflict (idempotency_key) do nothing;
  select id, status into v_id, v_status from delivery_outbox where idempotency_key = v_key;
  return jsonb_build_object('id', v_id, 'idempotency_key', v_key, 'status', v_status);
end $$;
revoke all on function public.wl_outbox_enqueue(uuid,text,text) from public, anon, authenticated;
grant execute on function public.wl_outbox_enqueue(uuid,text,text) to service_role;

-- Atomically claim due deliveries: pending, previously-failed (retry), and STALE 'sending'
-- rows (a crashed attempt), the last being the crash-edge recovery. Returns the idempotency key
-- so the sender re-sends with the SAME key.
create or replace function public.wl_outbox_claim(
  p_channel text, p_limit int default 50, p_stale_seconds int default 300
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_out jsonb;
begin
  with due as (
    select id from delivery_outbox
     where channel = p_channel
       and (status in ('pending','failed')
            or (status = 'sending' and updated_at < now() - make_interval(secs => p_stale_seconds)))
     order by created_at
     limit p_limit
     for update skip locked
  ),
  claimed as (
    update delivery_outbox d set status = 'sending', attempts = d.attempts + 1, updated_at = now()
      from due where d.id = due.id
    returning d.id, d.idempotency_key, d.destination, d.report_id, d.attempts
  )
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'idempotency_key', idempotency_key,
           'destination', destination, 'report_id', report_id, 'attempts', attempts) order by id), '[]'::jsonb)
    into v_out from claimed;
  return v_out;
end $$;
revoke all on function public.wl_outbox_claim(text,int,int) from public, anon, authenticated;
grant execute on function public.wl_outbox_claim(text,int,int) to service_role;

-- Record the outcome of a send attempt.
create or replace function public.wl_outbox_mark(
  p_id uuid, p_ok boolean, p_provider_message_id text default null, p_error text default null
) returns void
language sql volatile security definer set search_path = public as $$
  update delivery_outbox
     set status = case when p_ok then 'sent' else 'failed' end,
         provider_message_id = coalesce(p_provider_message_id, provider_message_id),
         last_error = p_error,
         sent_at = case when p_ok then now() else sent_at end,
         updated_at = now()
   where id = p_id;
$$;
revoke all on function public.wl_outbox_mark(uuid,boolean,text,text) from public, anon, authenticated;
grant execute on function public.wl_outbox_mark(uuid,boolean,text,text) to service_role;
