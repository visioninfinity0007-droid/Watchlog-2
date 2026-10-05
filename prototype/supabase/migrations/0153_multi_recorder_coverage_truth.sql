-- 0153 - Multi-recorder coverage truth.
--
-- STACKED AFTER 0152. REPO ONLY until separately approved.
--
-- Problem:
--   Legacy WatchLog coverage is Agent/site based. On a multi-recorder site the
--   Agent can remain online while Recorder B is unavailable, so site-only gaps
--   cannot express "only cameras on Recorder B were unverified".
--
-- This migration adds deterministic recorder coverage intervals and a governed
-- camera-impact read model. It does NOT rewrite historical coverage before this
-- migration starts tracking recorder identity.
--
-- Truth rules:
--   * Unknown stays Unknown.
--   * Recorder outage affects only cameras assigned to that recorder.
--   * Recorder-specific RECOVERED never upgrades unrelated cameras.
--   * Historical time before recorder tracking began remains legacy/unavailable.
--   * Existing site-level coverage is preserved as a compatibility signal.

alter table public.recorders
  add column if not exists coverage_tracking_started_at timestamptz;

update public.recorders
   set coverage_tracking_started_at = now()
 where coverage_tracking_started_at is null;

alter table public.recorders
  alter column coverage_tracking_started_at set default now();

alter table public.recorders
  alter column coverage_tracking_started_at set not null;


create table if not exists public.recorder_coverage_intervals (
  id bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  recorder_id uuid not null,
  started_at timestamptz not null,
  ended_at timestamptz null,
  cause text not null default 'unknown',
  source text not null default 'recorder_health',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint recorder_coverage_lineage_fkey
    foreign key (recorder_id,tenant_id,site_id)
    references public.recorders(id,tenant_id,site_id)
    on delete restrict,
  constraint recorder_coverage_positive_window
    check (ended_at is null or ended_at >= started_at)
);

create unique index if not exists recorder_coverage_one_open_idx
  on public.recorder_coverage_intervals(recorder_id)
  where ended_at is null;

create index if not exists recorder_coverage_site_window_idx
  on public.recorder_coverage_intervals(site_id,started_at,ended_at);

alter table public.recorder_coverage_intervals enable row level security;
revoke all on table public.recorder_coverage_intervals
  from public,anon,authenticated,service_role;


-- Owner-only state transition helper. It is called by deterministic DB triggers,
-- never by the model or browser.
create or replace function public.wl_set_recorder_coverage_state(
  p_recorder_id uuid,
  p_tenant_id uuid,
  p_site_id uuid,
  p_verified boolean,
  p_cause text,
  p_at timestamptz default now()
) returns void
language plpgsql
volatile
set search_path = public
as $function$
declare
  v_open public.recorder_coverage_intervals;
  v_at timestamptz := coalesce(p_at,now());
  v_cause text := lower(btrim(coalesce(p_cause,'unknown')));
begin
  if not exists (
    select 1 from public.recorders r
     where r.id=p_recorder_id
       and r.tenant_id=p_tenant_id
       and r.site_id=p_site_id
       and r.is_configured
  ) then
    return;
  end if;

  if v_cause='' then v_cause:='unknown'; end if;

  select * into v_open
    from public.recorder_coverage_intervals
   where recorder_id=p_recorder_id
     and ended_at is null
   order by started_at desc,id desc
   limit 1
   for update;

  if coalesce(p_verified,false) then
    if v_open.id is not null then
      update public.recorder_coverage_intervals
         set ended_at=greatest(v_open.started_at,v_at),
             updated_at=now()
       where id=v_open.id;
    end if;
    return;
  end if;

  if v_open.id is null then
    insert into public.recorder_coverage_intervals(
      tenant_id,site_id,recorder_id,started_at,cause,source
    ) values (
      p_tenant_id,p_site_id,p_recorder_id,v_at,v_cause,'recorder_health'
    );
    return;
  end if;

  if v_open.cause is distinct from v_cause then
    if v_at<=v_open.started_at then
      update public.recorder_coverage_intervals
         set cause=v_cause,updated_at=now()
       where id=v_open.id;
    else
      update public.recorder_coverage_intervals
         set ended_at=v_at,updated_at=now()
       where id=v_open.id;
      insert into public.recorder_coverage_intervals(
        tenant_id,site_id,recorder_id,started_at,cause,source
      ) values (
        p_tenant_id,p_site_id,p_recorder_id,v_at,v_cause,'recorder_health'
      );
    end if;
  end if;
end
$function$;

revoke all on function public.wl_set_recorder_coverage_state(
  uuid,uuid,uuid,boolean,text,timestamptz
) from public,anon,authenticated,service_role;


-- Recorder registry lifecycle:
--   newly configured recorder => UNKNOWN interval immediately;
--   disabled recorder => close any open interval.
create or replace function public.wl_recorder_coverage_registry_trigger()
returns trigger
language plpgsql
set search_path = public
as $function$
begin
  if tg_op='INSERT' then
    if new.is_configured then
      perform public.wl_set_recorder_coverage_state(
        new.id,new.tenant_id,new.site_id,false,'unknown',
        new.coverage_tracking_started_at
      );
    end if;
    return new;
  end if;

  if old.is_configured is distinct from new.is_configured then
    if new.is_configured then
      perform public.wl_set_recorder_coverage_state(
        new.id,new.tenant_id,new.site_id,false,'unknown',now()
      );
    else
      update public.recorder_coverage_intervals
         set ended_at=greatest(started_at,now()),
             updated_at=now()
       where recorder_id=new.id
         and ended_at is null;
    end if;
  end if;
  return new;
end
$function$;

drop trigger if exists recorder_coverage_registry_trg on public.recorders;
create trigger recorder_coverage_registry_trg
after insert or update of is_configured on public.recorders
for each row execute function public.wl_recorder_coverage_registry_trigger();


-- Health changes drive recorder coverage. Reachable + auth-not-failed is verified;
-- false/unknown liveness is unverified. A recorder can therefore fail independently
-- while the site Agent and other recorders remain LIVE.
--
-- Silent health is unknown (MNVR-016): when a report arrives more than the
-- 15-minute freshness window after the recorder's previous report (from any
-- Agent), the silent stretch from previous report + 15 minutes to this report
-- is recorded as a closed 'health_stale' interval, unless an open interval
-- already covers it. The read model also treats the current silent tail as
-- unknown, so a stopped Agent or recorder thread never leaves stale LIVE.
create or replace function public.wl_recorder_health_coverage_trigger()
returns trigger
language plpgsql
set search_path = public
as $function$
declare
  v_verified boolean;
  v_cause text;
  v_at timestamptz := coalesce(new.updated_at,now());
  v_prev_at timestamptz;
  v_stale_from timestamptz;
begin
  select max(h.updated_at)
    into v_prev_at
    from public.recorder_health h
   where h.recorder_id=new.recorder_id
     and h.agent_id<>new.agent_id
     and h.updated_at<v_at;

  if tg_op='UPDATE' then
    v_prev_at := greatest(v_prev_at,old.updated_at);
  end if;

  v_stale_from := v_prev_at+interval '15 minutes';

  if v_stale_from<v_at
     and exists (
       select 1 from public.recorders r
        where r.id=new.recorder_id
          and r.tenant_id=new.tenant_id
          and r.site_id=new.site_id
          and r.is_configured
     )
     and not exists (
       select 1 from public.recorder_coverage_intervals x
        where x.recorder_id=new.recorder_id
          and x.ended_at is null
     )
  then
    insert into public.recorder_coverage_intervals(
      tenant_id,site_id,recorder_id,started_at,ended_at,cause,source
    ) values (
      new.tenant_id,new.site_id,new.recorder_id,
      v_stale_from,v_at,'health_stale','recorder_health_stale'
    );
  end if;

  v_verified := (
    new.nvr_reachable is true
    and new.nvr_auth_ok is true
  );
  v_cause := case
    when new.nvr_reachable is false then coalesce(new.reason_code,'nvr_unreachable')
    when new.nvr_auth_ok is false then coalesce(new.reason_code,'nvr_auth_failed')
    when new.nvr_reachable is null then 'unknown'
    else coalesce(new.reason_code,'unknown')
  end;

  perform public.wl_set_recorder_coverage_state(
    new.recorder_id,new.tenant_id,new.site_id,
    v_verified,v_cause,v_at
  );
  return new;
end
$function$;

drop trigger if exists recorder_health_coverage_trg on public.recorder_health;
create trigger recorder_health_coverage_trg
after insert or update of nvr_reachable,nvr_auth_ok,reason_code,updated_at
on public.recorder_health
for each row execute function public.wl_recorder_health_coverage_trigger();


-- Existing configured recorders start UNKNOWN at the rollout boundary.
insert into public.recorder_coverage_intervals(
  tenant_id,site_id,recorder_id,started_at,cause,source
)
select
  r.tenant_id,r.site_id,r.id,r.coverage_tracking_started_at,
  'unknown','migration_baseline'
from public.recorders r
where r.is_configured
  and not exists (
    select 1 from public.recorder_coverage_intervals x
     where x.recorder_id=r.id
       and x.ended_at is null
  )
on conflict do nothing;

-- If 0147 already has a current health state, immediately apply it at the
-- rollout boundary. This closes UNKNOWN for healthy recorders and keeps an
-- explicit unverified state for unhealthy/unknown recorders.
do $$
declare
  x record;
begin
  for x in
    select distinct on (rh.recorder_id)
      rh.*
    from public.recorder_health rh
    join public.recorders r on r.id=rh.recorder_id and r.is_configured
    order by rh.recorder_id,rh.updated_at desc,rh.agent_id
  loop
    perform public.wl_set_recorder_coverage_state(
      x.recorder_id,x.tenant_id,x.site_id,
      (x.nvr_reachable is true and x.nvr_auth_ok is true),
      case
        when x.nvr_reachable is false then coalesce(x.reason_code,'nvr_unreachable')
        when x.nvr_auth_ok is false then coalesce(x.reason_code,'nvr_auth_failed')
        when x.nvr_reachable is null then 'unknown'
        else coalesce(x.reason_code,'unknown')
      end,
      now()
    );
  end loop;
end
$$;


-- Internal recorder/camera coverage read model.
--
-- Recovered time is camera-scoped (MNVR-045): a 'recovered' recovery interval
-- restores only the cameras listed in recovery_intervals.cameras, never every
-- camera on that recorder. An interval that names no camera restores nothing.
-- Per recorder, wall-clock recovered_seconds is time when every one of its
-- cameras was recovered and unverified_seconds is time when at least one was
-- not; live + recovered + unverified = wall. Camera-time totals are kept in
-- their own *_camera_seconds fields, and coverage_ratio is camera-time.
--
-- A recorder's unverified time is the union of (MNVR-016):
--   * its own recorder_coverage_intervals (health said unreachable, auth
--     failed or unknown; or a past silent stretch recorded by the health
--     trigger);
--   * every site-level gap of wl_site_coverage_report: Agent unreachable
--     (server watchdog), Agent-reported observation gaps (PC asleep, off or
--     restarting), link gaps and time with no authoritative Agent. The site
--     Agent observes every recorder, so these gaps apply to all of them;
--   * the tail after its newest recorder_health report once that report is
--     older than the 15-minute freshness window (the same cut 0152 uses for
--     owner recorder state): silent health is unknown, never stale LIVE.
create or replace function public.wl_site_recorder_coverage_facts(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
with requested as (
  select least(p_from,p_to) requested_a,
         greatest(p_from,p_to) requested_b
),
rec as (
  select
    r.id recorder_id,
    r.display_name,
    r.is_primary,
    r.coverage_tracking_started_at,
    coalesce(c.camera_count,0)::int camera_count
  from public.recorders r
  left join lateral (
    select count(*)::int camera_count
    from public.cameras c
    where c.site_id=r.site_id
      and c.tenant_id=r.tenant_id
      and c.recorder_id=r.id
      and c.is_configured
      and coalesce(c.is_canonical,true)
  ) c on true
  where r.site_id=p_site_id
    and r.is_configured
),
cam as (
  select c.id camera_id,c.recorder_id
  from public.cameras c
  join public.recorders r
    on r.id=c.recorder_id
   and r.tenant_id=c.tenant_id
   and r.site_id=c.site_id
  where c.site_id=p_site_id
    and r.is_configured
    and c.is_configured
    and coalesce(c.is_canonical,true)
),
meta as (
  select
    count(*)::int recorder_count,
    coalesce(sum(camera_count),0)::int camera_count,
    max(coverage_tracking_started_at) common_tracking_start
  from rec
),
lo as (
  select
    q.requested_a,
    q.requested_b,
    greatest(
      q.requested_a,
      coalesce(m.common_tracking_start,q.requested_b)
    ) a,
    q.requested_b b,
    m.recorder_count,
    m.camera_count,
    m.common_tracking_start
  from requested q cross join meta m
),
site_gaps as (
  select
    tstzrange(
      greatest((g->>'start')::timestamptz,l.a),
      least((g->>'end')::timestamptz,l.b),
      '[)'
    ) r,
    coalesce(nullif(g->>'cause',''),'unknown') cause
  from lo l
  cross join lateral jsonb_array_elements(
    case
      when l.a<l.b and l.recorder_count>0 then coalesce(
        public.wl_site_coverage_report(p_site_id,l.a,l.b)->'gaps',
        '[]'::jsonb
      )
      else '[]'::jsonb
    end
  ) g
  where nullif(g->>'start','') is not null
    and nullif(g->>'end','') is not null
    and greatest((g->>'start')::timestamptz,l.a)
        < least((g->>'end')::timestamptz,l.b)
),
health as (
  select h.recorder_id,max(h.updated_at) latest_health_at
  from public.recorder_health h
  join rec r on r.recorder_id=h.recorder_id
  where h.site_id=p_site_id
  group by h.recorder_id
),
sources as (
  select
    x.recorder_id,
    tstzrange(
      greatest(x.started_at,l.a),
      least(coalesce(x.ended_at,l.b),l.b),
      '[)'
    ) r,
    x.cause
  from public.recorder_coverage_intervals x
  join rec r on r.recorder_id=x.recorder_id
  cross join lo l
  where greatest(x.started_at,l.a)
        < least(coalesce(x.ended_at,l.b),l.b)
  union all
  select r.recorder_id,g.r,g.cause
  from rec r cross join site_gaps g
  union all
  select
    h.recorder_id,
    tstzrange(
      greatest(h.latest_health_at+interval '15 minutes',l.a),
      l.b,
      '[)'
    ),
    'health_stale'
  from health h cross join lo l
  where greatest(h.latest_health_at+interval '15 minutes',l.a)<l.b
),
sets0 as (
  select
    r.*,
    l.a,l.b,
    greatest(0,extract(epoch from (l.b-l.a)))::numeric wall_seconds,
    coalesce((
      select range_agg(x.r)
      from sources x
      where x.recorder_id=r.recorder_id
    ),'{}'::tstzmultirange) gap_mr
  from rec r cross join lo l
),
-- Each camera is recovered only by the intervals of its own recorder that
-- name it.
cam_sets0 as (
  select
    c.camera_id,
    c.recorder_id,
    s.gap_mr,
    coalesce((
      select range_agg(
        tstzrange(
          greatest(ri.started_at,s.a),
          least(ri.ended_at,s.b),
          '[)'
        )
      )
      from public.recovery_intervals ri
      where ri.recorder_id=c.recorder_id
        and ri.status='recovered'
        and c.camera_id=any(ri.cameras)
        and greatest(ri.started_at,s.a)<least(ri.ended_at,s.b)
    ),'{}'::tstzmultirange) recovered_mr
  from cam c
  join sets0 s on s.recorder_id=c.recorder_id
),
cam_sets as (
  select
    x.*,
    (x.gap_mr*x.recovered_mr) recovered_gap_mr,
    (x.gap_mr-x.recovered_mr) unverified_mr
  from cam_sets0 x
),
cam_secs as (
  select
    x.camera_id,
    x.recorder_id,
    coalesce((
      select sum(extract(epoch from (upper(u.r)-lower(u.r))))
      from unnest(x.recovered_gap_mr) u(r)
    ),0)::numeric recovered_seconds,
    coalesce((
      select sum(extract(epoch from (upper(u.r)-lower(u.r))))
      from unnest(x.unverified_mr) u(r)
    ),0)::numeric unverified_seconds
  from cam_sets x
),
sets as (
  select
    s.*,
    -- Wall-clock time when at least one camera of the recorder was
    -- unverified. A recorder without configured cameras keeps its raw gap.
    case
      when s.camera_count>0 then coalesce((
        select range_agg(u.r)
        from cam_sets x
        cross join lateral unnest(x.unverified_mr) u(r)
        where x.recorder_id=s.recorder_id
      ),'{}'::tstzmultirange)
      else s.gap_mr
    end unverified_mr,
    coalesce((
      select sum(cs.recovered_seconds)
      from cam_secs cs
      where cs.recorder_id=s.recorder_id
    ),0)::numeric recovered_camera_seconds,
    coalesce((
      select sum(cs.unverified_seconds)
      from cam_secs cs
      where cs.recorder_id=s.recorder_id
    ),0)::numeric unverified_camera_seconds
  from sets0 s
),
per_rec0 as (
  select
    s.*,
    coalesce((
      select sum(extract(epoch from (upper(u.r)-lower(u.r))))
      from unnest(s.gap_mr) u(r)
    ),0)::numeric raw_gap_seconds,
    coalesce((
      select sum(extract(epoch from (upper(u.r)-lower(u.r))))
      from unnest(s.unverified_mr) u(r)
    ),0)::numeric unverified_seconds
  from sets s
),
per_rec as (
  select
    p.*,
    greatest(0,p.raw_gap_seconds-p.unverified_seconds)::numeric recovered_seconds
  from per_rec0 p
),
unv_ranges as (
  select
    x.camera_id,u.r
  from cam_sets x
  cross join lateral unnest(x.unverified_mr) u(r)
),
bounds as (
  select a ts from lo where a<b
  union
  select b from lo where a<b
  union
  select lower(r) from unv_ranges
  union
  select upper(r) from unv_ranges
  union
  select lower(r) from site_gaps
  union
  select upper(r) from site_gaps
),
segments0 as (
  select ts,lead(ts) over(order by ts) nxt
  from (select distinct ts from bounds) b
),
segments as (
  select
    s.ts started_at,
    s.nxt ended_at,
    count(distinct u.camera_id)::int affected_camera_count,
    (
      select g.cause
      from site_gaps g
      where g.r && tstzrange(s.ts,s.nxt,'[)')
      order by g.cause
      limit 1
    ) site_cause
  from segments0 s
  left join unv_ranges u
    on u.r && tstzrange(s.ts,s.nxt,'[)')
  where s.nxt is not null and s.ts<s.nxt
  group by s.ts,s.nxt
),
site_totals as (
  select
    coalesce(sum(p.wall_seconds*p.camera_count),0)::numeric camera_time_seconds,
    coalesce(sum((p.wall_seconds-p.raw_gap_seconds)*p.camera_count),0)::numeric live_camera_seconds,
    coalesce(sum(p.recovered_camera_seconds),0)::numeric recovered_camera_seconds,
    coalesce(sum(p.unverified_camera_seconds),0)::numeric unverified_camera_seconds
  from per_rec p
),
impact as (
  select
    coalesce(sum(extract(epoch from (ended_at-started_at)))
      filter(where affected_camera_count=0),0)::numeric fully_verified_seconds,
    coalesce(sum(extract(epoch from (ended_at-started_at)))
      filter(where affected_camera_count>0
               and affected_camera_count<(select camera_count from lo)),0)::numeric
      partial_unverified_seconds,
    coalesce(sum(extract(epoch from (ended_at-started_at)))
      filter(where affected_camera_count>0
               and affected_camera_count>=(select camera_count from lo)
               and (select camera_count from lo)>0),0)::numeric
      fully_unverified_seconds,
    coalesce(max(affected_camera_count),0)::int max_affected_cameras
  from segments
),
rec_json as (
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'recorder_id',p.recorder_id,
        'name',p.display_name,
        'camera_count',p.camera_count,
        'tracking_started_at',p.coverage_tracking_started_at,
        'wall_seconds',round(p.wall_seconds,1),
        'live_seconds',round(greatest(0,p.wall_seconds-p.raw_gap_seconds),1),
        'recovered_seconds',round(p.recovered_seconds,1),
        'unverified_seconds',round(p.unverified_seconds,1),
        'camera_time_seconds',round(p.wall_seconds*p.camera_count,1),
        'recovered_camera_seconds',round(p.recovered_camera_seconds,1),
        'unverified_camera_seconds',round(p.unverified_camera_seconds,1),
        'coverage_ratio',case
          when p.wall_seconds<=0 then null
          when p.camera_count>0 then round(
            greatest(
              0,
              (p.wall_seconds-p.raw_gap_seconds)*p.camera_count
              +p.recovered_camera_seconds
            )/(p.wall_seconds*p.camera_count),4
          )
          else round(
            greatest(0,p.wall_seconds-p.raw_gap_seconds+p.recovered_seconds)
            /p.wall_seconds,4
          )
        end,
        'gaps',coalesce((
          select jsonb_agg(
            jsonb_build_object(
              'start',lower(u.r),
              'end',upper(u.r),
              'cause',coalesce((
                select x.cause
                from sources x
                where x.recorder_id=p.recorder_id
                  and x.r && u.r
                order by
                  upper(x.r*u.r)-lower(x.r*u.r) desc,
                  lower(x.r) desc,
                  x.cause
                limit 1
              ),'unknown')
            )
            order by lower(u.r)
          )
          from unnest(p.unverified_mr) u(r)
        ),'[]'::jsonb)
      )
      order by p.is_primary desc,p.display_name,p.recorder_id
    ),
    '[]'::jsonb
  ) recorders
  from per_rec p
),
impact_json as (
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'start',started_at,
        'end',ended_at,
        'affected_camera_count',affected_camera_count,
        'total_camera_count',(select camera_count from lo),
        -- Set when the whole site was unobservable (Agent/PC gap), else null:
        -- the window is then a recorder/camera-level gap.
        'cause',site_cause,
        'state',case
          when affected_camera_count=0 then 'verified'
          when affected_camera_count>=(select camera_count from lo)
            and (select camera_count from lo)>0 then 'fully_unverified'
          else 'partial_unverified'
        end
      )
      order by started_at
    ) filter(where affected_camera_count>0),
    '[]'::jsonb
  ) windows
  from segments
)
select jsonb_build_object(
  'enabled',(select recorder_count>0 from lo),
  'schema','multi-recorder-coverage-v1',
  'requested_from',(select requested_a from lo),
  'requested_to',(select requested_b from lo),
  'tracking_started_at',(select common_tracking_start from lo),
  'complete_window',coalesce(
    (select requested_a>=common_tracking_start from lo),
    false
  ),
  'legacy_seconds',round(greatest(
    0,
    extract(epoch from (
      least(
        coalesce((select common_tracking_start from lo),(select requested_b from lo)),
        (select requested_b from lo)
      )-(select requested_a from lo)
    ))
  ),1),
  'tracked_wall_seconds',round(greatest(
    0,extract(epoch from ((select b from lo)-(select a from lo)))
  ),1),
  'recorder_count',(select recorder_count from lo),
  'camera_count',(select camera_count from lo),
  'fully_verified_seconds',round((select fully_verified_seconds from impact),1),
  'partial_unverified_seconds',round((select partial_unverified_seconds from impact),1),
  'fully_unverified_seconds',round((select fully_unverified_seconds from impact),1),
  'any_unverified_seconds',round(
    (select partial_unverified_seconds+fully_unverified_seconds from impact),1
  ),
  'max_affected_cameras',(select max_affected_cameras from impact),
  'camera_time_seconds',round((select camera_time_seconds from site_totals),1),
  'live_camera_seconds',round((select live_camera_seconds from site_totals),1),
  'recovered_camera_seconds',round((select recovered_camera_seconds from site_totals),1),
  'unverified_camera_seconds',round((select unverified_camera_seconds from site_totals),1),
  'camera_coverage_ratio',case
    when (select camera_time_seconds from site_totals)<=0 then null
    else round(
      (
        (select live_camera_seconds from site_totals)
        +(select recovered_camera_seconds from site_totals)
      )/(select camera_time_seconds from site_totals),
      4
    )
  end,
  'recorders',(select recorders from rec_json),
  'impact_windows',(select windows from impact_json),
  'measurement_notes',jsonb_build_array(
    'Recorder coverage is tracked only from tracking_started_at; earlier time is not reconstructed.',
    'A recorder gap makes only cameras assigned to that recorder unverified.',
    'Recovered time counts only for the cameras the recovery names and never upgrades other cameras.',
    'camera_coverage_ratio is camera-time coverage, not a count of events or people.',
    'When the site Agent or PC could not observe, every recorder''s cameras are unverified for that time.',
    'Recorder health older than 15 minutes is unknown, never assumed live.'
  )
)
$function$;

revoke all on function public.wl_site_recorder_coverage_facts(
  uuid,timestamptz,timestamptz
) from public,anon,authenticated,service_role;


create or replace function public.wl_my_site_recorder_coverage(
  p_site_id uuid,
  p_from timestamptz default null,
  p_to timestamptz default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_tenant uuid;
  v_tz text;
  v_to timestamptz := coalesce(p_to,now());
  v_from timestamptz;
begin
  v_tenant := public.wl_assert_my_site(p_site_id);
  select timezone into v_tz
    from public.sites
   where id=p_site_id and tenant_id=v_tenant;
  if v_tz is null then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  v_from := coalesce(
    p_from,
    ((v_to at time zone v_tz)::date::text||' 00:00:00')::timestamp
      at time zone v_tz
  );

  return public.wl_site_recorder_coverage_facts(p_site_id,v_from,v_to);
end
$function$;

revoke all on function public.wl_my_site_recorder_coverage(
  uuid,timestamptz,timestamptz
) from public,anon,authenticated,service_role;
grant execute on function public.wl_my_site_recorder_coverage(
  uuid,timestamptz,timestamptz
) to authenticated;


-- Preserve the pre-multi-recorder three-class coverage contract as a helper.
create or replace function public.wl_site_coverage_legacy_classes(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_base jsonb;
  v_wall numeric;
  v_unv numeric;
  v_mon numeric;
  v_rec numeric;
  v_a timestamptz;
  v_b timestamptz;
  v_single uuid;
begin
  -- A site with exactly one configured recorder keeps the legacy wall-clock
  -- contract, and the recorder RPCs treat that recorder's intervals and the
  -- legacy NULL-recorder intervals as the same work (v_singleton). A
  -- recorder-aware Agent bound to that recorder stores its recovery with the
  -- recorder id, so count it here exactly like a NULL-recorder interval.
  select (array_agg(r.id))[1]
    into v_single
    from public.recorders r
   where r.site_id=p_site_id
     and r.is_configured
  having count(*)=1;

  v_base := public.wl_site_coverage_report(p_site_id,p_from,p_to);
  v_a := least(p_from,p_to);
  v_b := greatest(p_from,p_to);
  v_wall := coalesce((v_base->>'wall_seconds')::numeric,0);
  v_unv := coalesce((v_base->>'unverified_seconds')::numeric,0);
  v_mon := coalesce((v_base->>'monitored_seconds')::numeric,0);

  with gaps as (
    select tstzrange(
      (x->>'start')::timestamptz,
      (x->>'end')::timestamptz,
      '[)'
    ) r
    from jsonb_array_elements(coalesce(v_base->'gaps','[]'::jsonb)) x
    where (x->>'start') is not null
      and (x->>'end') is not null
      and (x->>'start')::timestamptz<(x->>'end')::timestamptz
  ),
  gmr as (
    select coalesce(range_agg(r),'{}'::tstzmultirange) rr from gaps
  ),
  rec as (
    select tstzrange(
      greatest(ri.started_at,v_a),
      least(ri.ended_at,v_b),
      '[)'
    ) r
    from public.recovery_intervals ri
    where ri.site_id=p_site_id
      and (ri.recorder_id is null or ri.recorder_id=v_single)
      and ri.status='recovered'
      and greatest(ri.started_at,v_a)<least(ri.ended_at,v_b)
  ),
  rmr as (
    select coalesce(range_agg(r),'{}'::tstzmultirange) rr from rec
  ),
  inter as (
    select ((select rr from gmr)*(select rr from rmr)) m
  )
  select coalesce((
    select sum(extract(epoch from (upper(x)-lower(x))))
    from (select unnest((select m from inter)) x) z
  ),0)::numeric
  into v_rec;

  v_rec := least(v_rec,v_unv);

  return v_base||jsonb_build_object(
    'classes',
    jsonb_build_object(
      'live_seconds',round(v_mon,1),
      'recovered_seconds',round(v_rec,1),
      'unverified_seconds',round(greatest(0,v_unv-v_rec),1),
      'live_ratio',case when v_wall>0 then round(v_mon/v_wall,4) else 1 end,
      'recovered_ratio',case when v_wall>0 then round(v_rec/v_wall,4) else 0 end,
      'unverified_ratio',case when v_wall>0
        then round(greatest(0,v_unv-v_rec)/v_wall,4) else 0 end,
      'total_coverage_ratio',case when v_wall>0
        then round((v_mon+v_rec)/v_wall,4) else 1 end
    )
  );
end
$function$;

revoke all on function public.wl_site_coverage_legacy_classes(
  uuid,timestamptz,timestamptz
) from public,anon,authenticated,service_role;


-- Existing consumers keep every legacy field. Multi-recorder recorder/camera
-- coverage is additive and may be preferred by customer-facing readers once
-- complete_window=true.
create or replace function public.wl_site_coverage_report_classes(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
  select public.wl_site_coverage_legacy_classes(p_site_id,p_from,p_to)
         ||jsonb_build_object(
           'recorder_coverage',
           public.wl_site_recorder_coverage_facts(p_site_id,p_from,p_to)
         )
$function$;

-- This body has no tenant check (the base report it wraps has none either),
-- so it stays service_role-only, as 0103 left it. Browser roles get it back
-- only in 0155, which adds wl_assert_my_site (MNVR-065): a deploy that stops
-- after this file must not expose other tenants' coverage.
revoke all on function public.wl_site_coverage_report_classes(
  uuid,timestamptz,timestamptz
) from public,anon,authenticated;
grant execute on function public.wl_site_coverage_report_classes(
  uuid,timestamptz,timestamptz
) to service_role;
