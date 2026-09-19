-- =====================================================================
-- 0083 — Persisted report snapshot (final integration pass, item 7).
--
-- A customer-visible daily report must be generated ONCE and preserved. Portal, PDF and
-- WhatsApp then all consume the SAME frozen snapshot, so a later threshold/config change can
-- never silently change what yesterday's report said. The snapshot preserves the canonical
-- JSON payload, its schema + inference/config versions, coverage, the generated timestamp, the
-- PDF artifact reference/hash, and the delivery status.
-- =====================================================================

create table if not exists public.report_snapshots (
  id             uuid primary key default gen_random_uuid(),   -- report ID
  tenant_id      uuid not null references public.tenants(id) on delete cascade,
  site_id        uuid not null references public.sites(id) on delete cascade,
  report_date    date not null,
  payload        jsonb not null,                                -- the frozen canonical dataset
  payload_schema text not null,
  versions       jsonb not null default '{}'::jsonb,            -- {inference, journeys, day_state, intelligence}
  coverage_ratio numeric,
  generated_at   timestamptz not null default now(),
  revision       int not null default 1,
  pdf_sha256     text,
  pdf_bytes      int,
  delivery_status text not null default 'pending'
                   check (delivery_status in ('pending','delivered','failed','skipped')),
  unique (site_id, report_date)
);
create index if not exists report_snapshots_site_idx on public.report_snapshots (site_id, report_date desc);
alter table public.report_snapshots enable row level security;

-- ---------------------------------------------------------------------
-- Generate (once) or fetch the frozen snapshot. Without p_force, an existing snapshot is
-- returned verbatim — it is NOT recomputed, so it is immune to later threshold changes.
-- ---------------------------------------------------------------------
create or replace function public.wl_generate_daily_report(
  p_site_id uuid, p_date date default null, p_force boolean default false
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  v_site sites; v_date date; v_existing uuid; v_payload jsonb; v_inf text; v_id uuid; v_rev int;
begin
  select * into v_site from sites where id = p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode = '22023'; end if;
  v_date := coalesce(p_date, (now() at time zone v_site.timezone)::date);

  select id into v_existing from report_snapshots where site_id = p_site_id and report_date = v_date;
  if v_existing is not null and not p_force then
    return (select jsonb_build_object('report_id', id, 'frozen', true, 'revision', revision,
                     'generated_at', generated_at, 'payload', payload)
              from report_snapshots where id = v_existing);
  end if;

  v_payload := wl_daily_intelligence(p_site_id, v_date, true);
  select version into v_inf from inference_config
    where site_id = p_site_id or site_id is null order by (site_id is not null) desc limit 1;

  insert into report_snapshots (tenant_id, site_id, report_date, payload, payload_schema, versions, coverage_ratio)
  values (v_site.tenant_id, p_site_id, v_date, v_payload, v_payload->>'schema',
          jsonb_build_object('inference', coalesce(v_inf,'inference-v1'), 'journeys', 'topology-v2',
                             'day_state', 'state-machine-v1', 'intelligence', v_payload->>'schema'),
          (v_payload->'coverage'->>'coverage_ratio')::numeric)
  on conflict (site_id, report_date) do update
    set payload = excluded.payload, payload_schema = excluded.payload_schema, versions = excluded.versions,
        coverage_ratio = excluded.coverage_ratio, generated_at = now(),
        revision = report_snapshots.revision + 1, delivery_status = 'pending',
        pdf_sha256 = null, pdf_bytes = null
  returning id, revision into v_id, v_rev;

  return jsonb_build_object('report_id', v_id, 'frozen', false, 'revision', v_rev,
                            'generated_at', now(), 'payload', v_payload);
end $$;
revoke all on function public.wl_generate_daily_report(uuid,date,boolean) from public, anon, authenticated;
grant execute on function public.wl_generate_daily_report(uuid,date,boolean) to service_role;

create or replace function public.wl_get_report_snapshot(p_report_id uuid)
returns jsonb language sql stable security definer set search_path = public as $$
  select jsonb_build_object('report_id', id, 'site_id', site_id, 'report_date', report_date,
           'payload', payload, 'payload_schema', payload_schema, 'versions', versions,
           'coverage_ratio', coverage_ratio, 'generated_at', generated_at, 'revision', revision,
           'pdf_sha256', pdf_sha256, 'pdf_bytes', pdf_bytes, 'delivery_status', delivery_status)
    from report_snapshots where id = p_report_id;
$$;
revoke all on function public.wl_get_report_snapshot(uuid) from public, anon, authenticated;
grant execute on function public.wl_get_report_snapshot(uuid) to service_role;

-- Portal reads the frozen snapshot for a day; it does NOT regenerate.
create or replace function public.wl_my_report_snapshot(p_site_id uuid, p_date date default null)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_owner uuid; v_date date; v_tz text;
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode='42501'; end if;
  select tenant_id, timezone into v_owner, v_tz from sites where id = p_site_id;
  if v_owner is null or v_owner <> v_tenant then raise exception 'not authorized for this site' using errcode='42501'; end if;
  v_date := coalesce(p_date, (now() at time zone v_tz)::date);
  return (select jsonb_build_object('report_id', id, 'report_date', report_date, 'payload', payload,
            'versions', versions, 'generated_at', generated_at, 'delivery_status', delivery_status,
            'pdf_available', pdf_sha256 is not null)
            from report_snapshots where site_id = p_site_id and report_date = v_date);
end $$;
revoke all on function public.wl_my_report_snapshot(uuid,date) from public, anon;
grant execute on function public.wl_my_report_snapshot(uuid,date) to authenticated, service_role;

-- Attach the generated PDF artifact reference, and record delivery status.
create or replace function public.wl_set_report_pdf(p_report_id uuid, p_sha256 text, p_bytes int)
returns void language sql volatile security definer set search_path = public as $$
  update report_snapshots set pdf_sha256 = p_sha256, pdf_bytes = p_bytes where id = p_report_id;
$$;
revoke all on function public.wl_set_report_pdf(uuid,text,int) from public, anon, authenticated;
grant execute on function public.wl_set_report_pdf(uuid,text,int) to service_role;

create or replace function public.wl_set_report_delivery_status(p_report_id uuid, p_status text)
returns void language sql volatile security definer set search_path = public as $$
  update report_snapshots set delivery_status = p_status where id = p_report_id;
$$;
revoke all on function public.wl_set_report_delivery_status(uuid,text) from public, anon, authenticated;
grant execute on function public.wl_set_report_delivery_status(uuid,text) to service_role;
