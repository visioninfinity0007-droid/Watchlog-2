-- Make Chai Wala's Yesterday semantics explicit in live tenant context.
-- Runtime already resolves the configured overnight service window; this records
-- the same rule in tenant metadata for AI/harness consistency.

UPDATE public.site_business_context sbc
SET reporting_prefs = jsonb_set(
  coalesce(sbc.reporting_prefs,'{}'::jsonb),
  '{restaurant_intelligence_context,service_day,yesterday_rule}',
  to_jsonb('Yesterday means the latest completed configured Chai Wala service day, not midnight-to-midnight calendar yesterday.'::text),
  true
)
FROM public.sites s, public.tenants t
WHERE sbc.site_id=s.id
  AND sbc.tenant_id=t.id
  AND lower(t.name)='chaiwala'
  AND s.name='Chai Wala - Chota Bukhari';
