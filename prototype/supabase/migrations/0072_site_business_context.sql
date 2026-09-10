-- =====================================================================
-- 0072 — Site business context + onboarding state (SaaS onboarding foundation, item 3).
--
-- The technical setup state (awaiting_agent -> enrolled -> recorder_connected ->
-- cameras_discovered -> ready) is already DERIVED from what the agent reports (wl_sites,
-- 0020). What was missing is the BUSINESS context the customer supplies — the facts every
-- downstream intelligence layer needs but no recorder can tell us:
--   site type, operating hours, which cameras are entrances / reception / management /
--   restricted / critical, and reporting + notification preferences.
--
-- This context is consumed by: opening/closing (0074), visitor/staff inference (0073),
-- capability-aware Site Control UX, and the daily dataset. It is persisted per site so the
-- customer can leave onboarding and resume; the onboarding checklist COMPOSES the derived
-- technical steps with these persisted business steps.
--
-- All write/read entry points are tenant-guarded (wl_my_tenant). No recorder credential is
-- ever stored here.
-- =====================================================================

create table if not exists public.site_business_context (
  site_id       uuid primary key references public.sites(id) on delete cascade,
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_type     text check (site_type in ('office','retail','factory','restaurant','warehouse','clinic','other')),
  open_time     time,                          -- local operating hours; null = unknown
  close_time    time,
  overnight     boolean not null default false, -- close_time is on the NEXT day (e.g. 20:00-04:00)
  working_days  int[] not null default '{1,2,3,4,5}',   -- ISO dow 1=Mon..7=Sun
  entrance_camera_ids   uuid[] not null default '{}',
  reception_camera_ids  uuid[] not null default '{}',
  management_camera_ids uuid[] not null default '{}',
  critical_camera_ids   uuid[] not null default '{}',
  restricted_purposes   text[] not null default '{armory,restricted,vault,strongroom,safe}',
  reporting_prefs   jsonb not null default '{"daily":true,"weekly":false,"monthly":true}'::jsonb,
  notification_prefs jsonb not null default '{"critical":true,"warning":true,"quiet_hours":null}'::jsonb,
  updated_at    timestamptz not null default now()
);
alter table public.site_business_context enable row level security;

-- Persisted onboarding step state, so a half-finished setup can resume. The DERIVED technical
-- steps live in wl_sites.setup_state; these are the BUSINESS steps the customer completes.
create table if not exists public.site_onboarding (
  site_id     uuid primary key references public.sites(id) on delete cascade,
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  steps       jsonb not null default '{}'::jsonb,  -- {context_captured:true, cameras_mapped:false, recommendation_reviewed:false, approved:false}
  completed_at timestamptz,
  updated_at  timestamptz not null default now()
);
alter table public.site_onboarding enable row level security;

-- ---------------------------------------------------------------------
-- helper: assert the caller owns the site, return tenant (raises otherwise)
-- ---------------------------------------------------------------------
create or replace function public.wl_assert_my_site(p_site_id uuid)
returns uuid
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_owner uuid;
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode = '42501'; end if;
  select tenant_id into v_owner from sites where id = p_site_id;
  if v_owner is null or v_owner <> v_tenant then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;
  return v_tenant;
end $$;
revoke all on function public.wl_assert_my_site(uuid) from public, anon;
grant execute on function public.wl_assert_my_site(uuid) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Read the business context (defaults if none captured yet).
-- ---------------------------------------------------------------------
create or replace function public.wl_my_site_context(p_site_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id); v_ctx site_business_context;
begin
  select * into v_ctx from site_business_context where site_id = p_site_id;
  return jsonb_build_object(
    'site_id', p_site_id,
    'captured', v_ctx.site_id is not null,
    'site_type', v_ctx.site_type,
    'open_time', v_ctx.open_time, 'close_time', v_ctx.close_time, 'overnight', coalesce(v_ctx.overnight,false),
    'working_days', to_jsonb(coalesce(v_ctx.working_days, '{1,2,3,4,5}'::int[])),
    'entrance_camera_ids', to_jsonb(coalesce(v_ctx.entrance_camera_ids, '{}'::uuid[])),
    'reception_camera_ids', to_jsonb(coalesce(v_ctx.reception_camera_ids, '{}'::uuid[])),
    'management_camera_ids', to_jsonb(coalesce(v_ctx.management_camera_ids, '{}'::uuid[])),
    'critical_camera_ids', to_jsonb(coalesce(v_ctx.critical_camera_ids, '{}'::uuid[])),
    'restricted_purposes', to_jsonb(coalesce(v_ctx.restricted_purposes, '{armory,restricted,vault,strongroom,safe}'::text[])),
    'reporting_prefs', coalesce(v_ctx.reporting_prefs, '{"daily":true,"weekly":false,"monthly":true}'::jsonb),
    'notification_prefs', coalesce(v_ctx.notification_prefs, '{"critical":true,"warning":true,"quiet_hours":null}'::jsonb));
end $$;
revoke all on function public.wl_my_site_context(uuid) from public, anon;
grant execute on function public.wl_my_site_context(uuid) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Upsert business context. Camera-id arrays are validated to belong to the site.
-- ---------------------------------------------------------------------
create or replace function public.wl_upsert_site_context(
  p_site_id uuid,
  p_site_type text default null,
  p_open_time time default null,
  p_close_time time default null,
  p_overnight boolean default null,
  p_working_days int[] default null,
  p_entrance_camera_ids uuid[] default null,
  p_reception_camera_ids uuid[] default null,
  p_management_camera_ids uuid[] default null,
  p_critical_camera_ids uuid[] default null,
  p_restricted_purposes text[] default null,
  p_reporting_prefs jsonb default null,
  p_notification_prefs jsonb default null
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id); v_bad int;
begin
  if p_site_type is not null and p_site_type not in ('office','retail','factory','restaurant','warehouse','clinic','other') then
    raise exception 'invalid site_type' using errcode = '22023';
  end if;
  -- Every supplied camera id must belong to this site (no cross-site references).
  select count(*) into v_bad from (
    select unnest(coalesce(p_entrance_camera_ids,'{}') || coalesce(p_reception_camera_ids,'{}')
        || coalesce(p_management_camera_ids,'{}') || coalesce(p_critical_camera_ids,'{}')) as cid
  ) z where cid is not null and not exists (select 1 from cameras c where c.id = z.cid and c.site_id = p_site_id);
  if v_bad > 0 then raise exception 'camera id does not belong to this site' using errcode = '22023'; end if;

  insert into site_business_context as b (site_id, tenant_id, site_type, open_time, close_time, overnight,
      working_days, entrance_camera_ids, reception_camera_ids, management_camera_ids, critical_camera_ids,
      restricted_purposes, reporting_prefs, notification_prefs, updated_at)
  values (p_site_id, v_tenant, p_site_type, p_open_time, p_close_time, coalesce(p_overnight,false),
      coalesce(p_working_days,'{1,2,3,4,5}'), coalesce(p_entrance_camera_ids,'{}'),
      coalesce(p_reception_camera_ids,'{}'), coalesce(p_management_camera_ids,'{}'),
      coalesce(p_critical_camera_ids,'{}'),
      coalesce(p_restricted_purposes,'{armory,restricted,vault,strongroom,safe}'),
      coalesce(p_reporting_prefs,'{"daily":true,"weekly":false,"monthly":true}'::jsonb),
      coalesce(p_notification_prefs,'{"critical":true,"warning":true,"quiet_hours":null}'::jsonb), now())
  on conflict (site_id) do update set
      site_type = coalesce(p_site_type, b.site_type),
      open_time = coalesce(p_open_time, b.open_time),
      close_time = coalesce(p_close_time, b.close_time),
      overnight = coalesce(p_overnight, b.overnight),
      working_days = coalesce(p_working_days, b.working_days),
      entrance_camera_ids = coalesce(p_entrance_camera_ids, b.entrance_camera_ids),
      reception_camera_ids = coalesce(p_reception_camera_ids, b.reception_camera_ids),
      management_camera_ids = coalesce(p_management_camera_ids, b.management_camera_ids),
      critical_camera_ids = coalesce(p_critical_camera_ids, b.critical_camera_ids),
      restricted_purposes = coalesce(p_restricted_purposes, b.restricted_purposes),
      reporting_prefs = coalesce(p_reporting_prefs, b.reporting_prefs),
      notification_prefs = coalesce(p_notification_prefs, b.notification_prefs),
      updated_at = now();

  -- Capturing context ticks its onboarding step.
  insert into site_onboarding (site_id, tenant_id, steps, updated_at)
  values (p_site_id, v_tenant, jsonb_build_object('context_captured', true), now())
  on conflict (site_id) do update set steps = site_onboarding.steps || jsonb_build_object('context_captured', true),
                                      updated_at = now();
  return jsonb_build_object('ok', true, 'site_id', p_site_id);
end $$;
revoke all on function public.wl_upsert_site_context(uuid,text,time,time,boolean,int[],uuid[],uuid[],uuid[],uuid[],text[],jsonb,jsonb) from public, anon;
grant execute on function public.wl_upsert_site_context(uuid,text,time,time,boolean,int[],uuid[],uuid[],uuid[],uuid[],text[],jsonb,jsonb) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Composed onboarding checklist: derived technical steps (from the agent) + persisted
-- business steps. Lets the portal render one resumable progress list.
-- ---------------------------------------------------------------------
create or replace function public.wl_onboarding_status(p_site_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_tenant uuid := wl_assert_my_site(p_site_id);
  v_agents int; v_online boolean; v_has_recorder boolean; v_cams int; v_events int;
  v_ctx boolean; v_steps jsonb;
begin
  select count(*), bool_or(a.last_seen_at > now() - interval '3 min'),
         bool_or(a.device_model is not null or a.device_vendor is not null)
    into v_agents, v_online, v_has_recorder from agents a where a.site_id = p_site_id;
  select count(*) into v_cams from cameras where site_id = p_site_id;
  select count(*) into v_events from events where site_id = p_site_id;
  v_ctx := exists (select 1 from site_business_context where site_id = p_site_id);
  select coalesce(steps,'{}'::jsonb) into v_steps from site_onboarding where site_id = p_site_id;

  return jsonb_build_object(
    'site_id', p_site_id,
    'steps', jsonb_build_array(
      jsonb_build_object('key','connect_agent','label','Connect the site agent','done', v_agents > 0, 'source','derived'),
      jsonb_build_object('key','discover_recorder','label','Recorder identified','done', coalesce(v_has_recorder,false), 'source','derived'),
      jsonb_build_object('key','discover_cameras','label','Cameras discovered','done', v_cams > 0, 'source','derived'),
      jsonb_build_object('key','map_cameras','label','Name & map cameras','done', coalesce((v_steps->>'cameras_mapped')::boolean,false), 'source','business'),
      jsonb_build_object('key','business_context','label','Give business context','done', v_ctx, 'source','business'),
      jsonb_build_object('key','recommendation','label','Review AI recommendation','done', coalesce((v_steps->>'recommendation_reviewed')::boolean,false), 'source','business'),
      jsonb_build_object('key','approval','label','Approve configuration','done', coalesce((v_steps->>'approved')::boolean,false), 'source','business'),
      jsonb_build_object('key','monitoring','label','Monitoring live','done', coalesce(v_online,false) and v_cams > 0 and v_events > 0, 'source','derived')),
    'context_captured', v_ctx,
    'events_flowing', v_events > 0);
end $$;
revoke all on function public.wl_onboarding_status(uuid) from public, anon;
grant execute on function public.wl_onboarding_status(uuid) to authenticated, service_role;

-- Mark a business onboarding step done (resume support).
create or replace function public.wl_onboarding_advance(p_site_id uuid, p_step text, p_done boolean default true)
returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id);
begin
  if p_step not in ('cameras_mapped','recommendation_reviewed','approved','context_captured') then
    raise exception 'unknown onboarding step' using errcode = '22023';
  end if;
  insert into site_onboarding (site_id, tenant_id, steps, updated_at)
  values (p_site_id, v_tenant, jsonb_build_object(p_step, p_done), now())
  on conflict (site_id) do update set steps = site_onboarding.steps || jsonb_build_object(p_step, p_done),
                                      updated_at = now();
  return jsonb_build_object('ok', true, 'step', p_step, 'done', p_done);
end $$;
revoke all on function public.wl_onboarding_advance(uuid,text,boolean) from public, anon;
grant execute on function public.wl_onboarding_advance(uuid,text,boolean) to authenticated, service_role;
