-- =====================================================================
-- Per-tier snapshot retention, enforced on a schedule.
--
-- The pricing tiers promise different history: Starter 7 days of stills,
-- Growth 30, Enterprise 90. Until now nothing enforced that — snapshots
-- (bytea in Postgres) would grow forever, and a promise we do not keep is
-- worse than one we do not make. This deletes stills past each tenant's
-- entitlement, tenant by tenant, and runs nightly via pg_cron.
--
-- Only STILLS are pruned. Event records (the log) are kept for as long as
-- the account is open — they are tiny and are what the daily report and
-- the history are built from. The website and terms say exactly this.
-- =====================================================================

create or replace function public.wl_plan_retention_days(p_plan text)
returns int
language sql
immutable
as $$
  select case coalesce(p_plan, 'trial')
           when 'enterprise' then 90
           when 'growth'     then 30
           else 7            -- trial + starter
         end;
$$;

create or replace function public.wl_enforce_retention()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  t         record;
  v_days    int;
  v_deleted int;
  v_total   int := 0;
  v_report  jsonb := '[]'::jsonb;
begin
  for t in select id, name, plan from tenants loop
    v_days := wl_plan_retention_days(t.plan);
    delete from snapshots
     where tenant_id = t.id
       and captured_at < now() - make_interval(days => v_days);
    get diagnostics v_deleted = row_count;
    v_total := v_total + v_deleted;
    if v_deleted > 0 then
      v_report := v_report || jsonb_build_object(
        'tenant', t.name, 'plan', t.plan, 'keep_days', v_days,
        'deleted', v_deleted);
    end if;
  end loop;

  return jsonb_build_object(
    'ran_at', now(), 'total_deleted', v_total, 'by_tenant', v_report,
    'snapshots_remaining', (select count(*) from snapshots),
    'bytes_remaining', (select coalesce(sum(bytes), 0) from snapshots));
end $$;

-- Called only by the scheduler / a superuser. Not for anon or the portal.
revoke all on function public.wl_enforce_retention() from public, anon, authenticated;

-- Nightly at 03:00 UTC via pg_cron, if available. Wrapped so the migration
-- still applies on a database where pg_cron is not enabled.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    -- Replace any prior definition of the same job.
    perform cron.unschedule(jobid)
       from cron.job where jobname = 'watchlog-retention';
    perform cron.schedule('watchlog-retention', '0 3 * * *',
                          'select public.wl_enforce_retention()');
  end if;
exception when others then
  raise notice 'pg_cron scheduling skipped: %', sqlerrm;
end $$;
