-- =====================================================================
-- 0032 - Portal operational authorization
--
-- The 0008 portal write RPCs predate the owner/admin/viewer contract added in
-- 0012. They derived the tenant from the caller, which prevented cross-tenant
-- writes, but they did not prevent a tenant Viewer from adding sites or minting
-- enrollment codes by calling the RPC directly.
--
-- Keep the public signatures stable and enforce the same role contract used by
-- Team, Analytics and Reporting: Owner/Admin operate; Viewer is read-only.
-- =====================================================================

create or replace function public.wl_add_site(
  p_name text,
  p_timezone text default 'Asia/Karachi'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_site uuid;
begin
  insert into sites (tenant_id, name, timezone)
  values (
    v_tenant,
    coalesce(nullif(trim(p_name), ''), 'New site'),
    coalesce(nullif(trim(p_timezone), ''), 'Asia/Karachi')
  ) returning id into v_site;

  return jsonb_build_object('site_id', v_site);
end
$$;

create or replace function public.wl_issue_code(
  p_site_id uuid,
  p_days int default 14
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_code text;
begin
  if not exists (
    select 1 from sites s where s.id = p_site_id and s.tenant_id = v_tenant
  ) then
    raise exception 'site not in your account' using errcode='42501';
  end if;

  v_code := 'WL-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 4))
                  || '-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 4));

  insert into enrollment_codes (code, tenant_id, site_id, expires_at)
  values (
    v_code,
    v_tenant,
    p_site_id,
    now() + make_interval(days => least(greatest(coalesce(p_days,14), 1), 90))
  );

  return jsonb_build_object('code', v_code);
end
$$;

revoke all on function public.wl_add_site(text,text) from public,anon;
revoke all on function public.wl_issue_code(uuid,int) from public,anon;
grant execute on function public.wl_add_site(text,text) to authenticated;
grant execute on function public.wl_issue_code(uuid,int) to authenticated;
