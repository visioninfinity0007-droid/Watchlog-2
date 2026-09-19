-- 0109: third text-provider layer, still subject to the same egress gate.
alter table public.ai_modes
  add column if not exists tertiary_provider_id uuid references public.ai_providers(id) on delete set null,
  add column if not exists tertiary_model text;

create or replace function public.wl_ai_resolve_mode(p_mode text,p_needs_vision boolean default false)
returns jsonb language plpgsql security definer set search_path=public as $$
declare v_m ai_modes;
begin
  if auth.role()<>'service_role' then raise exception 'service role required' using errcode='42501'; end if;
  select * into v_m from ai_modes where mode=p_mode;
  if v_m.mode is null then return jsonb_build_object('mode',p_mode,'configured',false); end if;
  return jsonb_build_object(
    'mode',v_m.mode,'configured',(v_m.primary_provider_id is not null),
    'external_egress_allowed',v_m.external_egress_allowed,
    'primary',wl_ai_resolve_provider(v_m.primary_provider_id,v_m.primary_model),
    'fallback',wl_ai_resolve_provider(v_m.fallback_provider_id,v_m.fallback_model),
    'tertiary',wl_ai_resolve_provider(v_m.tertiary_provider_id,v_m.tertiary_model),
    'vision',case when p_needs_vision then wl_ai_resolve_provider(v_m.vision_provider_id,v_m.vision_model) else null end);
end $$;
revoke all on function public.wl_ai_resolve_mode(text,boolean) from public,anon,authenticated;
grant execute on function public.wl_ai_resolve_mode(text,boolean) to service_role;

create or replace function public.wl_ai_mode_set_tertiary(p_mode text,p_provider_id uuid,p_model text,p_reason text)
returns jsonb language plpgsql security definer set search_path=public as $$
begin
  perform wl_platform_require(array['platform_owner']);
  update ai_modes set tertiary_provider_id=p_provider_id,tertiary_model=p_model,
    updated_by=auth.uid(),updated_at=now() where mode=p_mode;
  if not found then raise exception 'unknown mode %',p_mode; end if;
  perform wl_platform_write_audit(null,'ai_mode_set_tertiary',p_reason,null,
    jsonb_build_object('mode',p_mode,'tertiary_provider_id',p_provider_id,'tertiary_model',p_model));
  return jsonb_build_object('ok',true);
end $$;
revoke all on function public.wl_ai_mode_set_tertiary(text,uuid,text,text) from public,anon;
grant execute on function public.wl_ai_mode_set_tertiary(text,uuid,text,text) to authenticated;
