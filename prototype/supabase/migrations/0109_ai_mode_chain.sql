-- =====================================================================
-- 0109 - A THIRD provider layer per customer mode.
--
-- 0105 gave each WatchLog mode a primary and a fallback. Field reality (2026-09-16): every free
-- provider tier is rate-capped, so two layers is one outage away from the deterministic floor.
-- This adds an optional TERTIARY layer, so a mode resolves to up to three providers tried in
-- order before the guided fallback answers from verified data.
--
-- SECURITY: unchanged and deliberately so.
--   * The MODEL is still never the security boundary. A third candidate is still subject to the
--     SAME egress gate (site-level AND mode-level) inside the router; a tenant that forbids
--     external processing can no more reach layer 3 than layer 1.
--   * The tertiary key lives in Vault like every other, resolved service-role-side only.
--   * wl_ai_mode_set is left BYTE-IDENTICAL on purpose. The portal admin page calls it with 9
--     arguments; widening that signature (even with defaults) would make an existing 9-arg save
--     silently NULL the third layer. Layer 3 gets its own explicit, audited RPC instead.
-- =====================================================================

alter table public.ai_modes add column if not exists tertiary_provider_id uuid references public.ai_providers(id) on delete set null;
alter table public.ai_modes add column if not exists tertiary_model text;

-- ---------------------------------------------------------------------
-- Owner-gated mutation for the third layer only. Pass a NULL provider to remove it.
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_mode_set_tertiary(
  p_mode text, p_provider_id uuid, p_model text, p_reason text
) returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform wl_platform_require(array['platform_owner']);
  update ai_modes set
    tertiary_provider_id = p_provider_id,
    tertiary_model = p_model,
    updated_by = auth.uid(), updated_at = now()
  where mode = p_mode;
  if not found then raise exception 'unknown mode %', p_mode; end if;
  perform wl_platform_write_audit(null, 'ai_mode_set_tertiary', p_reason, null,
    jsonb_build_object('mode', p_mode, 'tertiary_provider_id', p_provider_id, 'tertiary_model', p_model));
  return jsonb_build_object('ok', true);
end $$;

-- ---------------------------------------------------------------------
-- Resolver: hand the router a third candidate (service_role only, as before).
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_resolve_mode(p_mode text, p_needs_vision boolean default false)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_m ai_modes;
begin
  if auth.role() <> 'service_role' then raise exception 'service role required' using errcode='42501'; end if;
  select * into v_m from ai_modes where mode = p_mode;
  if v_m.mode is null then return jsonb_build_object('mode', p_mode, 'configured', false); end if;
  return jsonb_build_object(
    'mode', v_m.mode, 'configured', (v_m.primary_provider_id is not null),
    'external_egress_allowed', v_m.external_egress_allowed,
    'primary',  wl_ai_resolve_provider(v_m.primary_provider_id,  v_m.primary_model),
    'fallback', wl_ai_resolve_provider(v_m.fallback_provider_id, v_m.fallback_model),
    'tertiary', wl_ai_resolve_provider(v_m.tertiary_provider_id, v_m.tertiary_model),
    'vision',   case when p_needs_vision then wl_ai_resolve_provider(v_m.vision_provider_id, v_m.vision_model) else null end
  );
end $$;

-- ---------------------------------------------------------------------
-- Admin config: surface the third layer (additive; the UI ignores fields it does not know).
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_admin_config()
returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform wl_platform_require(array['platform_owner']);
  return jsonb_build_object(
    'providers', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', p.id, 'name', p.name, 'type', p.type, 'endpoint', p.endpoint,
        'default_model', p.default_model,
        'supports', jsonb_build_object('text',p.supports_text,'vision',p.supports_vision,'tools',p.supports_tools,'json',p.supports_json),
        'privacy', p.privacy, 'external_egress', p.external_egress,
        'timeout_ms', p.timeout_ms, 'max_output', p.max_output, 'concurrency', p.concurrency,
        'cost_class', p.cost_class, 'priority', p.priority, 'enabled', p.enabled,
        'has_key', (p.vault_secret_id is not null), 'key_hint', p.key_hint,
        'health_status', p.health_status, 'last_tested_at', p.last_tested_at,
        'last_latency_ms', p.last_latency_ms, 'last_error', p.last_error,
        'models', coalesce((select jsonb_agg(jsonb_build_object(
            'id', m.id, 'model_id', m.model_id, 'label', m.label, 'enabled', m.enabled,
            'supports', jsonb_build_object('text',m.supports_text,'vision',m.supports_vision,'tools',m.supports_tools,'json',m.supports_json),
            'context_estimate', m.context_estimate) order by m.model_id)
          from ai_models m where m.provider_id = p.id), '[]'::jsonb)
      ) order by p.priority, p.name)
      from ai_providers p), '[]'::jsonb),
    'modes', coalesce((select jsonb_agg(jsonb_build_object(
        'mode', md.mode,
        'primary_provider_id', md.primary_provider_id, 'primary_model', md.primary_model,
        'fallback_provider_id', md.fallback_provider_id, 'fallback_model', md.fallback_model,
        'tertiary_provider_id', md.tertiary_provider_id, 'tertiary_model', md.tertiary_model,
        'vision_provider_id', md.vision_provider_id, 'vision_model', md.vision_model,
        'external_egress_allowed', md.external_egress_allowed) order by md.mode)
      from ai_modes md), '[]'::jsonb),
    'vault_available', wl_ai_vault_available()
  );
end $$;

-- ---------------------------------------------------------------------
-- Grants. Same convention as 0105: owner RPC to authenticated (in-body gate does the work),
-- resolver to service_role only, nothing to anon.
-- ---------------------------------------------------------------------
revoke all on function public.wl_ai_mode_set_tertiary(text,uuid,text,text) from public, anon;
grant execute on function public.wl_ai_mode_set_tertiary(text,uuid,text,text) to authenticated;

revoke all on function public.wl_ai_resolve_mode(text,boolean) from public, anon, authenticated;
grant execute on function public.wl_ai_resolve_mode(text,boolean) to service_role;

revoke all on function public.wl_ai_admin_config() from public, anon;
grant execute on function public.wl_ai_admin_config() to authenticated;
