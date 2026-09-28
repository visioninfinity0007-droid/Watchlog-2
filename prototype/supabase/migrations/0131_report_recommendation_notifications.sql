-- Surface report-backed recommendations through the existing Notification Center.
-- The report snapshot remains the source of truth; this only previews the first priority action.

CREATE OR REPLACE FUNCTION public.wl_notifications(
  p_site_id uuid DEFAULT NULL,
  p_limit integer DEFAULT 50
) RETURNS jsonb
LANGUAGE plpgsql
STABLE SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_tenant uuid := wl_my_tenant();
  v_user uuid := auth.uid();
  v_limit int := least(greatest(coalesce(p_limit,50),1),200);
begin
  if v_tenant is null or v_user is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;
  if p_site_id is not null and not exists(
    select 1 from sites s where s.id=p_site_id and s.tenant_id=v_tenant
  ) then
    raise exception 'site not in your account' using errcode='42501';
  end if;

  return coalesce((
    with feed as (
      select
        'incident'::text as source_kind,i.id::text as source_id,i.site_id,
        s.name as site_name,
        coalesce(i.incident_started_at,i.occurred_at,i.opened_at) as created_at,
        coalesce(nullif(i.severity,''),'attention') as severity,
        initcap(replace(coalesce(nullif(i.incident_type,''),'Incident'),'_',' ')) as title,
        case
          when c.name is not null then 'Activity needs review on '||c.name||'.'
          else 'Activity at this site needs review.'
        end as body,
        '/incidents/?site='||i.site_id::text as href
      from operations_incidents i
      join sites s on s.id=i.site_id
      left join cameras c on c.id=i.camera_id
      where i.tenant_id=v_tenant
        and (p_site_id is null or i.site_id=p_site_id)
        and coalesce(i.incident_started_at,i.occurred_at,i.opened_at) >= now()-interval '90 days'

      union all

      select
        'report',r.id::text,r.site_id,s.name,r.generated_at,'info',
        'Daily report ready',
        case
          when jsonb_typeof(r.payload->'priority_actions')='array'
           and jsonb_array_length(r.payload->'priority_actions')>0
           and nullif(trim(r.payload->'priority_actions'->>0),'') is not null
            then 'The WatchLog report for '||to_char(r.report_date,'FMDD FMMonth YYYY')||
                 ' is ready. Recommended action: '||
                 left(trim(r.payload->'priority_actions'->>0),320)
          else 'The WatchLog report for '||to_char(r.report_date,'FMDD FMMonth YYYY')||' is ready.'
        end,
        '/reports/?view=yesterday&site='||r.site_id::text
      from report_snapshots r
      join sites s on s.id=r.site_id
      where r.tenant_id=v_tenant
        and (p_site_id is null or r.site_id=p_site_id)
        and r.generated_at >= now()-interval '90 days'

      union all

      select
        'health',f.id::text,f.site_id,s.name,f.opened_at,
        coalesce(nullif(f.severity,''),'warning'),
        case
          when f.camera_id is not null and c.name is not null
            then c.name||' needs attention'
          else 'Site health needs attention'
        end,
        initcap(replace(coalesce(nullif(f.fault_type,''),nullif(f.reason_code,''),'Monitoring issue'),'_',' ')),
        '/site-health/?site='||f.site_id::text
      from operational_faults f
      join sites s on s.id=f.site_id
      left join cameras c on c.id=f.camera_id
      where f.tenant_id=v_tenant
        and f.state<>'resolved'
        and (p_site_id is null or f.site_id=p_site_id)
        and f.opened_at >= now()-interval '90 days'
    ),
    ranked as (
      select feed.*,
             (nr.id is not null) as is_read
      from feed
      left join notification_reads nr
        on nr.tenant_id=v_tenant
       and nr.user_id=v_user
       and nr.source_kind=feed.source_kind
       and nr.source_id=feed.source_id
      order by feed.created_at desc
      limit v_limit
    )
    select jsonb_agg(jsonb_build_object(
      'kind',source_kind,'id',source_id,'site_id',site_id,'site_name',site_name,
      'created_at',created_at,'severity',severity,'title',title,'body',body,
      'href',href,'read',is_read
    ) order by created_at desc)
    from ranked
  ),'[]'::jsonb);
end
$function$;

REVOKE ALL ON FUNCTION public.wl_notifications(uuid,integer) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.wl_notifications(uuid,integer) TO authenticated;
