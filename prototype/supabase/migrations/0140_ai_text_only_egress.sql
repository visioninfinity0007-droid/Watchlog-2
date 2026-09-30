-- 0140 — Text-only external AI egress, separate from image egress.
--
-- 0106 introduced one tenant-owned flag, external_egress_allowed, which gates BOTH chat text and
-- image processing (the cloud vision worker and evidence images). A tenant could not let WatchLog's
-- cloud chat model read its site's text context without also releasing camera images.
--
-- This adds a second, narrower consent: external_text_egress_allowed. When true (and full egress is
-- false) the chat may use an external model with TEXT ONLY; evidence images are always withheld and
-- the vision worker is unaffected (it keys on external_egress_allowed alone).
--
-- Same governance as 0106: privacy-first default (false), tenant owner/admin only, only for a site the
-- tenant owns. A platform admin cannot set it on a tenant's behalf; the model cannot set it at all.

alter table public.ai_site_egress_policy
  add column if not exists external_text_egress_allowed boolean not null default false;

comment on column public.ai_site_egress_policy.external_text_egress_allowed is
  'Tenant consent for TEXT-ONLY external AI processing (chat). Images stay local unless external_egress_allowed is also true.';

-- Read: now returns both consent levels. Full egress implies text egress.
create or replace function public.wl_ai_site_egress(p_site_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id); v_allowed boolean; v_text boolean;
begin
  select external_egress_allowed, external_text_egress_allowed into v_allowed, v_text
    from ai_site_egress_policy where site_id = p_site_id;
  return jsonb_build_object(
    'site_id', p_site_id,
    'external_egress_allowed', coalesce(v_allowed, false),
    'external_text_egress_allowed', coalesce(v_allowed, false) or coalesce(v_text, false));
end $$;
revoke all on function public.wl_ai_site_egress(uuid) from public, anon;
grant execute on function public.wl_ai_site_egress(uuid) to authenticated, service_role;

-- Write: tenant owner/admin grants or withdraws text-only consent for their own site.
create or replace function public.wl_ai_set_site_text_egress(p_site_id uuid, p_allowed boolean)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_tenant uuid := wl_require_role(array['owner','admin']);
begin
  if not exists (select 1 from sites where id = p_site_id and tenant_id = v_tenant) then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;
  insert into ai_site_egress_policy(site_id, external_text_egress_allowed, updated_by, updated_at)
  values (p_site_id, coalesce(p_allowed, false), auth.uid(), now())
  on conflict (site_id) do update
    set external_text_egress_allowed = excluded.external_text_egress_allowed,
        updated_by = excluded.updated_by, updated_at = now();
  return jsonb_build_object('site_id', p_site_id, 'external_text_egress_allowed', coalesce(p_allowed, false));
end $$;
revoke all on function public.wl_ai_set_site_text_egress(uuid, boolean) from public, anon;
grant execute on function public.wl_ai_set_site_text_egress(uuid, boolean) to authenticated;
