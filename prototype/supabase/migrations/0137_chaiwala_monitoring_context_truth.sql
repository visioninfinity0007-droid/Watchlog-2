-- Chai Wala AI context: make historical service-day monitoring/report authority explicit.

update public.site_business_context sbc
set reporting_prefs = jsonb_set(
      coalesce(sbc.reporting_prefs,'{}'::jsonb),
      '{restaurant_intelligence_context,monitoring_truth}',
      jsonb_build_object(
        'fully_monitored_rule',
          'A completed service day is fully monitored only when the configured 4:00 PM-4:00 AM service window has no unrecovered/unverified monitoring time.',
        'historical_authority',
          'For a completed service day, service-day monitoring coverage and the completed visual review/saved report outrank generic calendar-day coverage or an empty event index.',
        'empty_event_index_rule',
          'An empty event index does not mean no retained evidence when reviewed snapshots or a saved report exist for that service day.',
        'coverage_language',
          'Unverified time means WatchLog cannot confirm what happened in that period. Never describe it as no activity.'
      ),
      true
    ),
    updated_at=now()
from public.sites s
join public.tenants t on t.id=s.tenant_id
where sbc.site_id=s.id
  and sbc.tenant_id=t.id
  and lower(t.name)='chaiwala'
  and s.name='Chai Wala - Chota Bukhari';
