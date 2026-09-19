-- =====================================================================
-- 0101 — WatchLog AI workspace foundation
--
-- Adds a tenant-safe conversational workspace for the new AI-first portal.
-- The browser never receives recorder credentials or provider API keys.
-- The AI context is assembled through SECURITY DEFINER RPCs scoped to the
-- signed-in tenant/site and reuses the existing capability + diagnosis truth.
-- =====================================================================

create table if not exists public.ai_conversations (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  user_id     uuid not null,
  site_id     uuid references public.sites(id) on delete set null,
  title       text not null default 'New conversation',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists ai_conversations_user_updated_idx
  on public.ai_conversations(tenant_id, user_id, updated_at desc);
alter table public.ai_conversations enable row level security;

create table if not exists public.ai_messages (
  id               bigint generated always as identity primary key,
  conversation_id  uuid not null references public.ai_conversations(id) on delete cascade,
  tenant_id         uuid not null references public.tenants(id) on delete cascade,
  user_id           uuid not null,
  role              text not null check (role in ('user','assistant','tool')),
  content           text not null,
  payload           jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now()
);
create index if not exists ai_messages_conversation_idx
  on public.ai_messages(conversation_id, id);
alter table public.ai_messages enable row level security;

-- No direct table access. All reads/writes go through tenant/user-guarded RPCs.
revoke all on table public.ai_conversations from public, anon, authenticated;
revoke all on table public.ai_messages from public, anon, authenticated;

create or replace function public.wl_ai_new_conversation(
  p_site_id uuid default null,
  p_title text default 'New conversation'
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_id uuid;
  v_title text := left(coalesce(nullif(trim(p_title),''),'New conversation'), 120);
begin
  if v_tenant is null or auth.uid() is null then
    raise exception 'not authenticated' using errcode = '42501';
  end if;
  if p_site_id is not null and not exists (
      select 1 from sites s where s.id = p_site_id and s.tenant_id = v_tenant
  ) then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;

  insert into ai_conversations(tenant_id,user_id,site_id,title)
  values(v_tenant,auth.uid(),p_site_id,v_title)
  returning id into v_id;

  return jsonb_build_object('id',v_id,'site_id',p_site_id,'title',v_title);
end $$;
revoke all on function public.wl_ai_new_conversation(uuid,text) from public, anon;
grant execute on function public.wl_ai_new_conversation(uuid,text) to authenticated;

create or replace function public.wl_ai_conversations(p_limit int default 30)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null or auth.uid() is null then
    raise exception 'not authenticated' using errcode = '42501';
  end if;
  return coalesce((
    select jsonb_agg(x order by x.updated_at desc)
    from (
      select c.id,c.site_id,c.title,c.created_at,c.updated_at,
             s.name as site_name
        from ai_conversations c
        left join sites s on s.id = c.site_id
       where c.tenant_id = v_tenant and c.user_id = auth.uid()
       order by c.updated_at desc
       limit greatest(1,least(coalesce(p_limit,30),100))
    ) x
  ),'[]'::jsonb);
end $$;
revoke all on function public.wl_ai_conversations(int) from public, anon;
grant execute on function public.wl_ai_conversations(int) to authenticated;

create or replace function public.wl_ai_messages(
  p_conversation_id uuid,
  p_limit int default 80
) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null or auth.uid() is null then
    raise exception 'not authenticated' using errcode = '42501';
  end if;
  if not exists (
    select 1 from ai_conversations c
     where c.id=p_conversation_id and c.tenant_id=v_tenant and c.user_id=auth.uid()
  ) then
    raise exception 'conversation not found' using errcode = '42501';
  end if;

  return coalesce((
    select jsonb_agg(y order by y.id)
    from (
      select m.id,m.role,m.content,m.payload,m.created_at
        from ai_messages m
       where m.conversation_id=p_conversation_id and m.tenant_id=v_tenant and m.user_id=auth.uid()
       order by m.id desc
       limit greatest(1,least(coalesce(p_limit,80),200))
    ) y
  ),'[]'::jsonb);
end $$;
revoke all on function public.wl_ai_messages(uuid,int) from public, anon;
grant execute on function public.wl_ai_messages(uuid,int) to authenticated;

create or replace function public.wl_ai_append_message(
  p_conversation_id uuid,
  p_role text,
  p_content text,
  p_payload jsonb default '{}'::jsonb
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_id bigint;
  v_content text := trim(coalesce(p_content,''));
begin
  if v_tenant is null or auth.uid() is null then
    raise exception 'not authenticated' using errcode = '42501';
  end if;
  if p_role not in ('user','assistant','tool') then
    raise exception 'invalid message role' using errcode = '22023';
  end if;
  if length(v_content)=0 or length(v_content)>20000 then
    raise exception 'invalid message content' using errcode = '22023';
  end if;
  if not exists (
    select 1 from ai_conversations c
     where c.id=p_conversation_id and c.tenant_id=v_tenant and c.user_id=auth.uid()
  ) then
    raise exception 'conversation not found' using errcode = '42501';
  end if;

  insert into ai_messages(conversation_id,tenant_id,user_id,role,content,payload)
  values(p_conversation_id,v_tenant,auth.uid(),p_role,v_content,coalesce(p_payload,'{}'::jsonb))
  returning id into v_id;

  update ai_conversations set updated_at=now(),
    title=case
      when title='New conversation' and p_role='user'
      then left(regexp_replace(v_content,'\s+',' ','g'),72)
      else title end
  where id=p_conversation_id;

  return jsonb_build_object('id',v_id,'ok',true);
end $$;
revoke all on function public.wl_ai_append_message(uuid,text,text,jsonb) from public, anon;
grant execute on function public.wl_ai_append_message(uuid,text,text,jsonb) to authenticated;

-- One factual context packet for the AI. Recorder credentials are deliberately absent.
create or replace function public.wl_ai_context(p_site_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_tenant uuid := wl_assert_my_site(p_site_id);
  v_site sites;
  v_diag jsonb;
  v_ctx jsonb;
  v_onboarding jsonb;
  v_recent jsonb;
  v_camera_rows jsonb;
begin
  select * into v_site from sites where id=p_site_id and tenant_id=v_tenant;
  v_diag := wl_my_site_diagnosis(p_site_id);
  v_ctx := wl_my_site_context(p_site_id);
  v_onboarding := wl_onboarding_status(p_site_id);

  select coalesce(jsonb_agg(to_jsonb(r) order by r.device_ts desc),'[]'::jsonb) into v_recent
    from (
      select e.id as event_id,e.event_type,e.device_ts,e.received_at,
             c.name as camera,c.channel,
             coalesce(e.payload->>'source','live') as source,
             coalesce((e.payload->>'recovered')::boolean,false) as recovered
        from events e
        left join cameras c on c.id=e.camera_id
       where e.site_id=p_site_id
       order by e.device_ts desc
       limit 20
    ) r;

  select coalesce(jsonb_agg(jsonb_build_object(
      'id',c.id,'channel',c.channel,'name',c.name,'purpose',c.purpose,
      'monitor',c.is_configured,'analytics_enabled',coalesce(c.analytics_enabled,false),
      'health_state',coalesce(h.health_state::text,'unknown'),
      'recording_state',coalesce(h.recording_state::text,'unknown')
    ) order by c.channel),'[]'::jsonb) into v_camera_rows
    from cameras c left join camera_health h on h.camera_id=c.id
   where c.site_id=p_site_id;

  return jsonb_build_object(
    'facts_version','watchlog-ai-context-v1',
    'generated_at',now(),
    'site',jsonb_build_object('id',v_site.id,'name',v_site.name,'timezone',v_site.timezone),
    'business_context',v_ctx,
    'onboarding',v_onboarding,
    'recorder',v_diag->'recorder',
    'connectivity',v_diag->'connectivity',
    'capabilities',v_diag->'capabilities',
    'capability_known',v_diag->'capability_known',
    'cameras',v_camera_rows,
    'faults',v_diag->'faults',
    'coverage',v_diag->'coverage',
    'permissions',v_diag->'tiers',
    'recent_events',v_recent,
    'safety',jsonb_build_object(
      'recorder_credentials_leave_site',false,
      'recorder_writes_require_approval',true,
      'unknown_capability_must_not_be_assumed',true)
  );
end $$;
revoke all on function public.wl_ai_context(uuid) from public, anon;
grant execute on function public.wl_ai_context(uuid) to authenticated, service_role;

-- Setup-time camera edit used by the conversational setup wizard.
create or replace function public.wl_ai_setup_camera(
  p_site_id uuid,
  p_camera_id uuid,
  p_name text default null,
  p_purpose text default null,
  p_monitor boolean default null
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_row cameras;
  v_name text := nullif(trim(coalesce(p_name,'')),'');
  v_purpose text := nullif(trim(coalesce(p_purpose,'')),'');
begin
  if not exists(select 1 from sites s where s.id=p_site_id and s.tenant_id=v_tenant) then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;
  if v_name is not null and length(v_name)>120 then raise exception 'camera name too long' using errcode='22023'; end if;
  if v_purpose is not null and length(v_purpose)>120 then raise exception 'camera purpose too long' using errcode='22023'; end if;

  update cameras c set
    name=coalesce(v_name,c.name),
    purpose=coalesce(v_purpose,c.purpose),
    is_configured=coalesce(p_monitor,c.is_configured),
    analytics_enabled=case when coalesce(p_monitor,c.is_configured) then c.analytics_enabled else false end
  where c.id=p_camera_id and c.site_id=p_site_id and c.tenant_id=v_tenant
  returning * into v_row;

  if v_row.id is null then
    raise exception 'camera not found' using errcode='22023';
  end if;

  return jsonb_build_object('ok',true,'camera',jsonb_build_object(
    'id',v_row.id,'channel',v_row.channel,'name',v_row.name,'purpose',v_row.purpose,
    'monitor',v_row.is_configured));
end $$;
revoke all on function public.wl_ai_setup_camera(uuid,uuid,text,text,boolean) from public, anon;
grant execute on function public.wl_ai_setup_camera(uuid,uuid,text,text,boolean) to authenticated;
