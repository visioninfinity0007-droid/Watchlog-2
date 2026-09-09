-- ===================================================================
-- 0051 — Archive / Historical Scan (server foundation).
--
-- Site -> Camera(s) -> date/time range -> Rule/SOP -> Scan: reprocess RECORDED
-- footage with WatchLog rules, OFFLINE. Results are explicitly labeled
-- "Recovered from recorder archive" and kept in their OWN tables, strictly
-- SEPARATE from:
--   * live WatchLog observation           (events / operations_incidents), and
--   * recorder-native historical evidence (incident_clip_requests, 0040).
-- We NEVER pretend WatchLog AI ran live while the agent PC was offline: an
-- archive result carries a recovered timestamp + a mandatory provenance label,
-- and is never written into the live streams.
--
-- The actual retrieval (locate + pull bounded recording locally) and offline
-- analysis are AGENT-side and REQUIRE A FUTURE AGENT RELEASE. This migration is
-- the server model + tenant-safe RPCs that the agent will drive.
-- ===================================================================

create table if not exists public.archive_scans (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  camera_ids    uuid[] not null,
  rule_ids      uuid[],                                   -- null = all enabled rules for the cameras
  from_ts       timestamptz not null,
  to_ts         timestamptz not null,
  status        text not null default 'requested'
                  check (status in ('requested','locating','retrieving','analyzing',
                                    'complete','failed','cancelled')),
  provenance    text not null default 'recorder_archive'
                  check (provenance = 'recorder_archive'),
  requested_by  uuid references auth.users(id) on delete set null,
  requested_at  timestamptz not null default now(),
  started_at    timestamptz,
  completed_at  timestamptz,
  error_message text,
  stats         jsonb not null default '{}'::jsonb,
  constraint archive_scans_range_chk check (to_ts > from_ts),
  -- bounded: a single scan window may not exceed 7 days (retrieval is bounded, never a firehose)
  constraint archive_scans_bound_chk check (to_ts - from_ts <= interval '7 days'),
  constraint archive_scans_cams_chk check (cardinality(camera_ids) between 1 and 64)
);
create index if not exists archive_scans_site_idx on public.archive_scans(site_id, requested_at desc);
create index if not exists archive_scans_tenant_idx on public.archive_scans(tenant_id, requested_at desc);

create table if not exists public.archive_scan_results (
  id            bigint generated always as identity primary key,
  scan_id       uuid not null references public.archive_scans(id) on delete cascade,
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  camera_id     uuid references public.cameras(id) on delete set null,
  result_type   text not null,                            -- the rule primitive / detection
  rule_id       uuid references public.monitoring_rules(id) on delete set null,
  rule_version  int,
  recovered_at  timestamptz not null,                     -- timestamp RECOVERED from the recording
  confidence    numeric(4,3),
  still_evidence jsonb not null default '{}'::jsonb,       -- bounded recovered still (ref)
  clip_ref      jsonb not null default '{}'::jsonb,        -- bounded recovered clip (ref), only where needed
  -- the label is MANDATORY and fixed: an archive result can never masquerade as live
  provenance_label text not null default 'Recovered from recorder archive'
                  check (provenance_label = 'Recovered from recorder archive'),
  detail        jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);
create index if not exists archive_scan_results_scan_idx on public.archive_scan_results(scan_id, recovered_at desc);
create index if not exists archive_scan_results_site_idx on public.archive_scan_results(site_id, recovered_at desc);

-- -------------------------------------------------------------------
-- Customer: request a bounded archive scan (owner/admin, tenant-scoped).
-- -------------------------------------------------------------------
create or replace function public.wl_request_archive_scan(
  p_site_id uuid, p_camera_ids uuid[], p_from timestamptz, p_to timestamptz,
  p_rule_ids uuid[] default null
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_scan   public.archive_scans;
  v_bad    int;
begin
  if p_site_id is null or not exists (select 1 from public.sites where id=p_site_id and tenant_id=v_tenant) then
    raise exception 'site not in your account' using errcode='42501';
  end if;
  if p_camera_ids is null or cardinality(p_camera_ids) = 0 then
    raise exception 'at least one camera is required' using errcode='22023';
  end if;
  -- every camera must belong to this site (no cross-site/tenant scan)
  select count(*) into v_bad from unnest(p_camera_ids) cid
   where not exists (select 1 from public.cameras c where c.id=cid and c.site_id=p_site_id and c.tenant_id=v_tenant);
  if v_bad > 0 then
    raise exception '% camera(s) are not in this site', v_bad using errcode='42501';
  end if;
  if p_to <= p_from then
    raise exception 'to must be after from' using errcode='22023';
  end if;
  if p_to - p_from > interval '7 days' then
    raise exception 'scan window may not exceed 7 days' using errcode='22023';
  end if;

  insert into public.archive_scans (tenant_id, site_id, camera_ids, rule_ids, from_ts, to_ts, requested_by)
  values (v_tenant, p_site_id, p_camera_ids, p_rule_ids, p_from, p_to, auth.uid())
  returning * into v_scan;
  return to_jsonb(v_scan);
end $$;

-- -------------------------------------------------------------------
-- Agent/engine (service_role): advance a scan's status and record recovered
-- results. REQUIRES A FUTURE AGENT RELEASE to actually drive these — the agent
-- does the local retrieval + offline analysis and posts candidates here.
-- -------------------------------------------------------------------
create or replace function public.wl_set_archive_scan_status(
  p_scan_id uuid, p_status text, p_error text default null, p_stats jsonb default null
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_scan public.archive_scans;
begin
  if p_status not in ('requested','locating','retrieving','analyzing','complete','failed','cancelled') then
    raise exception 'invalid status %', p_status using errcode='22023';
  end if;
  update public.archive_scans
     set status = p_status,
         started_at = case when p_status in ('locating','retrieving','analyzing') and started_at is null then now() else started_at end,
         completed_at = case when p_status in ('complete','failed','cancelled') then now() else completed_at end,
         error_message = coalesce(p_error, error_message),
         stats = coalesce(p_stats, stats)
   where id = p_scan_id
   returning * into v_scan;
  if v_scan.id is null then
    raise exception 'archive scan % not found', p_scan_id using errcode='42704';
  end if;
  return to_jsonb(v_scan);
end $$;

create or replace function public.wl_record_archive_result(
  p_scan_id uuid, p_camera_id uuid, p_result_type text, p_recovered_at timestamptz,
  p_confidence numeric default null, p_rule_id uuid default null, p_rule_version int default null,
  p_still jsonb default '{}'::jsonb, p_clip jsonb default '{}'::jsonb, p_detail jsonb default '{}'::jsonb
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_scan public.archive_scans; v_row public.archive_scan_results;
begin
  select * into v_scan from public.archive_scans where id = p_scan_id;
  if v_scan.id is null then
    raise exception 'archive scan % not found', p_scan_id using errcode='42704';
  end if;
  insert into public.archive_scan_results (
    scan_id, tenant_id, site_id, camera_id, result_type, rule_id, rule_version,
    recovered_at, confidence, still_evidence, clip_ref, detail)
  values (
    v_scan.id, v_scan.tenant_id, v_scan.site_id, p_camera_id, p_result_type, p_rule_id, p_rule_version,
    p_recovered_at, p_confidence, coalesce(p_still,'{}'::jsonb), coalesce(p_clip,'{}'::jsonb),
    coalesce(p_detail,'{}'::jsonb))
  returning * into v_row;                       -- provenance_label defaults to the fixed archive label
  return to_jsonb(v_row);
end $$;

-- -------------------------------------------------------------------
-- Customer read model: a scan + its recovered results (tenant-scoped). Every
-- result carries the provenance label so the UI can never show it as live.
-- -------------------------------------------------------------------
create or replace function public.wl_archive_scan(p_scan_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_scan public.archive_scans;
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode='28000'; end if;
  select * into v_scan from public.archive_scans where id = p_scan_id and tenant_id = v_tenant;
  if v_scan.id is null then
    raise exception 'archive scan not found in your account' using errcode='42704';
  end if;
  return jsonb_build_object(
    'scan', to_jsonb(v_scan),
    'results', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', r.id, 'camera_id', r.camera_id, 'result_type', r.result_type,
        'rule_id', r.rule_id, 'rule_version', r.rule_version, 'recovered_at', r.recovered_at,
        'confidence', r.confidence, 'still_evidence', r.still_evidence, 'clip_ref', r.clip_ref,
        'provenance_label', r.provenance_label) order by r.recovered_at desc)
      from public.archive_scan_results r where r.scan_id = v_scan.id), '[]'::jsonb));
end $$;

-- -------------------------------------------------------------------
-- RLS + grants
-- -------------------------------------------------------------------
alter table public.archive_scans        enable row level security;
alter table public.archive_scan_results enable row level security;

drop policy if exists portal_read_archive_scans on public.archive_scans;
create policy portal_read_archive_scans on public.archive_scans
  for select to authenticated using (public.wl_is_member(tenant_id));
drop policy if exists portal_read_archive_scan_results on public.archive_scan_results;
create policy portal_read_archive_scan_results on public.archive_scan_results
  for select to authenticated using (public.wl_is_member(tenant_id));

revoke all on function public.wl_request_archive_scan(uuid,uuid[],timestamptz,timestamptz,uuid[]) from public, anon;
grant execute on function public.wl_request_archive_scan(uuid,uuid[],timestamptz,timestamptz,uuid[]) to authenticated;
revoke all on function public.wl_archive_scan(uuid) from public, anon;
grant execute on function public.wl_archive_scan(uuid) to authenticated;
-- engine entrypoints are service_role only (the agent posts via the service key)
revoke all on function public.wl_set_archive_scan_status(uuid,text,text,jsonb) from public, anon, authenticated;
grant execute on function public.wl_set_archive_scan_status(uuid,text,text,jsonb) to service_role;
revoke all on function public.wl_record_archive_result(uuid,uuid,text,timestamptz,numeric,uuid,int,jsonb,jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.wl_record_archive_result(uuid,uuid,text,timestamptz,numeric,uuid,int,jsonb,jsonb,jsonb) to service_role;
