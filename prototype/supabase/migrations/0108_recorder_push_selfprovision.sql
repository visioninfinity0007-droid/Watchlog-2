-- =====================================================================
-- Recorder-push self-provisioning (0.4.6).
--
-- 0013 gave us the PC-free path: the recorder POSTs its own alarms to the
-- push bridge and a virtual agent owns them, so a site keeps reporting
-- with no PC running. The missing piece was *provisioning*: the only way
-- to mint a push token was wl_issue_push_token, which requires an
-- owner/admin session. The setup wizard has no such session -- it has the
-- agent key it just enrolled with, and it is the only thing that is ever
-- on the recorder's LAN holding the recorder credentials.
--
-- So: let an ENROLLED AGENT mint the push token for ITS OWN SITE, and the
-- wizard can point the recorder at us during install. After that the site
-- survives the PC being shut down, uninstalled, or rebuilt.
--
-- Why this grants nothing new:
--   * Authentication is the agent key hash -- the same check wl_heartbeat
--     and wl_ingest_events make. A caller who can run this can already
--     ingest events for this site, so the push token is not an escalation,
--     it is the same authority in a second shape.
--   * The token is scoped to the agent's OWN site. There is no site_id
--     parameter to tamper with.
--   * A recorder-push virtual agent may NOT mint tokens. Push credentials
--     can never be used to mint more push credentials.
--
-- IDEMPOTENT, unlike wl_issue_push_token which deliberately rotates. Setup
-- is re-run often (repair, reinstall, a second technician visit); rotating
-- on every run would silently invalidate the config already written into
-- the recorder and leave a site that looks configured but is deaf, and it
-- would accumulate a dead virtual agent per run.
-- =====================================================================

create or replace function public.wl_agent_issue_push_token(
  p_agent_id  uuid,
  p_agent_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent     agents;
  v_new_agent uuid;
  v_token     text;
begin
  select * into v_agent
    from agents a
   where a.id = p_agent_id
     and a.agent_key_hash = encode(sha256(p_agent_key::bytea), 'hex');

  if v_agent.id is null then
    raise exception 'agent authentication failed' using errcode = '28000';
  end if;

  if coalesce(v_agent.device_driver, '') = 'recorder-push' then
    raise exception 'a recorder-push agent cannot issue push tokens'
      using errcode = '42501';
  end if;

  -- Reuse this site's live push source if it already has one.
  select ps.token into v_token
    from push_sources ps
   where ps.site_id = v_agent.site_id
     and ps.enabled
   order by ps.created_at desc
   limit 1;

  if v_token is null then
    -- A virtual agent to own the pushed events. agent_key_hash is required
    -- but never used: this agent authenticates by push token, not by key.
    insert into agents (tenant_id, site_id, agent_key_hash, hostname,
                        device_driver, last_seen_at)
    values (v_agent.tenant_id, v_agent.site_id,
            md5(gen_random_uuid()::text || gen_random_uuid()::text),
            'Recorder push', 'recorder-push', now())
    returning id into v_new_agent;

    insert into push_sources (tenant_id, site_id, agent_id)
    values (v_agent.tenant_id, v_agent.site_id, v_new_agent)
    returning token into v_token;
  end if;

  return jsonb_build_object(
    'ok', true,
    'token', v_token,
    'site_id', v_agent.site_id,
    'note', 'Point the recorder at the WatchLog push bridge with this token.');
end $$;

comment on function public.wl_agent_issue_push_token(uuid, text) is
  'Mint (or return) the recorder-push token for the calling agent''s own site. '
  'Agent-key authenticated, idempotent, and refused to recorder-push agents.';

-- Agents call as anon carrying their key, exactly like wl_heartbeat.
revoke all on function public.wl_agent_issue_push_token(uuid, text) from public;
grant execute on function public.wl_agent_issue_push_token(uuid, text)
  to anon, authenticated;
