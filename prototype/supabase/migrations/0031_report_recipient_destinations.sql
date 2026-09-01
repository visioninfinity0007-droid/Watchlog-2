-- =====================================================================
-- 0031 - Report recipient destinations + operational authorization
--
-- The original recipient model stored one `destination` even when channel
-- was `both`. The reporter then attempted to send that same value to both
-- WhatsApp and SendGrid. A phone number therefore became an email address.
--
-- Keep `destination` for backward compatibility, but make each transport's
-- address explicit. Existing `both` rows cannot contain a recoverable email,
-- so they are conservatively migrated to WhatsApp-only instead of inventing
-- an address or silently dropping delivery.
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

-- Legacy BOTH rows never stored an email address. Preserve the valid half.
update public.report_recipients
   set channel = 'whatsapp'
 where channel = 'both' and email_destination is null;

alter table public.report_recipients
  drop constraint if exists report_recipients_destinations_chk;
alter table public.report_recipients
  add constraint report_recipients_destinations_chk check (
    (channel = 'whatsapp' and whatsapp_destination is not null and email_destination is null)
    or (channel = 'email' and email_destination is not null and whatsapp_destination is null)
    or (channel = 'both' and whatsapp_destination is not null and email_destination is not null)
  );

-- The old unique index keyed only the overloaded destination field.
drop index if exists report_recipients_unique;
create unique index if not exists report_recipients_unique_v2
  on public.report_recipients(
    tenant_id,
    coalesce(site_id,'00000000-0000-0000-0000-000000000000'::uuid),
    channel,
    coalesce(whatsapp_destination,''),
    coalesce(email_destination,'')
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
  v_id uuid;
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
  end if;
  if p_channel in ('email','both') then
    v_email := lower(btrim(coalesce(p_email,'')));
    if v_email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' then
      raise exception 'enter a valid email address';
    end if;
  end if;

  insert into report_recipients(
    tenant_id,site_id,name,channel,destination,
    whatsapp_destination,email_destination,enabled
  ) values (
    v_tenant,p_site_id,nullif(btrim(coalesce(p_name,'')),''),p_channel,
    coalesce(v_wa,v_email),v_wa,v_email,true
  )
  on conflict do nothing
  returning id into v_id;

  if v_id is null then
    return jsonb_build_object('ok',true,'created',false,'note','that recipient already exists');
  end if;
  return jsonb_build_object(
    'ok',true,'created',true,'id',v_id,'channel',p_channel,
    'whatsapp_destination',v_wa,'email_destination',v_email
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
    ) order by coalesce(s.name,'All sites'),coalesce(r.name,''),r.created_at)
    from report_recipients r left join sites s on s.id=r.site_id
    where r.tenant_id=v_tenant
  ),'[]'::jsonb);
end $$;

-- Harden the existing management RPC; viewer remains read-only.
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
