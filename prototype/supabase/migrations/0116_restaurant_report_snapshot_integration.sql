-- Attach the restaurant service-day block to frozen daily report snapshots.
-- Non-restaurant reports retain their existing payload unchanged.

create or replace function public.wl_generate_daily_report(
  p_site_id uuid,
  p_date date default null,
  p_force boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $$
declare
  v_site public.sites;
  v_ctx public.site_business_context;
  v_is_restaurant boolean;
  v_date date;
  v_existing uuid;
  v_payload jsonb;
  v_inf text;
  v_id uuid;
  v_rev int;
begin
  select * into v_site from public.sites where id=p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode='22023'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id;
  v_is_restaurant:=coalesce(v_ctx.site_type,v_site.site_type,'other')='restaurant';
  v_date:=coalesce(p_date,(now() at time zone v_site.timezone)::date);

  select id into v_existing from public.report_snapshots
   where site_id=p_site_id and report_date=v_date;
  if v_existing is not null and not p_force then
    return (select jsonb_build_object(
      'report_id',id,'frozen',true,'revision',revision,
      'generated_at',generated_at,'payload',payload)
      from public.report_snapshots where id=v_existing);
  end if;

  v_payload:=public.wl_daily_intelligence(p_site_id,v_date,true);
  if v_is_restaurant then
    v_payload:=v_payload||jsonb_build_object(
      'restaurant',public.wl_restaurant_day(p_site_id,v_date)
    );
  end if;

  select version into v_inf from public.inference_config
   where site_id=p_site_id or site_id is null
   order by (site_id is not null) desc limit 1;

  insert into public.report_snapshots(
    tenant_id,site_id,report_date,payload,payload_schema,versions,coverage_ratio
  ) values (
    v_site.tenant_id,p_site_id,v_date,v_payload,v_payload->>'schema',
    jsonb_build_object(
      'inference',coalesce(v_inf,'inference-v1'),
      'journeys','topology-v2',
      'day_state','state-machine-v1',
      'intelligence',v_payload->>'schema',
      'restaurant',case when v_is_restaurant then 'restaurant-day-v1' else null end
    ),
    (v_payload->'coverage'->>'coverage_ratio')::numeric
  )
  on conflict(site_id,report_date) do update
    set payload=excluded.payload,
        payload_schema=excluded.payload_schema,
        versions=excluded.versions,
        coverage_ratio=excluded.coverage_ratio,
        generated_at=now(),
        revision=public.report_snapshots.revision+1,
        delivery_status='pending',
        pdf_sha256=null,
        pdf_bytes=null
  returning id,revision into v_id,v_rev;

  return jsonb_build_object(
    'report_id',v_id,'frozen',false,'revision',v_rev,
    'generated_at',now(),'payload',v_payload
  );
end $$;
