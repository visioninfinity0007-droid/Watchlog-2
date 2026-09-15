-- =====================================================================
-- 0107_ai_evidence_workspace.sql — Phase 6-7: the scoped WatchLog evidence workspace.
--
-- Evidence lives in the canonical database (never a filesystem-as-database). The AI never gets
-- unrestricted tenant storage — only SERVER-AUTHORIZED tenant/site/time/camera/event manifests,
-- reached through two-stage retrieval:
--   1. wl_ai_evidence_index  — compact metadata index/search (NO image bytes);
--   2. wl_ai_evidence_bundle — the full bundle for ONE selected event (event.json + snapshots +
--      detections + coverage + provenance + analysis), decrypted only for the authorized caller.
--
-- Hierarchy expressed on every row: tenant -> site -> date -> camera -> hour -> event(_ref).
-- Payloads are ENCRYPTED AT REST (pgcrypto, key from Supabase Vault; a disposable-DB fallback keeps
-- CI runnable). TTLs are SERVER-SET at write time by evidence class and can NEVER be extended by a
-- model or a tenant — there is no update path for expires_at. Deletion is automatic (cron),
-- idempotent, and audited; the audit survives after the raw evidence is gone.
--
-- Retention tiers (exact):
--   operational_snapshot   7 days
--   harness_snapshot       30 days
--   incident_video         72 hours (48-72h ceiling for NVR-copied video)
--   finding                no TTL here — structured findings/incidents/reports are retained
--                          separately under WatchLog policy (their own tables), never auto-purged here.
--
-- Depends on: sites, wl_my_tenant, wl_assert_my_site (0072), pgcrypto, wl_ai_vault_available (0105).
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- Encryption key. Vault-held in production; a clearly-labelled non-secret fallback keeps a Vault-less
-- disposable database (CI) runnable. Callable ONLY by the SECURITY DEFINER evidence functions
-- (owner), never by anon/authenticated/service directly.
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_evidence_key()
returns text language plpgsql stable security definer set search_path = public as $$
declare v_key text;
begin
  if wl_ai_vault_available() then
    select decrypted_secret into v_key from vault.decrypted_secrets where name = 'ai_evidence_key' limit 1;
  end if;
  -- NOT a production secret: only reached when Vault is absent (the disposable CI database).
  return coalesce(v_key, 'wl-evidence-fallback-key-ci-only-not-secret');
end $$;
revoke all on function public.wl_ai_evidence_key() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------
-- TTL by class (server truth; a model can never widen these).
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_evidence_ttl(p_class text)
returns interval language sql immutable as $$
  select case p_class
    when 'operational_snapshot' then interval '7 days'
    when 'harness_snapshot'     then interval '30 days'
    when 'incident_video'       then interval '72 hours'
    else null                                    -- 'finding' etc. have no TTL in this workspace
  end;
$$;

-- ---------------------------------------------------------------------
-- The evidence store. One row per evidence object; many rows compose one event bundle.
-- ---------------------------------------------------------------------
create table if not exists public.ai_evidence (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references public.tenants(id) on delete cascade,
  site_id        uuid not null references public.sites(id)   on delete cascade,
  camera_id      uuid,                                        -- null = site-level evidence
  event_ref      text not null,                               -- groups objects into one event bundle
  evidence_class text not null check (evidence_class in ('operational_snapshot','harness_snapshot','incident_video','finding')),
  captured_at    timestamptz not null default now(),
  content_type   text,
  payload_enc    bytea,                                       -- pgp_sym_encrypt(base64 text, key); null for meta-only rows
  byte_size      int,
  meta           jsonb not null default '{}'::jsonb,          -- detections/coverage/provenance/analysis (NO bytes)
  expires_at     timestamptz,                                 -- server-set from class; null = policy-retained
  created_at     timestamptz not null default now()
);
create index if not exists ai_evidence_scope_idx  on public.ai_evidence(tenant_id, site_id, captured_at desc);
create index if not exists ai_evidence_cam_idx     on public.ai_evidence(site_id, camera_id, captured_at desc);
create index if not exists ai_evidence_event_idx   on public.ai_evidence(site_id, event_ref);
create index if not exists ai_evidence_expiry_idx  on public.ai_evidence(evidence_class, expires_at);
alter table public.ai_evidence enable row level security;
revoke all on public.ai_evidence from anon, authenticated;

-- Structured deletion audit — retained AFTER the raw evidence is deleted.
create table if not exists public.evidence_deletion_audit (
  id              uuid primary key default gen_random_uuid(),
  run_at          timestamptz not null default now(),
  evidence_class  text not null,
  tenant_id       uuid,
  site_id         uuid,
  deleted_count   int not null,
  oldest_captured timestamptz,
  newest_captured timestamptz,
  reason          text not null default 'ttl_expired'
);
create index if not exists evidence_deletion_audit_idx on public.evidence_deletion_audit(run_at desc);
alter table public.evidence_deletion_audit enable row level security;
revoke all on public.evidence_deletion_audit from anon, authenticated;

-- ---------------------------------------------------------------------
-- WRITE (service_role: the harness/agent path). Server sets expires_at from the class; the caller
-- cannot supply or extend it. Payload is encrypted at rest.
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_evidence_put(
  p_site_id uuid, p_camera_id uuid, p_event_ref text, p_class text,
  p_content_type text, p_payload_b64 text, p_meta jsonb, p_captured_at timestamptz
) returns uuid language plpgsql security definer set search_path = public as $$
declare v_tenant uuid; v_id uuid; v_cap timestamptz := coalesce(p_captured_at, now());
begin
  if auth.role() <> 'service_role' then raise exception 'service role required' using errcode = '42501'; end if;
  select tenant_id into v_tenant from sites where id = p_site_id;
  if v_tenant is null then raise exception 'unknown site' using errcode = '42501'; end if;
  if wl_ai_evidence_ttl(p_class) is null and p_class <> 'finding' then
    raise exception 'unknown evidence class %', p_class;
  end if;
  insert into ai_evidence(tenant_id, site_id, camera_id, event_ref, evidence_class, captured_at,
                          content_type, payload_enc, byte_size, meta, expires_at)
  values (v_tenant, p_site_id, p_camera_id, p_event_ref, p_class, v_cap, p_content_type,
          case when p_payload_b64 is not null and length(p_payload_b64) > 0
               then pgp_sym_encrypt(p_payload_b64, wl_ai_evidence_key()) else null end,
          case when p_payload_b64 is not null then (length(p_payload_b64) * 3) / 4 else null end,
          coalesce(p_meta, '{}'::jsonb),
          case when wl_ai_evidence_ttl(p_class) is not null then v_cap + wl_ai_evidence_ttl(p_class) else null end)
  returning id into v_id;
  return v_id;
end $$;
revoke all on function public.wl_ai_evidence_put(uuid,uuid,text,text,text,text,jsonb,timestamptz) from public, anon, authenticated;
grant execute on function public.wl_ai_evidence_put(uuid,uuid,text,text,text,text,jsonb,timestamptz) to service_role;

-- ---------------------------------------------------------------------
-- STAGE 1 — compact index/search. Tenant/site scoped (wl_assert_my_site raises 42501 for a foreign
-- site). Returns metadata only — NEVER image bytes. Expired rows are excluded.
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_evidence_index(
  p_site_id uuid, p_from timestamptz, p_to timestamptz, p_camera_id uuid default null
) returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id);
begin
  return coalesce((
    select jsonb_agg(jsonb_build_object(
      'id', e.id, 'event_ref', e.event_ref, 'evidence_class', e.evidence_class,
      'captured_at', e.captured_at,
      'date', (e.captured_at at time zone 'UTC')::date,
      'hour', extract(hour from (e.captured_at at time zone 'UTC'))::int,
      'camera_id', e.camera_id, 'content_type', e.content_type, 'byte_size', e.byte_size,
      'has_payload', (e.payload_enc is not null),
      'summary', coalesce(e.meta->'summary', e.meta->'analysis'->'summary'),
      'detections', jsonb_array_length(coalesce(e.meta->'detections','[]'::jsonb)),
      'coverage', e.meta->'coverage'->>'class'
    ) order by e.captured_at desc)
    from ai_evidence e
    where e.site_id = p_site_id and e.tenant_id = v_tenant
      and e.captured_at >= coalesce(p_from, now() - interval '30 days')
      and e.captured_at <= coalesce(p_to, now())
      and (p_camera_id is null or e.camera_id = p_camera_id)
      and (e.expires_at is null or e.expires_at > now())
  ), '[]'::jsonb);
end $$;
revoke all on function public.wl_ai_evidence_index(uuid,timestamptz,timestamptz,uuid) from public, anon;
grant execute on function public.wl_ai_evidence_index(uuid,timestamptz,timestamptz,uuid) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- STAGE 2 — the full bundle for ONE event, scoped by (site, event_ref). A manipulated event_ref that
-- belongs to another site simply returns an empty bundle (its rows are under a different site_id);
-- a foreign site_id is refused by wl_assert_my_site. Decrypts payloads for the authorized caller only.
-- ---------------------------------------------------------------------
create or replace function public.wl_ai_evidence_bundle(p_site_id uuid, p_event_ref text)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id); v_found boolean;
begin
  select exists(select 1 from ai_evidence e
    where e.site_id = p_site_id and e.tenant_id = v_tenant and e.event_ref = p_event_ref
      and (e.expires_at is null or e.expires_at > now())) into v_found;
  if not v_found then
    return jsonb_build_object('event_ref', p_event_ref, 'site_id', p_site_id, 'found', false, 'snapshots', '[]'::jsonb);
  end if;
  return jsonb_build_object(
    'found', true,
    'event', (select jsonb_build_object(
        'event_ref', p_event_ref, 'site_id', p_site_id,
        'cameras', coalesce(jsonb_agg(distinct e.camera_id) filter (where e.camera_id is not null), '[]'::jsonb),
        'captured_from', min(e.captured_at), 'captured_to', max(e.captured_at),
        'classes', coalesce(jsonb_agg(distinct e.evidence_class), '[]'::jsonb))
      from ai_evidence e where e.site_id = p_site_id and e.event_ref = p_event_ref
        and (e.expires_at is null or e.expires_at > now())),
    'snapshots', (select coalesce(jsonb_agg(jsonb_build_object(
        'id', e.id, 'captured_at', e.captured_at, 'camera_id', e.camera_id,
        'evidence_class', e.evidence_class, 'content_type', e.content_type,
        'image_b64', pgp_sym_decrypt(e.payload_enc, wl_ai_evidence_key())) order by e.captured_at), '[]'::jsonb)
      from ai_evidence e where e.site_id = p_site_id and e.event_ref = p_event_ref and e.payload_enc is not null
        and (e.expires_at is null or e.expires_at > now())),
    'detections', (select coalesce(jsonb_agg(e.meta->'detections') filter (where e.meta ? 'detections'), '[]'::jsonb)
      from ai_evidence e where e.site_id = p_site_id and e.event_ref = p_event_ref
        and (e.expires_at is null or e.expires_at > now())),
    'coverage', (select e.meta->'coverage' from ai_evidence e where e.site_id = p_site_id and e.event_ref = p_event_ref
        and e.meta ? 'coverage' and (e.expires_at is null or e.expires_at > now()) limit 1),
    'provenance', (select coalesce(jsonb_agg(jsonb_build_object('id', e.id, 'class', e.evidence_class,
        'captured_at', e.captured_at, 'provenance', e.meta->'provenance')), '[]'::jsonb)
      from ai_evidence e where e.site_id = p_site_id and e.event_ref = p_event_ref
        and (e.expires_at is null or e.expires_at > now())),
    'analysis', (select e.meta->'analysis' from ai_evidence e where e.site_id = p_site_id and e.event_ref = p_event_ref
        and e.meta ? 'analysis' and (e.expires_at is null or e.expires_at > now()) limit 1)
  );
end $$;
revoke all on function public.wl_ai_evidence_bundle(uuid,text) from public, anon;
grant execute on function public.wl_ai_evidence_bundle(uuid,text) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- RETENTION — automatic, idempotent, audited. Per class + site: delete expired, record the batch
-- (only when >0). A re-run finds nothing (retry-safe). Also drains the previously-orphaned
-- incident-still evidence (0058). A model's output can never reach this path.
-- ---------------------------------------------------------------------
create or replace function public.wl_evidence_enforce_retention()
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_grp record; v_n int; v_oldest timestamptz; v_newest timestamptz; v_total int := 0;
begin
  if auth.role() <> 'service_role' then raise exception 'service role required' using errcode = '42501'; end if;
  for v_grp in
    select distinct evidence_class, site_id, tenant_id
    from ai_evidence where expires_at is not null and expires_at <= now()
  loop
    with del as (
      delete from ai_evidence
       where evidence_class = v_grp.evidence_class and site_id = v_grp.site_id
         and expires_at is not null and expires_at <= now()
      returning captured_at
    )
    select count(*), min(captured_at), max(captured_at) into v_n, v_oldest, v_newest from del;
    if v_n > 0 then
      insert into evidence_deletion_audit(evidence_class, tenant_id, site_id, deleted_count, oldest_captured, newest_captured, reason)
        values (v_grp.evidence_class, v_grp.tenant_id, v_grp.site_id, v_n, v_oldest, v_newest, 'ttl_expired');
      v_total := v_total + v_n;
    end if;
  end loop;
  -- Drain the orphaned 0058 incident-still evidence pruner too (best-effort; table may not exist on a
  -- minimal DB, hence the guard).
  begin
    delete from public.operations_incident_evidence where expires_at <= now() and status in ('pending','processing','ready');
  exception when undefined_table then null; end;
  return jsonb_build_object('deleted', v_total, 'ran_at', now());
end $$;
revoke all on function public.wl_evidence_enforce_retention() from public, anon, authenticated;
grant execute on function public.wl_evidence_enforce_retention() to service_role;

-- Schedule it hourly (the 72h video tier is the tightest). Guarded exactly like 0014/0041 — pg_cron
-- may be absent on a disposable database.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname = 'watchlog-evidence-retention';
    perform cron.schedule('watchlog-evidence-retention', '7 * * * *', 'select public.wl_evidence_enforce_retention()');
  end if;
exception when others then
  raise notice 'pg_cron scheduling skipped: %', sqlerrm;
end $$;
