-- 0111: private customer image attachments + platform-admin conversation review.
create table if not exists public.ai_message_attachments(
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.ai_conversations(id) on delete cascade,
  message_id bigint not null references public.ai_messages(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  object_path text not null unique,
  file_name text not null,
  content_type text not null check(content_type in('image/jpeg','image/png','image/webp')),
  bytes integer not null check(bytes between 1 and 4194304),
  created_at timestamptz not null default now()
);
create index if not exists ai_message_attachments_conversation_idx on public.ai_message_attachments(conversation_id,message_id);
create index if not exists ai_message_attachments_user_idx on public.ai_message_attachments(user_id,created_at desc);
alter table public.ai_message_attachments enable row level security;
revoke all on public.ai_message_attachments from anon,authenticated;

do $$
begin
  if to_regclass('storage.buckets') is not null then
    insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
    values('ai-chat-attachments','ai-chat-attachments',false,4194304,array['image/jpeg','image/png','image/webp']::text[])
    on conflict(id) do update set public=false,file_size_limit=excluded.file_size_limit,allowed_mime_types=excluded.allowed_mime_types;
  end if;
end $$;

create or replace function public.wl_platform_ai_conversations(
  p_tenant_id uuid default null,p_search text default null,p_limit int default 100,p_offset int default 0)
returns jsonb language plpgsql stable security definer set search_path=public as $$
declare
  v_role text:=wl_platform_require(array['platform_owner','platform_admin','platform_support']);
  v_limit int:=least(greatest(coalesce(p_limit,100),1),250);
  v_offset int:=greatest(coalesce(p_offset,0),0);
  v_search text:=nullif(btrim(coalesce(p_search,'')),'');
begin
  return jsonb_build_object('role',v_role,
    'items',coalesce((select jsonb_agg(to_jsonb(x) order by x.updated_at desc) from(
      select c.id,c.tenant_id,t.name tenant_name,c.site_id,s.name site_name,c.user_id,u.email user_email,
        c.title,c.created_at,c.updated_at,
        (select count(*) from ai_messages m where m.conversation_id=c.id) message_count,
        (select count(*) from ai_message_attachments a where a.conversation_id=c.id) attachment_count,
        (select max(m.created_at) from ai_messages m where m.conversation_id=c.id) last_message_at,
        (select left(m.content,280) from ai_messages m where m.conversation_id=c.id and m.role='user' order by m.id desc limit 1) last_user_message,
        (select left(m.content,280) from ai_messages m where m.conversation_id=c.id and m.role='assistant' order by m.id desc limit 1) last_assistant_message
      from ai_conversations c join tenants t on t.id=c.tenant_id
      left join sites s on s.id=c.site_id left join auth.users u on u.id=c.user_id
      where(p_tenant_id is null or c.tenant_id=p_tenant_id) and(v_search is null or c.title ilike '%'||v_search||'%'
        or t.name ilike '%'||v_search||'%' or coalesce(s.name,'') ilike '%'||v_search||'%'
        or coalesce(u.email,'') ilike '%'||v_search||'%' or exists(select 1 from ai_messages m where m.conversation_id=c.id and m.content ilike '%'||v_search||'%'))
      order by c.updated_at desc limit v_limit offset v_offset)x),'[]'::jsonb),
    'total',(select count(*) from ai_conversations c join tenants t on t.id=c.tenant_id
      left join sites s on s.id=c.site_id left join auth.users u on u.id=c.user_id
      where(p_tenant_id is null or c.tenant_id=p_tenant_id) and(v_search is null or c.title ilike '%'||v_search||'%'
        or t.name ilike '%'||v_search||'%' or coalesce(s.name,'') ilike '%'||v_search||'%'
        or coalesce(u.email,'') ilike '%'||v_search||'%' or exists(select 1 from ai_messages m where m.conversation_id=c.id and m.content ilike '%'||v_search||'%'))));
end $$;

create or replace function public.wl_platform_ai_conversation(p_conversation_id uuid)
returns jsonb language plpgsql stable security definer set search_path=public as $$
declare v_role text:=wl_platform_require(array['platform_owner','platform_admin','platform_support']); v_conv ai_conversations;
begin
  select * into v_conv from ai_conversations where id=p_conversation_id;
  if v_conv.id is null then raise exception 'conversation not found' using errcode='22023'; end if;
  return jsonb_build_object('role',v_role,
    'conversation',jsonb_build_object('id',v_conv.id,'tenant_id',v_conv.tenant_id,'tenant_name',(select name from tenants where id=v_conv.tenant_id),
      'site_id',v_conv.site_id,'site_name',(select name from sites where id=v_conv.site_id),'user_id',v_conv.user_id,
      'user_email',(select email from auth.users where id=v_conv.user_id),'title',v_conv.title,'created_at',v_conv.created_at,'updated_at',v_conv.updated_at),
    'messages',coalesce((select jsonb_agg(jsonb_build_object('id',m.id,'role',m.role,'content',m.content,'payload',m.payload,'created_at',m.created_at,
      'attachments',coalesce((select jsonb_agg(jsonb_build_object('id',a.id,'name',a.file_name,'content_type',a.content_type,'bytes',a.bytes,'created_at',a.created_at) order by a.created_at)
        from ai_message_attachments a where a.message_id=m.id),'[]'::jsonb)) order by m.id)
      from ai_messages m where m.conversation_id=v_conv.id),'[]'::jsonb),
    'routes',coalesce((select jsonb_agg(jsonb_build_object('id',a.id,'created_at',a.created_at,'mode',a.mode,'route',a.route,
      'provider_name',a.provider_name,'model',a.model,'used_fallback',a.used_fallback,'egress',a.egress,'latency_ms',a.latency_ms,
      'candidates_tried',a.candidates_tried,'tool_calls',a.tool_calls,'outcome',a.outcome) order by a.created_at)
      from ai_route_audit a where a.conversation_id=v_conv.id),'[]'::jsonb));
end $$;
revoke all on function public.wl_platform_ai_conversations(uuid,text,int,int) from public,anon;
revoke all on function public.wl_platform_ai_conversation(uuid) from public,anon;
grant execute on function public.wl_platform_ai_conversations(uuid,text,int,int) to authenticated;
grant execute on function public.wl_platform_ai_conversation(uuid) to authenticated;
