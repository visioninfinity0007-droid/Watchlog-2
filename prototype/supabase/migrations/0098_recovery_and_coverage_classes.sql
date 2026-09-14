-- =====================================================================
-- 0098 — Automatic outage recovery ledger + THREE coverage classes (0.4.4 directive §1/§2/§3).
--
-- The core product promise: an Agent/Internet outage causes DELAYED intelligence, not
-- permanently lost intelligence, whenever the NVR kept recording. This adds:
--
--   * recovery_intervals — the recovery-candidate ledger. On reconnect the Agent reports the
--     missed window (last-live -> reconnect) as a PENDING recovery interval; the recovery worker
--     claims it, backfills from the NVR archive in bounded resumable chunks, and marks it
--     recovered / partial / unrecoverable. Provenance is always recorder_archive.
--
--   * wl_site_coverage_report_classes — extends the authority-aware coverage (0091, unchanged)
--     with the three classes the client report must never blend:
--         LIVE MONITORED  — Agent online, processed in real time
--         RECOVERED       — Agent was down, footage later recovered from the NVR
--         UNVERIFIED      — neither live nor recoverable
--     RECOVERED is the part of the UNVERIFIED windows that a recovered interval covers (you can
--     only "recover" time that was unverified), so live + recovered + unverified = wall exactly.
-- =====================================================================

create table if not exists public.recovery_intervals (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  agent_id      uuid references public.agents(id) on delete set null,
  started_at    timestamptz not null,          -- last live observation before the outage
  ended_at      timestamptz not null,          -- Agent reconnect
  status        text not null default 'pending'
                  check (status in ('pending','in_progress','recovered','partial','unrecoverable')),
  source        text not null default 'recorder_archive',
  cameras       uuid[] not null default '{}',
  recovered_count int not null default 0,
  attempts      int not null default 0,
  checkpoint    jsonb not null default '{}'::jsonb,   -- resumable cursor (survives Agent restart)
  detail        jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (site_id, started_at, ended_at)             -- idempotent open (same outage window)
);
create index if not exists recovery_intervals_claim_idx
  on public.recovery_intervals (site_id, status, updated_at);
alter table public.recovery_intervals enable row level security;

-- ---------------------------------------------------------------------
-- Agent: report the missed interval on reconnect. Idempotent (same window ~5s tolerance).
-- Classifies the window initially as a PENDING recovery candidate (UNVERIFIED — recovery pending).
-- ---------------------------------------------------------------------
create or replace function public.wl_open_recovery_interval(
  p_agent_id uuid, p_agent_key text, p_started_at timestamptz, p_ended_at timestamptz,
  p_cameras uuid[] default '{}'
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_agent agents; v_id uuid;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode = '28000'; end if;
  if p_ended_at <= p_started_at then return jsonb_build_object('ok', false, 'reason', 'empty_interval'); end if;
  select id into v_id from recovery_intervals r
   where r.site_id = v_agent.site_id
     and abs(extract(epoch from (r.started_at - p_started_at))) < 5
     and abs(extract(epoch from (r.ended_at   - p_ended_at)))   < 5;
  if v_id is not null then
    return jsonb_build_object('ok', true, 'duplicate', true, 'id', v_id);
  end if;
  insert into recovery_intervals (tenant_id, site_id, agent_id, started_at, ended_at, cameras)
    values (v_agent.tenant_id, v_agent.site_id, v_agent.id, p_started_at, p_ended_at, coalesce(p_cameras,'{}'))
    returning id into v_id;
  return jsonb_build_object('ok', true, 'id', v_id, 'status', 'pending');
end $$;
revoke all on function public.wl_open_recovery_interval(uuid,text,timestamptz,timestamptz,uuid[]) from public;
grant execute on function public.wl_open_recovery_interval(uuid,text,timestamptz,timestamptz,uuid[]) to anon, authenticated;

-- Agent: atomically claim pending (or stale in-progress) recovery intervals for its site.
create or replace function public.wl_agent_claim_recovery(
  p_agent_id uuid, p_agent_key text, p_limit int default 1, p_stale_seconds int default 900
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_agent agents; v_out jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode = '28000'; end if;
  with due as (
    select id from recovery_intervals
     where site_id = v_agent.site_id
       and (status = 'pending'
            or (status = 'in_progress' and updated_at < now() - make_interval(secs => p_stale_seconds)))
     order by ended_at desc                       -- newest / highest-value gaps first
     limit greatest(1, p_limit)
     for update skip locked
  ),
  claimed as (
    update recovery_intervals r set status = 'in_progress', attempts = r.attempts + 1,
           agent_id = v_agent.id, updated_at = now()
      from due where r.id = due.id
    returning r.id, r.started_at, r.ended_at, r.cameras, r.checkpoint, r.attempts
  )
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'started_at', started_at, 'ended_at', ended_at,
           'cameras', to_jsonb(cameras), 'checkpoint', checkpoint, 'attempts', attempts) order by ended_at desc), '[]'::jsonb)
    into v_out from claimed;
  return v_out;
end $$;
revoke all on function public.wl_agent_claim_recovery(uuid,text,int,int) from public;
grant execute on function public.wl_agent_claim_recovery(uuid,text,int,int) to anon, authenticated;

-- Agent: persist recovery progress / completion (resumable — checkpoint survives restart).
create or replace function public.wl_complete_recovery(
  p_agent_id uuid, p_agent_key text, p_id uuid, p_status text,
  p_recovered_count int default 0, p_checkpoint jsonb default '{}'::jsonb, p_detail jsonb default '{}'::jsonb
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_agent agents;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode = '28000'; end if;
  if p_status not in ('pending','in_progress','recovered','partial','unrecoverable') then
    raise exception 'invalid recovery status' using errcode = '22023';
  end if;
  update recovery_intervals r
     set status = p_status,
         recovered_count = greatest(r.recovered_count, coalesce(p_recovered_count,0)),
         checkpoint = coalesce(p_checkpoint, r.checkpoint),
         detail = r.detail || coalesce(p_detail, '{}'::jsonb),
         updated_at = now()
   where r.id = p_id and r.site_id = v_agent.site_id;
  if not found then return jsonb_build_object('ok', false, 'reason', 'not_found'); end if;
  return jsonb_build_object('ok', true, 'id', p_id, 'status', p_status);
end $$;
revoke all on function public.wl_complete_recovery(uuid,text,uuid,text,int,jsonb,jsonb) from public;
grant execute on function public.wl_complete_recovery(uuid,text,uuid,text,int,jsonb,jsonb) to anon, authenticated;

-- ---------------------------------------------------------------------
-- THREE coverage classes. Extends 0091's wl_site_coverage_report (unchanged) by intersecting
-- the recovered recovery_intervals with that report's UNVERIFIED windows.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_coverage_report_classes(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_base jsonb; v_wall numeric; v_unv numeric; v_mon numeric; v_rec numeric; v_a timestamptz; v_b timestamptz;
begin
  v_base := wl_site_coverage_report(p_site_id, p_from, p_to);
  v_a := least(p_from, p_to); v_b := greatest(p_from, p_to);
  v_wall := coalesce((v_base->>'wall_seconds')::numeric, 0);
  v_unv  := coalesce((v_base->>'unverified_seconds')::numeric, 0);
  v_mon  := coalesce((v_base->>'monitored_seconds')::numeric, 0);

  -- recovered = duration of (unverified windows  ∩  recovered recovery intervals), clipped.
  with gaps as (
    select tstzrange((x->>'start')::timestamptz, (x->>'end')::timestamptz, '[)') r
      from jsonb_array_elements(coalesce(v_base->'gaps','[]'::jsonb)) x
     where (x->>'start') is not null and (x->>'end') is not null
       and (x->>'start')::timestamptz < (x->>'end')::timestamptz
  ),
  gmr as (select coalesce(range_agg(r), '{}'::tstzmultirange) rr from gaps),
  rec as (
    select tstzrange(greatest(ri.started_at, v_a), least(ri.ended_at, v_b), '[)') r
      from recovery_intervals ri
     where ri.site_id = p_site_id and ri.status = 'recovered'
       and greatest(ri.started_at, v_a) < least(ri.ended_at, v_b)
  ),
  rmr as (select coalesce(range_agg(r), '{}'::tstzmultirange) rr from rec),
  inter as (select ((select rr from gmr) * (select rr from rmr)) m)
  select coalesce((select sum(extract(epoch from (upper(x) - lower(x))))
                     from (select unnest((select m from inter)) x) z), 0)::numeric
    into v_rec;
  v_rec := least(v_rec, v_unv);   -- can never recover more than was unverified

  return v_base || jsonb_build_object('classes', jsonb_build_object(
    'live_seconds', round(v_mon, 1),
    'recovered_seconds', round(v_rec, 1),
    'unverified_seconds', round(greatest(0, v_unv - v_rec), 1),
    'live_ratio', case when v_wall > 0 then round(v_mon / v_wall, 4) else 1 end,
    'recovered_ratio', case when v_wall > 0 then round(v_rec / v_wall, 4) else 0 end,
    'unverified_ratio', case when v_wall > 0 then round(greatest(0, v_unv - v_rec) / v_wall, 4) else 0 end,
    'total_coverage_ratio', case when v_wall > 0 then round((v_mon + v_rec) / v_wall, 4) else 1 end));
end $$;
revoke all on function public.wl_site_coverage_report_classes(uuid,timestamptz,timestamptz) from public;
grant execute on function public.wl_site_coverage_report_classes(uuid,timestamptz,timestamptz) to authenticated, service_role;
