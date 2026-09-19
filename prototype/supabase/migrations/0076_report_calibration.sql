-- =====================================================================
-- 0076 — Report calibration tooling (item 10). Reviewer feedback storage + a review API +
-- config tuning WITHOUT editing product code.
--
-- A reviewer confirms or corrects an inferred metric (opening, closing, visitor journeys,
-- probable staff, restricted episodes). We store the inferred vs confirmed value, the delta,
-- a note, and the config version in force — so error can be tracked over time and thresholds
-- tuned against evidence. Tuning writes a per-site inference_config override (0073), which the
-- derivation reads next run: no code change to change behavior.
-- =====================================================================

create table if not exists public.report_review_feedback (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  report_date   date not null,
  metric        text not null check (metric in
                  ('opening','closing','visitor_journeys','probable_staff','restricted_episodes','after_hours','other')),
  inferred_value text,
  confirmed_value text,
  numeric_error numeric,                     -- confirmed - inferred, when both are numeric
  reviewer_note text,
  config_version text,
  reviewer      uuid,                        -- auth.uid() of the reviewer
  reviewed_at   timestamptz not null default now()
);
create index if not exists rrf_site_idx on public.report_review_feedback (site_id, report_date desc);
alter table public.report_review_feedback enable row level security;

-- ---------------------------------------------------------------------
-- Submit a review (tenant-guarded). numeric_error computed when both values parse as numbers.
-- ---------------------------------------------------------------------
create or replace function public.wl_submit_report_review(
  p_site_id uuid, p_report_date date, p_metric text,
  p_inferred text, p_confirmed text, p_note text default null, p_config_version text default null
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id); v_err numeric; v_id uuid;
begin
  if p_metric not in ('opening','closing','visitor_journeys','probable_staff','restricted_episodes','after_hours','other') then
    raise exception 'unknown metric' using errcode = '22023';
  end if;
  if p_inferred ~ '^-?[0-9]+(\.[0-9]+)?$' and p_confirmed ~ '^-?[0-9]+(\.[0-9]+)?$' then
    v_err := p_confirmed::numeric - p_inferred::numeric;
  end if;
  insert into report_review_feedback (tenant_id, site_id, report_date, metric, inferred_value,
      confirmed_value, numeric_error, reviewer_note, config_version, reviewer)
  values (v_tenant, p_site_id, p_report_date, p_metric, p_inferred, p_confirmed, v_err,
      nullif(btrim(coalesce(p_note,'')),''), p_config_version, auth.uid())
  returning id into v_id;
  return jsonb_build_object('ok', true, 'id', v_id, 'numeric_error', v_err);
end $$;
revoke all on function public.wl_submit_report_review(uuid,date,text,text,text,text,text) from public, anon;
grant execute on function public.wl_submit_report_review(uuid,date,text,text,text,text,text) to authenticated, service_role;

-- Recent reviews for a site.
create or replace function public.wl_report_reviews(p_site_id uuid, p_days int default 90)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id);
begin
  return coalesce((select jsonb_agg(jsonb_build_object(
      'report_date', report_date, 'metric', metric, 'inferred', inferred_value,
      'confirmed', confirmed_value, 'numeric_error', numeric_error, 'note', reviewer_note,
      'config_version', config_version, 'reviewed_at', reviewed_at) order by reviewed_at desc)
    from report_review_feedback
    where site_id = p_site_id and report_date > current_date - least(greatest(p_days,1),365)),'[]'::jsonb);
end $$;
revoke all on function public.wl_report_reviews(uuid,int) from public, anon;
grant execute on function public.wl_report_reviews(uuid,int) to authenticated, service_role;

-- Calibration signal: mean absolute error + count per metric.
create or replace function public.wl_review_summary(p_site_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id);
begin
  return coalesce((select jsonb_object_agg(metric, m) from (
      select metric, jsonb_build_object('reviews', count(*),
             'mean_abs_error', round(avg(abs(numeric_error)) filter (where numeric_error is not null), 2),
             'bias', round(avg(numeric_error) filter (where numeric_error is not null), 2)) m
        from report_review_feedback where site_id = p_site_id group by metric) z), '{}'::jsonb);
end $$;
revoke all on function public.wl_review_summary(uuid) from public, anon;
grant execute on function public.wl_review_summary(uuid) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Tune inference thresholds/weights as DATA (a per-site override), no code edit.
-- ---------------------------------------------------------------------
create or replace function public.wl_upsert_inference_config(
  p_site_id uuid, p_config jsonb, p_version text default 'site-tuned'
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id);
begin
  if jsonb_typeof(p_config) <> 'object' then raise exception 'config must be a json object' using errcode = '22023'; end if;
  insert into inference_config (site_id, version, config)
  values (p_site_id, p_version, p_config)
  on conflict (site_id, version) do update set config = excluded.config;
  return jsonb_build_object('ok', true, 'site_id', p_site_id, 'version', p_version);
end $$;
revoke all on function public.wl_upsert_inference_config(uuid,jsonb,text) from public, anon;
grant execute on function public.wl_upsert_inference_config(uuid,jsonb,text) to authenticated, service_role;
