-- =====================================================================
-- 0041 - Incident clip failure-path authorization hardening
--
-- 0040 validates tenant/site/claim ownership before upload and completion.
-- Apply the same ordering to the failure path: never delete chunks for a
-- request until the authenticated agent has proven it owns that claim.
-- =====================================================================

create or replace function public.wl_agent_fail_clip(
  p_agent_id uuid,p_agent_key text,p_request_id uuid,p_reason text,p_unsupported boolean default false
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_request public.incident_clip_requests;
  v_status text := case when coalesce(p_unsupported,false) then 'unsupported' else 'failed' end;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  select * into v_request
    from public.incident_clip_requests
   where id=p_request_id
     and tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and claimed_by_agent_id=v_agent.id
     and status='processing';
  if v_request.id is null then
    raise exception 'clip request not claimed by this agent' using errcode='42501';
  end if;

  delete from public.incident_clip_chunks where request_id=v_request.id;
  update public.incident_clip_requests
     set status=v_status,
         error_message=left(coalesce(nullif(btrim(p_reason),''),'Recorder could not provide this footage'),300),
         completed_at=now()
   where id=v_request.id;

  return jsonb_build_object('ok',true,'status',v_status);
end
$$;

revoke all on function public.wl_agent_fail_clip(uuid,text,uuid,text,boolean) from public;
grant execute on function public.wl_agent_fail_clip(uuid,text,uuid,text,boolean) to anon,authenticated;
