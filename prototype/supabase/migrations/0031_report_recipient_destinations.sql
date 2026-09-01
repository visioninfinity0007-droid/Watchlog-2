-- =====================================================================
-- 0031 - Report recipient destinations + operational authorization
--
-- The original model allowed channel='both' with ONE destination. The reporter
-- expands 'both' into WhatsApp + email, so the same value was handed to both
-- providers. That shape cannot represent two different addresses.
--
-- Canonical model after this migration: one report_recipients row = one
-- delivery endpoint. The v2 RPC accepts "both" as a UX convenience but writes
-- TWO rows (one WhatsApp row and one email row). The reporter therefore needs
-- no special migration-time compatibility mode and delivery idempotency remains
-- naturally keyed by channel + destination.
-- =====================================================================

alter table public.report_recipients
  add column if not exists whatsapp_destination text,
  add column if not exists email_destination text;

update public.report_recipients
   set whatsapp_destination = destination
 where channel in ('whatsapp','both') and whatsapp_destination is null;
update public.report_recipients
   set email_destination = lower(btrim(destination))
 where channel = 'email' and email_destination is null;

-- Legacy BOTH rows contain only a phone number because 0011 normalized the
-- overloaded destination as WhatsApp. Preserve that valid endpoint and stop
-- pretending an email address exists.
update public.report_recipients
   set channel = 'whatsapp', email_destination = null
 where channel = 'both';

alter table public.report_recipients
  drop constraint if exists report_recipients_destinations_chk;
alter table public.report_recipients
  add constraint report_recipients_destinations_chk check (
    (channel = 'whatsapp' and whatsapp_destination is not null and email_destination is null)
    or (channel = 'email' and email_destination is not null and whatsapp_destination is null)
  );

-- Existing rows can keep the legacy non-null `destination`; the transport-
-- specific columns make intent explicit for portal reads and future exports.
drop index if exists report_recipients_unique;
create unique index if not exists report_recipients_unique_v2
  on public.report_recipients(
    tenant_id,
    coalesce(site_id,'00000000-0000-0000-0000-000000000000'::uuid),
    channel,
    destination
  );

create or replace function public.wl_add_recipient_v2(
  p_whatsapp text,
  p_email text,
  p_channel text default 'whatsapp',
  p_name text default null,
  p_site_id uuid default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_wa text;
  v_email text;
  v_wa_id uuid;
  v_email_id uuid;
begin
  if p_channel not in ('whatsapp','email','both') then
    raise exception 'channel must be whatsapp, email or both';
  end if;
  if p_site_id is not null and not exists(
    select 1 from sites where id=p_site_id and tenant_id=v_tenant
  ) then
    raise exception 'site not in your account' using errcode='42501';
  end if;

  if p_channel in ('whatsapp','both') then
    v_wa := regexp_replace(coalesce(p_whatsapp,''),'[^0-9]','','g');
    if length(v_wa) < 10 or length(v_wa) > 15 then
      raise exception 'enter a WhatsApp number with country code';
    end if;
    insert into report_recipients(
      tenant_id,site_id,name,channel,destination,whatsapp_destination,email_destination,enabled
    ) values (
      v_tenant,p_site_id,nullif(btrim(coalesce(p_name,'')),''),'whatsapp',v_wa,v_wa,null,true
    ) on conflict do nothing returning id into v_wa_id;
  end if;

  if p_channel in ('email','both') then
    v_email := lower(btrim(coalesce(p_email,'')));
    if v_email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' then
      raise exception 'enter a valid email address';
    end if;
    insert into report_recipients(
      tenant_id,site_id,name,channel,destination,whatsapp_destination,email_destination,enabled
    ) values (
      v_tenant,p_site_id,nullif(btrim(coalesce(p_name,'')),''),'email',v_email,null,v_email,true
    ) on conflict do nothing returning id into v_email_id;
  end if;

  return jsonb_build_object(
    'ok',true,
    'created',(v_wa_id is not null or v_email_id is not null),
    'whatsapp_id',v_wa_id,'email_id',v_email_id,
    'note',case when v_wa_id is null and v_email_id is null then 'those delivery endpoints already exist' else null end
  );
end $$;

-- Preserve the exact 0011 signature for older portal builds. BOTH is rejected
-- because that legacy call can only supply one address.
create or replace function public.wl_add_recipient(
  p_destination text,
  p_channel text default 'whatsapp',
  p_name text default null,
  p_site_id uuid default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_channel = 'both' then
    raise exception 'WhatsApp + Email requires separate destinations; update this client';
  end if;
  if p_channel = 'whatsapp' then
    return wl_add_recipient_v2(p_destination,null,p_channel,p_name,p_site_id);
  end if;
  return wl_add_recipient_v2(null,p_destination,p_channel,p_name,p_site_id);
end $$;

create or replace function public.wl_recipients()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then return '[]'::jsonb; end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
      'id',r.id,'site_id',r.site_id,'site',s.name,'name',r.name,
      'channel',r.channel,'destination',r.destination,
      'whatsapp_destination',r.whatsapp_destination,
      'email_destination',r.email_destination,
      'enabled',r.enabled,'created_at',r.created_at
    ) order by coalesce(s.name,'All sites'),coalesce(r.name,''),r.channel,r.created_at)
    from report_recipients r left join sites s on s.id=r.site_id
    where r.tenant_id=v_tenant
  ),'[]'::jsonb);
end $$;

-- Harden the existing management RPCs: viewer is read-only.
create or replace function public.wl_set_recipient(p_id uuid,p_enabled boolean)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_require_role(array['owner','admin']); v_hit int;
begin
  update report_recipients set enabled=coalesce(p_enabled,false)
   where id=p_id and tenant_id=v_tenant;
  get diagnostics v_hit=row_count;
  return jsonb_build_object('ok',v_hit>0);
end $$;

create or replace function public.wl_remove_recipient(p_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_require_role(array['owner','admin']); v_hit int;
begin
  delete from report_recipients where id=p_id and tenant_id=v_tenant;
  get diagnostics v_hit=row_count;
  return jsonb_build_object('ok',v_hit>0);
end $$;

revoke all on function public.wl_add_recipient_v2(text,text,text,text,uuid) from public,anon;
revoke all on function public.wl_add_recipient(text,text,text,uuid) from public,anon;
revoke all on function public.wl_recipients() from public,anon;
revoke all on function public.wl_set_recipient(uuid,boolean) from public,anon;
revoke all on function public.wl_remove_recipient(uuid) from public,anon;
grant execute on function public.wl_add_recipient_v2(text,text,text,text,uuid) to authenticated;
grant execute on function public.wl_add_recipient(text,text,text,uuid) to authenticated;
grant execute on function public.wl_recipients() to authenticated;
grant execute on function public.wl_set_recipient(uuid,boolean) to authenticated;
grant execute on function public.wl_remove_recipient(uuid) to authenticated;
