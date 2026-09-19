-- 0108: authenticated site agent can provision/reuse its own recorder-push source.
create or replace function public.wl_agent_issue_push_token(p_agent_id uuid, p_agent_key text)
returns jsonb language plpgsql security definer set search_path=public as $$
declare v_agent agents; v_new_agent uuid; v_token text;
begin
  select * into v_agent from agents a
   where a.id=p_agent_id and a.agent_key_hash=encode(sha256(p_agent_key::bytea),'hex');
  if v_agent.id is null then raise exception 'agent authentication failed' using errcode='28000'; end if;
  if coalesce(v_agent.device_driver,'')='recorder-push' then
    raise exception 'a recorder-push agent cannot issue push tokens' using errcode='42501';
  end if;
  select ps.token into v_token from push_sources ps
   where ps.site_id=v_agent.site_id and ps.enabled order by ps.created_at desc limit 1;
  if v_token is null then
    insert into agents(tenant_id,site_id,agent_key_hash,hostname,device_driver,last_seen_at)
    values(v_agent.tenant_id,v_agent.site_id,md5(gen_random_uuid()::text||gen_random_uuid()::text),
           'Recorder push','recorder-push',now()) returning id into v_new_agent;
    insert into push_sources(tenant_id,site_id,agent_id)
    values(v_agent.tenant_id,v_agent.site_id,v_new_agent) returning token into v_token;
  end if;
  return jsonb_build_object('ok',true,'token',v_token,'site_id',v_agent.site_id,
    'note','Point the recorder at the WatchLog push bridge with this token.');
end $$;
revoke all on function public.wl_agent_issue_push_token(uuid,text) from public;
grant execute on function public.wl_agent_issue_push_token(uuid,text) to anon,authenticated;
