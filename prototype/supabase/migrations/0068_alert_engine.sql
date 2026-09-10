-- =====================================================================
-- 0068 — Alert engine: immediate push for incidents that can't wait for the daily report.
--
-- The daily intelligence report (0067) is a digest. Some incidents warrant a push the
-- moment they are promoted — after-hours access to the armory should not wait until the
-- next morning. This is the dispatch layer on top of the 0065 incident model:
--
--   * per incident+channel, AT MOST ONE dispatch ever (unique key = dedup),
--   * atomic CLAIM (for update skip locked + on-conflict) so two senders never double-send,
--   * severity threshold so info-level incidents don't spam a phone.
--
-- Delivery itself stays the report-runner's job (Evolution WhatsApp / email), entitlement-
-- and recipient-gated; this only decides WHAT to send and records that it was sent.
-- =====================================================================

create table if not exists public.alert_dispatches (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  incident_id   uuid not null,                 -- provenance -> intel_incidents.id
  channel       text not null,                 -- 'whatsapp' | 'email' | ...
  severity      text not null,
  status        text not null default 'claimed' check (status in ('claimed','sent','failed')),
  claimed_at    timestamptz not null default now(),
  sent_at       timestamptz,
  detail_json   jsonb not null default '{}'::jsonb,
  unique (incident_id, channel)                -- one dispatch per incident per channel
);
create index if not exists alert_dispatches_site_idx on public.alert_dispatches (site_id, claimed_at desc);
alter table public.alert_dispatches enable row level security;

-- ---------------------------------------------------------------------
-- Claim the incidents that warrant an immediate push on a channel, at/above a severity
-- threshold, that have not already been dispatched. Idempotent + concurrency-safe: the
-- returned set is exactly the rows THIS call inserted, so a re-run or a second sender
-- gets nothing to re-send.
-- ---------------------------------------------------------------------
create or replace function public.wl_claim_alerts(
  p_site_id uuid,
  p_channel text,
  p_min_severity text default 'warning',
  p_limit int default 50
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  v_rank int;
  v_out  jsonb;
begin
  v_rank := case lower(p_min_severity) when 'critical' then 0 when 'info' then 2 else 1 end;
  with cand as (
    select ii.*
      from intel_incidents ii
     where ii.site_id = p_site_id
       and (case ii.severity when 'critical' then 0 when 'warning' then 1 else 2 end) <= v_rank
       and ii.status = 'open'
       and not exists (select 1 from alert_dispatches d
                        where d.incident_id = ii.id and d.channel = p_channel)
     order by (case ii.severity when 'critical' then 0 when 'warning' then 1 else 2 end), ii.occurred_at
     limit p_limit
     for update skip locked
  ),
  ins as (
    insert into alert_dispatches (tenant_id, site_id, incident_id, channel, severity, detail_json)
    select c.tenant_id, c.site_id, c.id, p_channel, c.severity,
           jsonb_build_object('incident_type', c.incident_type, 'occurred_at', c.occurred_at,
                              'camera_id', c.camera_id, 'detail', c.detail_json)
      from cand c
    on conflict (incident_id, channel) do nothing
    returning incident_id, severity, detail_json
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'incident_id', incident_id, 'severity', severity, 'detail', detail_json)
           order by case severity when 'critical' then 0 when 'warning' then 1 else 2 end), '[]'::jsonb)
    into v_out from ins;
  return v_out;
end $$;
revoke all on function public.wl_claim_alerts(uuid,text,text,int) from public, anon, authenticated;
grant execute on function public.wl_claim_alerts(uuid,text,text,int) to service_role;

-- ---------------------------------------------------------------------
-- Record the outcome of a claimed dispatch. A failed send stays failed (not re-claimed by
-- wl_claim_alerts because the dispatch row exists) — retry is an explicit, separate decision.
-- ---------------------------------------------------------------------
create or replace function public.wl_mark_alert_sent(
  p_incident_id uuid,
  p_channel text,
  p_ok boolean,
  p_detail jsonb default '{}'::jsonb
) returns void
language sql
volatile
security definer
set search_path = public
as $$
  update public.alert_dispatches
     set status = case when p_ok then 'sent' else 'failed' end,
         sent_at = now(),
         detail_json = detail_json || coalesce(p_detail, '{}'::jsonb)
   where incident_id = p_incident_id and channel = p_channel;
$$;
revoke all on function public.wl_mark_alert_sent(uuid,text,boolean,jsonb) from public, anon, authenticated;
grant execute on function public.wl_mark_alert_sent(uuid,text,boolean,jsonb) to service_role;
