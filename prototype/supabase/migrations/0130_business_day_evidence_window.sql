-- Keep model-side evidence retrieval aligned with the same business/service-day
-- semantics used by daily reports and visual summaries.

CREATE OR REPLACE FUNCTION public.wl_my_business_day_window(
  p_site_id uuid,
  p_date date DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE SECURITY DEFINER
SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_date date;
  v_open time;
  v_close time;
  v_overnight boolean;
  v_timezone text;
  v_start timestamptz;
  v_end timestamptz;
begin
  select * into v_site
  from public.sites
  where id=p_site_id and tenant_id=v_tenant;

  if v_site.id is null then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  select * into v_ctx
  from public.site_business_context
  where site_id=p_site_id and tenant_id=v_tenant;

  v_date:=coalesce(
    p_date,
    public.wl_my_last_completed_business_date(p_site_id,now())
  );

  if v_date is null then
    raise exception 'business day could not be resolved' using errcode='22023';
  end if;

  v_timezone:=coalesce(v_site.timezone,'UTC');
  v_open:=coalesce(v_ctx.open_time,'00:00'::time);
  v_close:=coalesce(v_ctx.close_time,'23:59:59'::time);
  v_overnight:=coalesce(v_ctx.overnight,false) or v_close<=v_open;

  v_start:=(v_date::timestamp+v_open) at time zone v_timezone;
  v_end:=(
    (case when v_overnight then (v_date+1)::timestamp else v_date::timestamp end)
    +v_close
  ) at time zone v_timezone;

  return jsonb_build_object(
    'business_date',v_date,
    'from',v_start,
    'to',v_end,
    'timezone',v_timezone,
    'open_time',v_open,
    'close_time',v_close,
    'overnight',v_overnight
  );
end
$function$;

REVOKE EXECUTE ON FUNCTION public.wl_my_business_day_window(uuid,date) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.wl_my_business_day_window(uuid,date) TO authenticated,service_role;
