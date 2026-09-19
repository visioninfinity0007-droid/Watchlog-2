-- =====================================================================
-- 0105 - AI providers, models, modes + SECURE credential storage.
--
-- Makes WatchLog model-independent and admin-configurable WITHOUT deployments:
-- a platform owner adds/edits AI providers (Ollama, OpenAI-compatible, ...) and maps the
-- customer-facing WatchLog modes (Instant / Thinking / Hive) to a provider+model, with fallback.
--
-- SECURITY (non-negotiable, ref task section 3 & 17):
--   * The MODEL is never the security boundary. These are deterministic application controls.
--   * Provider API keys are stored ONLY in Supabase Vault (verified present in prod: supabase_vault
--     0.3.1). The plaintext key is written solely inside owner-gated SECURITY DEFINER RPCs and is
--     NEVER stored in a browser-SELECTable column, NEVER returned to the browser, NEVER logged, and
--     NEVER placed in a prompt. List/read RPCs expose only a masked hint (last 4).
--   * The decrypted key is read server-side ONLY by the service role (the edge function) via
--     wl_ai_resolve_mode. No authenticated/anon path can reach it.
--   * All mutations are platform_owner-gated and audited (redacted) via wl_platform_write_audit.
--
-- Vault is referenced by id only (no FK) so this migration applies cleanly on a plain Postgres
-- test database; the vault.* calls live in plpgsql bodies (deferred resolution) and are guarded by
-- wl_ai_vault_available(). No LLM provider is contacted here — this is configuration + resolution.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------
create table if not exists public.ai_providers (
  id               uuid primary key default gen_random_uuid(),
  name             text not null unique,
  type             text not null check (type in ('ollama','openai_compat','openai','anthropic','gemini')),
  endpoint         text not null,
  default_model    text,
  supports_text    boolean not null default true,
  supports_vision  boolean not null default false,
  supports_tools   boolean not null default false,
  supports_json    boolean not null default true,
  privacy          text not null default 'EXTERNAL' check (privacy in ('LOCAL','EXTERNAL')),
  external_egress  boolean not null default true,
  timeout_ms       integer not null default 35000 check (timeout_ms between 1000 and 300000),
  max_context      integer,
  max_output       integer default 900,
  concurrency      integer not null default 2 check (concurrency between 1 and 64),
  cost_class       text not null default 'standard' check (cost_class in ('free','local','standard','premium')),
  priority         integer not null default 100,
  enabled          boolean not null default false,
  vault_secret_id  uuid,                 -- vault.secrets.id; NULL = no key (e.g. local no-auth Ollama)
  key_hint         text,                 -- masked last-4 only, e.g. '****8F3A'
  health_status    text not null default 'unknown' check (health_status in ('unknown','healthy','degraded','down')),
  last_tested_at   timestamptz,
  last_latency_ms  integer,
  last_error       text,
  created_by       uuid,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create table if not exists public.ai_models (
  id               uuid primary key default gen_random_uuid(),
  provider_id      uuid not null references public.ai_providers(id) on delete cascade,
  model_id         text not null,        -- the provider's model name, e.g. 'qwen3:8b'
  label            text,
  supports_text    boolean not null default true,
  supports_vision  boolean not null default false,
  supports_tools   boolean not null default false,
  supports_json    boolean not null default true,
  context_estimate integer,
  enabled          boolean not null default true,
  created_at       timestamptz not null default now(),
  unique (provider_id, model_id)
);

-- Customer-facing WatchLog modes -> provider/model mapping (admin-configurable; never hard-coded).
create table if not exists public.ai_modes (
  mode                    text primary key check (mode in ('instant','thinking','hive')),
  primary_provider_id     uuid references public.ai_providers(id) on delete set null,
  primary_model           text,
  fallback_provider_id    uuid references public.ai_providers(id) on delete set null,
  fallback_model          text,
  vision_provider_id      uuid references public.ai_providers(id) on delete set null,
  vision_model            text,
  external_egress_allowed boolean not null default false,
  updated_by              uuid,
  updated_at              timestamptz not null default now()
);
insert into public.ai_modes(mode) values ('instant'),('thinking'),('hive')
  on conflict (mode) do nothing;

-- RLS: browser reaches NONE of these directly. All access is via SECURITY DEFINER RPCs.
alter table public.ai_providers enable row level security;
alter table public.ai_models    enable row level security;
alter table public.ai_modes     enable row level security;
revoke all on public.ai_providers from anon, authenticated;
revoke all on public.ai_models    from anon, authenticated;
revoke all on public.ai_modes     from anon, authenticated;

-- ---------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_mask_key(p_key text)
returns text language sql immutable as $$
  select case
    when p_key is null or length(btrim(p_key)) = 0 then null
    when length(p_key) <= 4 then '****'
    else '****' || right(p_key, 4)
  end;
$$;

create or replace function public.wl_ai_vault_available()
returns boolean language sql stable as $$
  select exists (select 1 from pg_namespace where nspname = 'vault')
     and exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname = 'vault' and p.proname = 'create_secret');
$$;

-- ---------------------------------------------------------------------
-- Read (platform_owner): full admin config, keys MASKED.
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
        'vision_provider_id', md.vision_provider_id, 'vision_model', md.vision_model,
        'external_egress_allowed', md.external_egress_allowed) order by md.mode)
      from ai_modes md), '[]'::jsonb),
    'vault_available', wl_ai_vault_available()
  );
end $$;

-- ---------------------------------------------------------------------
-- Mutations (platform_owner, audited). Key handling via Vault only.
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_provider_upsert(
  p_id uuid, p_name text, p_type text, p_endpoint text, p_default_model text,
  p_supports_text boolean, p_supports_vision boolean, p_supports_tools boolean, p_supports_json boolean,
  p_privacy text, p_external_egress boolean, p_timeout_ms integer, p_max_output integer,
  p_priority integer, p_cost_class text, p_api_key text, p_reason text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare v_id uuid; v_secret uuid; v_hint text; v_before jsonb;
begin
  perform wl_platform_require(array['platform_owner']);
  if p_id is not null then
    select to_jsonb(x) - 'vault_secret_id' into v_before from ai_providers x where id = p_id;
  end if;

  -- Secret handling: store ONLY in Vault. A key on a plain-Postgres (no Vault) env is refused.
  if p_api_key is not null and length(btrim(p_api_key)) > 0 then
    if not wl_ai_vault_available() then
      raise exception 'secure secret storage (Vault) is not available in this environment';
    end if;
    select vault_secret_id into v_secret from ai_providers where id = p_id;
    if v_secret is not null then
      perform vault.update_secret(v_secret, btrim(p_api_key));
    else
      v_secret := vault.create_secret(btrim(p_api_key), 'wl_ai_provider_' || coalesce(p_id::text, gen_random_uuid()::text), 'WatchLog AI provider key');
    end if;
    v_hint := wl_ai_mask_key(btrim(p_api_key));
  end if;

  if p_id is null then
    insert into ai_providers(name,type,endpoint,default_model,supports_text,supports_vision,supports_tools,supports_json,
      privacy,external_egress,timeout_ms,max_output,priority,cost_class,vault_secret_id,key_hint,created_by)
    values(p_name,p_type,p_endpoint,p_default_model,coalesce(p_supports_text,true),coalesce(p_supports_vision,false),
      coalesce(p_supports_tools,false),coalesce(p_supports_json,true),coalesce(p_privacy,'EXTERNAL'),
      coalesce(p_external_egress,true),coalesce(p_timeout_ms,35000),coalesce(p_max_output,900),
      coalesce(p_priority,100),coalesce(p_cost_class,'standard'),v_secret,v_hint,auth.uid())
    returning id into v_id;
  else
    update ai_providers set
      name=coalesce(p_name,name), type=coalesce(p_type,type), endpoint=coalesce(p_endpoint,endpoint),
      default_model=coalesce(p_default_model,default_model),
      supports_text=coalesce(p_supports_text,supports_text), supports_vision=coalesce(p_supports_vision,supports_vision),
      supports_tools=coalesce(p_supports_tools,supports_tools), supports_json=coalesce(p_supports_json,supports_json),
      privacy=coalesce(p_privacy,privacy), external_egress=coalesce(p_external_egress,external_egress),
      timeout_ms=coalesce(p_timeout_ms,timeout_ms), max_output=coalesce(p_max_output,max_output),
      priority=coalesce(p_priority,priority), cost_class=coalesce(p_cost_class,cost_class),
      vault_secret_id=coalesce(v_secret,vault_secret_id), key_hint=coalesce(v_hint,key_hint),
      updated_at=now()
    where id=p_id returning id into v_id;
  end if;

  perform wl_platform_write_audit(null, case when p_id is null then 'ai_provider_create' else 'ai_provider_update' end,
    p_reason, v_before,
    (select to_jsonb(x) - 'vault_secret_id' from ai_providers x where id=v_id));  -- redacted: no raw key, no secret id
  return (select to_jsonb(x) - 'vault_secret_id' from ai_providers x where id=v_id);
end $$;

create or replace function public.wl_ai_provider_rotate_key(p_id uuid, p_api_key text, p_reason text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_secret uuid; v_hint text;
begin
  perform wl_platform_require(array['platform_owner']);
  if p_api_key is null or length(btrim(p_api_key)) = 0 then raise exception 'a new key is required'; end if;
  if not wl_ai_vault_available() then raise exception 'secure secret storage (Vault) is not available'; end if;
  select vault_secret_id into v_secret from ai_providers where id = p_id;
  if v_secret is not null then perform vault.update_secret(v_secret, btrim(p_api_key));
  else v_secret := vault.create_secret(btrim(p_api_key), 'wl_ai_provider_' || p_id::text, 'WatchLog AI provider key'); end if;
  v_hint := wl_ai_mask_key(btrim(p_api_key));
  update ai_providers set vault_secret_id=v_secret, key_hint=v_hint, updated_at=now() where id=p_id;
  perform wl_platform_write_audit(null, 'ai_provider_rotate_key', p_reason, null, jsonb_build_object('id',p_id,'key_hint',v_hint));
  return jsonb_build_object('ok', true, 'key_hint', v_hint);
end $$;

create or replace function public.wl_ai_provider_delete(p_id uuid, p_reason text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_secret uuid; v_name text;
begin
  perform wl_platform_require(array['platform_owner']);
  select vault_secret_id, name into v_secret, v_name from ai_providers where id = p_id;
  delete from ai_providers where id = p_id;
  if v_secret is not null and wl_ai_vault_available() then delete from vault.secrets where id = v_secret; end if;
  perform wl_platform_write_audit(null, 'ai_provider_delete', p_reason, jsonb_build_object('id',p_id,'name',v_name), null);
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.wl_ai_provider_set_enabled(p_id uuid, p_enabled boolean, p_reason text)
returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform wl_platform_require(array['platform_owner']);
  update ai_providers set enabled = coalesce(p_enabled,false), updated_at=now() where id = p_id;
  perform wl_platform_write_audit(null, 'ai_provider_set_enabled', p_reason, null, jsonb_build_object('id',p_id,'enabled',p_enabled));
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.wl_ai_model_upsert(
  p_id uuid, p_provider_id uuid, p_model_id text, p_label text,
  p_supports_text boolean, p_supports_vision boolean, p_supports_tools boolean, p_supports_json boolean,
  p_context_estimate integer, p_enabled boolean, p_reason text
) returns jsonb language plpgsql security definer set search_path = public as $$
declare v_id uuid;
begin
  perform wl_platform_require(array['platform_owner']);
  if p_id is null then
    insert into ai_models(provider_id,model_id,label,supports_text,supports_vision,supports_tools,supports_json,context_estimate,enabled)
    values(p_provider_id,p_model_id,p_label,coalesce(p_supports_text,true),coalesce(p_supports_vision,false),
      coalesce(p_supports_tools,false),coalesce(p_supports_json,true),p_context_estimate,coalesce(p_enabled,true))
    on conflict (provider_id,model_id) do update set label=excluded.label, enabled=excluded.enabled,
      supports_vision=excluded.supports_vision, supports_tools=excluded.supports_tools, context_estimate=excluded.context_estimate
    returning id into v_id;
  else
    update ai_models set model_id=coalesce(p_model_id,model_id), label=coalesce(p_label,label),
      supports_text=coalesce(p_supports_text,supports_text), supports_vision=coalesce(p_supports_vision,supports_vision),
      supports_tools=coalesce(p_supports_tools,supports_tools), supports_json=coalesce(p_supports_json,supports_json),
      context_estimate=coalesce(p_context_estimate,context_estimate), enabled=coalesce(p_enabled,enabled)
    where id=p_id returning id into v_id;
  end if;
  perform wl_platform_write_audit(null, 'ai_model_upsert', p_reason, null, jsonb_build_object('id',v_id,'model_id',p_model_id));
  return jsonb_build_object('ok', true, 'id', v_id);
end $$;

create or replace function public.wl_ai_model_delete(p_id uuid, p_reason text)
returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform wl_platform_require(array['platform_owner']);
  delete from ai_models where id = p_id;
  perform wl_platform_write_audit(null, 'ai_model_delete', p_reason, jsonb_build_object('id',p_id), null);
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.wl_ai_mode_set(
  p_mode text, p_primary_provider_id uuid, p_primary_model text, p_fallback_provider_id uuid, p_fallback_model text,
  p_vision_provider_id uuid, p_vision_model text, p_external_egress_allowed boolean, p_reason text
) returns jsonb language plpgsql security definer set search_path = public as $$
begin
  perform wl_platform_require(array['platform_owner']);
  update ai_modes set
    primary_provider_id=p_primary_provider_id, primary_model=p_primary_model,
    fallback_provider_id=p_fallback_provider_id, fallback_model=p_fallback_model,
    vision_provider_id=p_vision_provider_id, vision_model=p_vision_model,
    external_egress_allowed=coalesce(p_external_egress_allowed,false), updated_by=auth.uid(), updated_at=now()
  where mode=p_mode;
  if not found then raise exception 'unknown mode %', p_mode; end if;
  perform wl_platform_write_audit(null, 'ai_mode_set', p_reason, null, jsonb_build_object('mode',p_mode,'primary_provider_id',p_primary_provider_id,'primary_model',p_primary_model));
  return jsonb_build_object('ok', true);
end $$;

-- ---------------------------------------------------------------------
-- Service-role only: health writeback + the resolver (returns DECRYPTED key).
-- These are the ONLY functions that touch the plaintext key, and only the edge function
-- (service_role) can call them. Never granted to anon/authenticated.
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_provider_set_health(p_id uuid, p_status text, p_latency_ms integer, p_error text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if auth.role() <> 'service_role' then raise exception 'service role required' using errcode='42501'; end if;
  update ai_providers set health_status = coalesce(p_status,'unknown'), last_latency_ms=p_latency_ms,
    last_error=left(p_error, 500), last_tested_at=now() where id = p_id;
end $$;

create or replace function public.wl_ai_resolve_provider(p_provider_id uuid, p_model text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_p ai_providers; v_key text;
begin
  if auth.role() <> 'service_role' then raise exception 'service role required' using errcode='42501'; end if;
  if p_provider_id is null then return null; end if;
  select * into v_p from ai_providers where id = p_provider_id and enabled = true;
  if v_p.id is null then return null; end if;
  if v_p.vault_secret_id is not null and wl_ai_vault_available() then
    select decrypted_secret into v_key from vault.decrypted_secrets where id = v_p.vault_secret_id;
  end if;
  return jsonb_build_object(
    'id', v_p.id, 'name', v_p.name, 'type', v_p.type, 'endpoint', v_p.endpoint,
    'model', coalesce(p_model, v_p.default_model),
    'supports_text', v_p.supports_text, 'supports_vision', v_p.supports_vision,
    'supports_tools', v_p.supports_tools, 'supports_json', v_p.supports_json,
    'privacy', v_p.privacy, 'external_egress', v_p.external_egress,
    'timeout_ms', v_p.timeout_ms, 'max_output', v_p.max_output,
    'api_key', v_key   -- DECRYPTED. service-role response only; never reaches the browser.
  );
end $$;

-- Resolve a WatchLog mode -> {primary, fallback, vision} provider configs (with decrypted keys)
-- + the mode's egress policy. service_role only. Router uses this; falls back deterministically
-- when a mode is unconfigured (all null => deterministic guided_fallback in the edge function).
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
    'vision',   case when p_needs_vision then wl_ai_resolve_provider(v_m.vision_provider_id, v_m.vision_model) else null end
  );
end $$;

-- ---------------------------------------------------------------------
-- Grants. Owner RPCs: granted to authenticated (in-body wl_platform_require does the real gating,
-- per the 0029 convention). Service RPCs: service_role only. Nothing to anon.
-- ---------------------------------------------------------------------
revoke all on function public.wl_ai_admin_config() from public, anon;
revoke all on function public.wl_ai_provider_upsert(uuid,text,text,text,text,boolean,boolean,boolean,boolean,text,boolean,integer,integer,integer,text,text,text) from public, anon;
revoke all on function public.wl_ai_provider_rotate_key(uuid,text,text) from public, anon;
revoke all on function public.wl_ai_provider_delete(uuid,text) from public, anon;
revoke all on function public.wl_ai_provider_set_enabled(uuid,boolean,text) from public, anon;
revoke all on function public.wl_ai_model_upsert(uuid,uuid,text,text,boolean,boolean,boolean,boolean,integer,boolean,text) from public, anon;
revoke all on function public.wl_ai_model_delete(uuid,text) from public, anon;
revoke all on function public.wl_ai_mode_set(text,uuid,text,uuid,text,uuid,text,boolean,text) from public, anon;
grant execute on function public.wl_ai_admin_config() to authenticated;
grant execute on function public.wl_ai_provider_upsert(uuid,text,text,text,text,boolean,boolean,boolean,boolean,text,boolean,integer,integer,integer,text,text,text) to authenticated;
grant execute on function public.wl_ai_provider_rotate_key(uuid,text,text) to authenticated;
grant execute on function public.wl_ai_provider_delete(uuid,text) to authenticated;
grant execute on function public.wl_ai_provider_set_enabled(uuid,boolean,text) to authenticated;
grant execute on function public.wl_ai_model_upsert(uuid,uuid,text,text,boolean,boolean,boolean,boolean,integer,boolean,text) to authenticated;
grant execute on function public.wl_ai_model_delete(uuid,text) to authenticated;
grant execute on function public.wl_ai_mode_set(text,uuid,text,uuid,text,uuid,text,boolean,text) to authenticated;

revoke all on function public.wl_ai_provider_set_health(uuid,text,integer,text) from public, anon, authenticated;
revoke all on function public.wl_ai_resolve_provider(uuid,text) from public, anon, authenticated;
revoke all on function public.wl_ai_resolve_mode(text,boolean) from public, anon, authenticated;
grant execute on function public.wl_ai_provider_set_health(uuid,text,integer,text) to service_role;
grant execute on function public.wl_ai_resolve_provider(uuid,text) to service_role;
grant execute on function public.wl_ai_resolve_mode(text,boolean) to service_role;
