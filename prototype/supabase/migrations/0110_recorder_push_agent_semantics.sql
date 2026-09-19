-- 0110: recorder-push liveness updates its virtual agent heartbeat.
create or replace function public.wl_push_liveness(p_token text)
returns jsonb language plpgsql security definer set search_path=public as $$
declare v_src push_sources;
begin
  select * into v_src from push_sources where token=p_token and enabled;
  if v_src.id is null then raise exception 'push token not recognised' using errcode='28000'; end if;
  update push_sources set last_push_at=now() where id=v_src.id;
  update agents set last_seen_at=now() where id=v_src.agent_id;
  return jsonb_build_object('ok',true,'recorded_at',now());
end $$;
revoke all on function public.wl_push_liveness(text) from public;
grant execute on function public.wl_push_liveness(text) to anon,authenticated;
