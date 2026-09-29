-- Rolling saved-report windows for restaurant tenants.
-- Keeps frozen daily reports immutable and calculates 7/30-day membership from service_date.
-- This preserves manual business reports (such as Chai Wala 2026-09-27) alongside structured restaurant analytics.

create or replace function public.wl_my_report_window(
  p_site_id uuid,
  p_days integer,
  p_end_date date default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_end date;
  v_start date;
  v_prev_start date;
  v_prev_end date;
  v_reports jsonb;
  v_prev_reports jsonb;
  v_structured jsonb := null;
  v_type text;
begin
  if p_days not in (7,30) then
    raise exception 'report window must be 7 or 30 days' using errcode='22023';
  end if;

  select * into v_site
  from public.sites
  where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  select * into v_ctx
  from public.site_business_context
  where site_id=p_site_id and tenant_id=v_tenant;

  v_end := coalesce(
    p_end_date,
    public.wl_my_last_completed_business_date(p_site_id, now())
  );
  if v_end is null then
    raise exception 'completed business date could not be resolved' using errcode='22023';
  end if;

  v_start := v_end - (p_days - 1);
  v_prev_end := v_start - 1;
  v_prev_start := v_prev_end - (p_days - 1);
  v_type := coalesce(v_ctx.site_type, v_site.site_type, 'other');

  select coalesce(jsonb_agg(
    jsonb_build_object(
      'report_id',r.id,
      'service_date',r.report_date,
      'revision',r.revision,
      'generated_at',r.generated_at,
      'coverage_ratio',r.coverage_ratio,
      'manual_business_report',coalesce((r.payload->>'manual_business_report')::boolean,false),
      'report_profile',r.payload->>'report_profile',
      'title',coalesce(r.payload->>'title',v_site.name),
      'summary',coalesce(
        r.payload->>'executive_summary',
        r.payload->>'narrative_summary',
        r.payload->>'ai_summary',
        r.payload->'visual'->>'owner_summary'
      ),
      'coverage',coalesce(r.payload->'coverage','{}'::jsonb),
      'business_estimates',coalesce(r.payload->'business_estimates','{}'::jsonb),
      'highlights',coalesce(r.payload->'highlights','[]'::jsonb),
      'incidents',coalesce(r.payload->'incidents','[]'::jsonb),
      'operations',coalesce(r.payload->'operations','[]'::jsonb),
      'action_items',coalesce(r.payload->'action_items',r.payload->'priority_actions','[]'::jsonb),
      'site_insights',coalesce(r.payload->'site_insights','[]'::jsonb),
      'restaurant',coalesce(r.payload->'restaurant','{}'::jsonb)
    )
    order by r.report_date desc
  ),'[]'::jsonb)
  into v_reports
  from public.report_snapshots r
  where r.site_id=p_site_id
    and r.tenant_id=v_tenant
    and r.report_date between v_start and v_end;

  select coalesce(jsonb_agg(
    jsonb_build_object(
      'report_id',r.id,
      'service_date',r.report_date,
      'revision',r.revision,
      'generated_at',r.generated_at,
      'coverage_ratio',r.coverage_ratio,
      'manual_business_report',coalesce((r.payload->>'manual_business_report')::boolean,false),
      'summary',coalesce(
        r.payload->>'executive_summary',
        r.payload->>'narrative_summary',
        r.payload->>'ai_summary',
        r.payload->'visual'->>'owner_summary'
      ),
      'business_estimates',coalesce(r.payload->'business_estimates','{}'::jsonb)
    )
    order by r.report_date desc
  ),'[]'::jsonb)
  into v_prev_reports
  from public.report_snapshots r
  where r.site_id=p_site_id
    and r.tenant_id=v_tenant
    and r.report_date between v_prev_start and v_prev_end;

  if v_type='restaurant' then
    v_structured := public.wl_restaurant_period(p_site_id,p_days,v_end);
  end if;

  return jsonb_build_object(
    'schema','report-window-v1',
    'site_id',p_site_id,
    'site',v_site.name,
    'site_type',v_type,
    'timezone',v_site.timezone,
    'days',p_days,
    'period',jsonb_build_object(
      'start_service_date',v_start,
      'end_service_date',v_end,
      'previous_start_service_date',v_prev_start,
      'previous_end_service_date',v_prev_end
    ),
    'saved_report_count',jsonb_array_length(v_reports),
    'saved_reports',v_reports,
    'previous_saved_report_count',jsonb_array_length(v_prev_reports),
    'previous_saved_reports',v_prev_reports,
    'structured_restaurant_metrics',v_structured,
    'window_behavior',jsonb_build_object(
      'rolling',true,
      'rule','Daily reports remain immutable. Membership is calculated from service_date each time the window is opened.',
      'missing_days_are_zero_activity',false
    )
  );
end
$$;

revoke all on function public.wl_my_report_window(uuid,integer,date) from public;
revoke all on function public.wl_my_report_window(uuid,integer,date) from anon;
grant execute on function public.wl_my_report_window(uuid,integer,date) to authenticated;
grant execute on function public.wl_my_report_window(uuid,integer,date) to service_role;

update public.site_business_context
set reporting_prefs = jsonb_set(
  jsonb_set(
    coalesce(reporting_prefs,'{}'::jsonb),
    '{rolling_report_rpc}',
    '"wl_my_report_window"'::jsonb,
    true
  ),
  '{restaurant_intelligence_context,report_windows,rolling_rule}',
  jsonb_build_object(
    'source','saved daily reports + structured restaurant analytics',
    'last_7_days','service dates ending at the selected completed service day; old reports leave automatically after 7 dates',
    'last_30_days','service dates ending at the selected completed service day; old reports leave automatically after 30 dates',
    'missing_days','missing coverage is not zero activity',
    'daily_reports_immutable',true
  ),
  true
),
updated_at=now()
where site_id='1a1fab32-10b3-4082-b61c-180ec04c758c';
