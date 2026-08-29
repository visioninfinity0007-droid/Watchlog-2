-- =====================================================================
-- Report delivery: who gets the daily summary, and proof it was sent.
--
-- wl_daily_report() already produces the content. What was missing was
-- any notion of a recipient, so the Milestone 1 deliverable "daily
-- WhatsApp event summary" had nowhere to send anything.
--
-- Two tables:
--
--   report_recipients  who should hear from us, per tenant, optionally
--                      narrowed to one site, with a channel preference.
--                      The channel column is also the Milestone 3
--                      "per-tenant channel preference (WhatsApp/email/
--                      both)" - built once here rather than twice.
--
--   report_deliveries  one row per successful or attempted send. Exists
--                      for one reason above all: IDEMPOTENCY. A daily
--                      report runs on a schedule, and schedules get
--                      retried - by cron overlap, by a container restart,
--                      by someone running it by hand to check. Without a
--                      uniqueness rule the customer gets the same summary
--                      three times and stops reading any of it. The
--                      unique index below makes a duplicate send
--                      impossible rather than unlikely.
--
-- Both tables are tenant-scoped, RLS on, readable only through the
-- membership check - the same shape as every other table here, and
-- covered by the isolation gate in prototype/tests.
-- =====================================================================

create table if not exists public.report_recipients (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  -- null means "every site this tenant has", including ones added later.
  site_id      uuid references public.sites(id) on delete cascade,
  name         text,
  channel      text not null default 'whatsapp'
                 check (channel in ('whatsapp', 'email', 'both')),
  -- Digits only for WhatsApp, in international form without '+' or
  -- spaces (923001234567). An email address for email. Checked by the
  -- sender, not here, because the rule differs per channel.
  destination  text not null,
  enabled      boolean not null default true,
  created_at   timestamptz not null default now()
);

-- Uniqueness has to be an INDEX rather than a table constraint, because
-- site_id is nullable and NULLs do not compare equal - without the
-- coalesce, "all sites" recipients could be added over and over.
create unique index if not exists report_recipients_unique
  on public.report_recipients (
    tenant_id,
    coalesce(site_id, '00000000-0000-0000-0000-000000000000'::uuid),
    channel, destination);

create index if not exists report_recipients_tenant_idx
  on public.report_recipients (tenant_id) where enabled;

alter table public.report_recipients enable row level security;

create policy portal_read_recipients on public.report_recipients
  for select using (public.wl_is_member(tenant_id));


create table if not exists public.report_deliveries (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  report_date  date not null,
  channel      text not null,
  destination  text not null,
  status       text not null check (status in ('sent', 'failed', 'skipped')),
  provider_id  text,
  error        text,
  events       int,
  sent_at      timestamptz not null default now()
);

-- The idempotency rule. One successful delivery of one day's report to
-- one destination, ever. Failures are allowed to repeat so a retry can
-- actually retry.
create unique index if not exists report_deliveries_once
  on public.report_deliveries (site_id, report_date, channel, destination)
  where status = 'sent';

create index if not exists report_deliveries_tenant_idx
  on public.report_deliveries (tenant_id, report_date desc);

alter table public.report_deliveries enable row level security;

create policy portal_read_deliveries on public.report_deliveries
  for select using (public.wl_is_member(tenant_id));


-- ---------------------------------------------------------------------
-- Portal management. Tenant-scoped, authenticated only - the caller can
-- only ever touch their own tenant because the tenant id comes from
-- wl_my_tenant() and is never accepted as an argument.
-- ---------------------------------------------------------------------

create or replace function public.wl_add_recipient(
  p_destination text,
  p_channel     text default 'whatsapp',
  p_name        text default null,
  p_site_id     uuid default null)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_id     uuid;
  v_dest   text := btrim(p_destination);
begin
  if v_tenant is null then
    raise exception 'not a member of any tenant';
  end if;
  if p_channel not in ('whatsapp', 'email', 'both') then
    raise exception 'channel must be whatsapp, email or both';
  end if;

  -- A WhatsApp number with punctuation in it fails silently at the
  -- provider, so normalise here rather than discovering it at 7am.
  if p_channel in ('whatsapp', 'both') then
    v_dest := regexp_replace(v_dest, '[^0-9]', '', 'g');
    if length(v_dest) < 10 then
      raise exception 'WhatsApp number must be in international form, digits only, e.g. 923001234567';
    end if;
  elsif position('@' in v_dest) = 0 then
    raise exception 'email address expected';
  end if;

  if p_site_id is not null
     and not exists (select 1 from sites where id = p_site_id and tenant_id = v_tenant) then
    raise exception 'that site does not belong to your account';
  end if;

  insert into report_recipients (tenant_id, site_id, name, channel, destination)
  values (v_tenant, p_site_id, nullif(btrim(coalesce(p_name, '')), ''), p_channel, v_dest)
  on conflict do nothing
  returning id into v_id;

  if v_id is null then
    return jsonb_build_object('ok', true, 'created', false,
                              'note', 'that recipient already exists');
  end if;
  return jsonb_build_object('ok', true, 'created', true, 'id', v_id);
end $$;


create or replace function public.wl_set_recipient(p_id uuid, p_enabled boolean)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_hit    int;
begin
  if v_tenant is null then
    raise exception 'not a member of any tenant';
  end if;
  update report_recipients set enabled = p_enabled
   where id = p_id and tenant_id = v_tenant;
  get diagnostics v_hit = row_count;
  return jsonb_build_object('ok', v_hit > 0);
end $$;


create or replace function public.wl_remove_recipient(p_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_hit    int;
begin
  if v_tenant is null then
    raise exception 'not a member of any tenant';
  end if;
  delete from report_recipients where id = p_id and tenant_id = v_tenant;
  get diagnostics v_hit = row_count;
  return jsonb_build_object('ok', v_hit > 0);
end $$;


create or replace function public.wl_recipients()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then
    return '[]'::jsonb;
  end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'id', r.id, 'name', r.name, 'channel', r.channel,
             'destination', r.destination, 'enabled', r.enabled,
             'site', s.name, 'site_id', r.site_id)
             order by r.created_at)
      from report_recipients r
      left join sites s on s.id = r.site_id
     where r.tenant_id = v_tenant), '[]'::jsonb);
end $$;


-- Recent delivery history, so the portal can show that reports are
-- actually going out rather than assuming they are.
create or replace function public.wl_deliveries(p_days int default 14)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_days   int  := least(greatest(coalesce(p_days, 14), 1), 90);
begin
  if v_tenant is null then
    return '[]'::jsonb;
  end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'date', d.report_date, 'site', s.name, 'channel', d.channel,
             'destination', d.destination, 'status', d.status,
             'events', d.events, 'error', d.error, 'sent_at', d.sent_at)
             order by d.sent_at desc)
      from report_deliveries d
      join sites s on s.id = d.site_id
     where d.tenant_id = v_tenant
       and d.report_date > (current_date - v_days)), '[]'::jsonb);
end $$;


revoke all on function public.wl_add_recipient(text, text, text, uuid) from public, anon;
revoke all on function public.wl_set_recipient(uuid, boolean)          from public, anon;
revoke all on function public.wl_remove_recipient(uuid)                from public, anon;
revoke all on function public.wl_recipients()                          from public, anon;
revoke all on function public.wl_deliveries(int)                       from public, anon;

grant execute on function public.wl_add_recipient(text, text, text, uuid) to authenticated;
grant execute on function public.wl_set_recipient(uuid, boolean)          to authenticated;
grant execute on function public.wl_remove_recipient(uuid)                to authenticated;
grant execute on function public.wl_recipients()                          to authenticated;
grant execute on function public.wl_deliveries(int)                       to authenticated;
