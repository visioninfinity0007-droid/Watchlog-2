-- 0112: agent-visible recorder-push delivery truth.
--
-- Read-only status for the enrolled PC agent. It exposes no push token. A recorder
-- configuration read-back is NOT enough to call PC-off coverage verified; only a real
-- recorder POST advances push_sources.last_push_at via wl_push_liveness/wl_ingest_push.
create or replace function public.wl_agent_push_status(p_agent_id uuid, p_agent_key text)
returns jsonb
language plpgsql
security definer
set search_path=public
as $$
declare
  v_agent agents;
  v_src push_sources;
begin
  select * into v_agent
    from agents a
   where a.id=p_agent_id
     and a.agent_key_hash=encode(sha256(p_agent_key::bytea),'hex');
  if v_agent.id is null then
    raise exception 'agent authentication failed' using errcode='28000';
  end if;

  select * into v_src
    from push_sources ps
   where ps.site_id=v_agent.site_id and ps.enabled
   order by ps.created_at desc
   limit 1;

  if v_src.id is null then
    return jsonb_build_object(
      'enabled',false,
      'delivery_verified',false,
      'last_push_at',null
    );
  end if;

  return jsonb_build_object(
    'enabled',true,
    'delivery_verified',v_src.last_push_at is not null,
    'created_at',v_src.created_at,
    'last_push_at',v_src.last_push_at
  );
end $$;

revoke all on function public.wl_agent_push_status(uuid,text) from public;
grant execute on function public.wl_agent_push_status(uuid,text) to anon,authenticated;
