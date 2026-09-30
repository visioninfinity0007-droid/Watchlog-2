-- Recommendation feedback loop for client-facing reports.
-- Clients respond to exact report recommendations; WatchLog platform staff triage and close the loop.

create table if not exists public.report_recommendation_feedback (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references public.report_snapshots(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  report_date date not null,
  report_revision integer not null default 1,
  recommendation_id text not null,
  recommendation_title text not null,
  recommendation_body text not null,
  recommendation_priority text null,
  client_user_id uuid not null references auth.users(id) on delete cascade,
  response_code text not null check (response_code in ('accepted','need_help','not_now','not_relevant')),
  client_note text null,
  team_status text not null default 'new' check (team_status in ('new','in_progress','resolved','closed')),
  team_note text null,
  team_updated_by uuid null references auth.users(id) on delete set null,
  team_updated_at timestamptz null,
  responded_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint report_recommendation_feedback_rec_len check (char_length(recommendation_id) between 1 and 160),
  constraint report_recommendation_feedback_note_len check (client_note is null or char_length(client_note) <= 2000),
  constraint report_recommendation_feedback_team_note_len check (team_note is null or char_length(team_note) <= 4000),
  unique(report_id,recommendation_id,client_user_id)
);

create index if not exists report_recommendation_feedback_tenant_status_idx
  on public.report_recommendation_feedback(tenant_id,team_status,updated_at desc);
create index if not exists report_recommendation_feedback_site_report_idx
  on public.report_recommendation_feedback(site_id,report_date desc,updated_at desc);

alter table public.report_recommendation_feedback enable row level security;
revoke all on table public.report_recommendation_feedback from anon, authenticated;
grant select,insert,update,delete on table public.report_recommendation_feedback to service_role;

create or replace function public.wl_my_recommendation_feedback(
  p_site_id uuid,
  p_report_id uuid
)
returns jsonb
language plpgsql
stable
security definer
set search_path to 'public'
as $function$
declare
  v_user uuid := auth.uid();
  v_tenant uuid;
  v_report_ok boolean;
begin
  if v_user is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;

  v_tenant := public.wl_assert_my_site(p_site_id);

  select exists(
    select 1 from public.report_snapshots r
     where r.id=p_report_id and r.site_id=p_site_id and r.tenant_id=v_tenant
  ) into v_report_ok;

  if not v_report_ok then
    raise exception 'report not available for this site' using errcode='42501';
  end if;

  return jsonb_build_object(
    'items',
    coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'id',f.id,
          'recommendation_id',f.recommendation_id,
          'response_code',f.response_code,
          'client_note',f.client_note,
          'team_status',f.team_status,
          'responded_at',f.responded_at,
          'updated_at',f.updated_at
        )
        order by f.updated_at desc
      )
      from public.report_recommendation_feedback f
      where f.report_id=p_report_id
        and f.site_id=p_site_id
        and f.tenant_id=v_tenant
        and f.client_user_id=v_user
    ),'[]'::jsonb)
  );
end
$function$;

create or replace function public.wl_save_recommendation_feedback(
  p_site_id uuid,
  p_report_id uuid,
  p_recommendation_id text,
  p_response_code text,
  p_client_note text default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  v_user uuid := auth.uid();
  v_tenant uuid;
  v_report public.report_snapshots%rowtype;
  v_rec jsonb;
  v_row public.report_recommendation_feedback%rowtype;
  v_note text := nullif(btrim(coalesce(p_client_note,'')),'');
begin
  if v_user is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;

  if p_response_code not in ('accepted','need_help','not_now','not_relevant') then
    raise exception 'invalid recommendation response' using errcode='22023';
  end if;

  if char_length(coalesce(p_recommendation_id,'')) not between 1 and 160 then
    raise exception 'invalid recommendation id' using errcode='22023';
  end if;

  if v_note is not null and char_length(v_note)>2000 then
    raise exception 'response note is too long' using errcode='22023';
  end if;

  v_tenant := public.wl_assert_my_site(p_site_id);

  select * into v_report
    from public.report_snapshots
   where id=p_report_id and site_id=p_site_id and tenant_id=v_tenant;

  if v_report.id is null then
    raise exception 'report not available for this site' using errcode='42501';
  end if;

  select x.value into v_rec
    from jsonb_array_elements(coalesce(v_report.payload->'action_items','[]'::jsonb)) as x(value)
   where x.value->>'id'=p_recommendation_id
   limit 1;

  if v_rec is null then
    raise exception 'recommendation not found in this report' using errcode='22023';
  end if;

  insert into public.report_recommendation_feedback(
    report_id,tenant_id,site_id,report_date,report_revision,
    recommendation_id,recommendation_title,recommendation_body,recommendation_priority,
    client_user_id,response_code,client_note,team_status,responded_at,updated_at
  )
  values(
    v_report.id,v_tenant,p_site_id,v_report.report_date,v_report.revision,
    p_recommendation_id,
    left(coalesce(v_rec->>'title','Recommendation'),300),
    left(coalesce(v_rec->>'body',''),4000),
    nullif(left(coalesce(v_rec->>'priority',''),80),''),
    v_user,p_response_code,v_note,'new',now(),now()
  )
  on conflict(report_id,recommendation_id,client_user_id) do update
    set report_revision=excluded.report_revision,
        recommendation_title=excluded.recommendation_title,
        recommendation_body=excluded.recommendation_body,
        recommendation_priority=excluded.recommendation_priority,
        response_code=excluded.response_code,
        client_note=excluded.client_note,
        team_status='new',
        responded_at=now(),
        updated_at=now()
  returning * into v_row;

  return jsonb_build_object(
    'id',v_row.id,
    'recommendation_id',v_row.recommendation_id,
    'response_code',v_row.response_code,
    'client_note',v_row.client_note,
    'team_status',v_row.team_status,
    'responded_at',v_row.responded_at,
    'updated_at',v_row.updated_at
  );
end
$function$;

create or replace function public.wl_platform_recommendation_feedback(
  p_tenant_id uuid default null,
  p_status text default null,
  p_limit integer default 100
)
returns jsonb
language plpgsql
stable
security definer
set search_path to 'public'
as $function$
declare
  v_role text;
  v_limit integer := greatest(1,least(coalesce(p_limit,100),500));
begin
  v_role := public.wl_platform_require(array['platform_owner','platform_admin','platform_support']);

  if p_status is not null and p_status not in ('new','in_progress','resolved','closed') then
    raise exception 'invalid feedback status' using errcode='22023';
  end if;

  return jsonb_build_object(
    'items',
    coalesce((
      select jsonb_agg(to_jsonb(q) order by q.updated_at desc)
      from (
        select
          f.id,f.tenant_id,t.name as tenant_name,f.site_id,s.name as site_name,
          f.report_id,f.report_date,f.report_revision,
          f.recommendation_id,f.recommendation_title,f.recommendation_body,f.recommendation_priority,
          f.response_code,f.client_note,f.team_status,f.team_note,
          f.responded_at,f.updated_at,f.team_updated_at,
          coalesce(u.email,'Customer user') as client_email
        from public.report_recommendation_feedback f
        join public.tenants t on t.id=f.tenant_id
        join public.sites s on s.id=f.site_id
        left join auth.users u on u.id=f.client_user_id
        where (p_tenant_id is null or f.tenant_id=p_tenant_id)
          and (p_status is null or f.team_status=p_status)
        order by f.updated_at desc
        limit v_limit
      ) q
    ),'[]'::jsonb)
  );
end
$function$;

create or replace function public.wl_platform_set_recommendation_feedback_status(
  p_feedback_id uuid,
  p_status text,
  p_team_note text default null,
  p_reason text default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  v_role text;
  v_user uuid := auth.uid();
  v_before public.report_recommendation_feedback%rowtype;
  v_after public.report_recommendation_feedback%rowtype;
  v_note text := nullif(btrim(coalesce(p_team_note,'')),'');
begin
  v_role := public.wl_platform_require(array['platform_owner','platform_admin','platform_support']);

  if p_status not in ('new','in_progress','resolved','closed') then
    raise exception 'invalid feedback status' using errcode='22023';
  end if;

  if v_note is not null and char_length(v_note)>4000 then
    raise exception 'team note is too long' using errcode='22023';
  end if;

  select * into v_before
    from public.report_recommendation_feedback
   where id=p_feedback_id;

  if v_before.id is null then
    raise exception 'feedback not found' using errcode='42704';
  end if;

  update public.report_recommendation_feedback
     set team_status=p_status,
         team_note=v_note,
         team_updated_by=v_user,
         team_updated_at=now(),
         updated_at=now()
   where id=p_feedback_id
  returning * into v_after;

  perform public.wl_platform_write_audit(
    v_after.tenant_id,
    'recommendation_feedback_status',
    coalesce(nullif(btrim(coalesce(p_reason,'')),''),'Recommendation feedback follow-up updated'),
    jsonb_build_object('id',v_before.id,'team_status',v_before.team_status,'team_note',v_before.team_note),
    jsonb_build_object('id',v_after.id,'team_status',v_after.team_status,'team_note',v_after.team_note)
  );

  return jsonb_build_object(
    'id',v_after.id,
    'team_status',v_after.team_status,
    'team_note',v_after.team_note,
    'team_updated_at',v_after.team_updated_at
  );
end
$function$;

revoke all on function public.wl_my_recommendation_feedback(uuid,uuid) from public,anon;
revoke all on function public.wl_save_recommendation_feedback(uuid,uuid,text,text,text) from public,anon;
revoke all on function public.wl_platform_recommendation_feedback(uuid,text,integer) from public,anon;
revoke all on function public.wl_platform_set_recommendation_feedback_status(uuid,text,text,text) from public,anon;

grant execute on function public.wl_my_recommendation_feedback(uuid,uuid) to authenticated;
grant execute on function public.wl_save_recommendation_feedback(uuid,uuid,text,text,text) to authenticated;
grant execute on function public.wl_platform_recommendation_feedback(uuid,text,integer) to authenticated;
grant execute on function public.wl_platform_set_recommendation_feedback_status(uuid,text,text,text) to authenticated;
