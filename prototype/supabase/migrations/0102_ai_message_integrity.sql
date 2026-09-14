-- =====================================================================
-- 0102 — AI conversation integrity
--
-- A browser may only append USER messages. Assistant messages are written
-- by the authenticated WatchLog AI Edge Function through a service-role-only
-- RPC after the function has validated the caller/session and conversation.
-- This prevents a customer browser from forging assistant history.
-- =====================================================================

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
  if p_role <> 'user' then
    raise exception 'browser may append user messages only' using errcode = '42501';
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
  values(p_conversation_id,v_tenant,auth.uid(),'user',v_content,coalesce(p_payload,'{}'::jsonb))
  returning id into v_id;

  update ai_conversations set updated_at=now(),
    title=case when title='New conversation'
      then left(regexp_replace(v_content,'\s+',' ','g'),72)
      else title end
  where id=p_conversation_id;

  return jsonb_build_object('id',v_id,'ok',true);
end $$;
revoke all on function public.wl_ai_append_message(uuid,text,text,jsonb) from public, anon;
grant execute on function public.wl_ai_append_message(uuid,text,text,jsonb) to authenticated;

create or replace function public.wl_ai_append_assistant_message(
  p_conversation_id uuid,
  p_user_id uuid,
  p_content text,
  p_payload jsonb default '{}'::jsonb
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  v_conversation ai_conversations;
  v_id bigint;
  v_content text := trim(coalesce(p_content,''));
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;
  if p_user_id is null or length(v_content)=0 or length(v_content)>30000 then
    raise exception 'invalid assistant message' using errcode='22023';
  end if;
  select * into v_conversation from ai_conversations
   where id=p_conversation_id and user_id=p_user_id;
  if v_conversation.id is null then
    raise exception 'conversation not found' using errcode='22023';
  end if;

  insert into ai_messages(conversation_id,tenant_id,user_id,role,content,payload)
  values(v_conversation.id,v_conversation.tenant_id,p_user_id,'assistant',v_content,coalesce(p_payload,'{}'::jsonb))
  returning id into v_id;
  update ai_conversations set updated_at=now() where id=v_conversation.id;
  return jsonb_build_object('id',v_id,'ok',true);
end $$;
revoke all on function public.wl_ai_append_assistant_message(uuid,uuid,text,jsonb) from public, anon, authenticated;
grant execute on function public.wl_ai_append_assistant_message(uuid,uuid,text,jsonb) to service_role;
