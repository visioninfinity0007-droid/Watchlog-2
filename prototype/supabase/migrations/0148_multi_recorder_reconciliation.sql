-- 0148 - Recorder-aware durable health / recording-storage reconciliation.
--
-- STACKED AFTER 0147. REPO ONLY until separately approved.
--
-- Keep the deployed RPC signatures. New Agent rows may carry recorder_id;
-- legacy rows omit it and continue to work only while the site has exactly one
-- configured recorder. On a multi-recorder site, an omitted recorder_id fails
-- closed before any write.
--
-- Preserved invariants from 0046/0047:
--   * per-id accepted / duplicate / rejected ACK contract;
--   * fail-soft typed parsing;
--   * one store epoch per batch;
--   * immutable ledger ordering;
--   * forward-only current-state watermarks;
--   * replay idempotence;
--   * no secret material.

create or replace function public.wl_try_uuid(p text)
returns uuid
language plpgsql
immutable
set search_path = public
as $function$
begin
  return p::uuid;
exception when others then
  return null;
end
$function$;

revoke all on function public.wl_try_uuid(text)
  from public,anon,authenticated,service_role;

alter table public.local_monitoring_checkpoints
  add column if not exists recorder_id uuid;

alter table public.local_monitoring_checkpoints
  drop constraint if exists local_monitoring_checkpoints_recorder_lineage_fkey;

alter table public.local_monitoring_checkpoints
  add constraint local_monitoring_checkpoints_recorder_lineage_fkey
  foreign key (recorder_id,tenant_id,site_id)
  references public.recorders(id,tenant_id,site_id)
  on delete restrict;

create index if not exists local_mon_ckpt_recorder_time_idx
  on public.local_monitoring_checkpoints(recorder_id,device_ts)
  where recorder_id is not null;

alter table public.storage_transitions
  add column if not exists recorder_id uuid;

alter table public.storage_transitions
  drop constraint if exists storage_transitions_recorder_lineage_fkey;

alter table public.storage_transitions
  add constraint storage_transitions_recorder_lineage_fkey
  foreign key (recorder_id,tenant_id,site_id)
  references public.recorders(id,tenant_id,site_id)
  on delete restrict;

create index if not exists storage_tx_recorder_effective_idx
  on public.storage_transitions(recorder_id,effective_at desc)
  where recorder_id is not null;

alter table public.recorder_health
  add column if not exists storage_reason_code text;
alter table public.recorder_health
  add column if not exists sto_observed_at timestamptz;
alter table public.recorder_health
  add column if not exists sto_observed_epoch text;
alter table public.recorder_health
  add column if not exists sto_observed_seq bigint;
alter table public.recorder_health
  add column if not exists sto_observed_ingest bigint;

-- Preserve existing single-recorder storage watermark/history in the additive
-- recorder-health read model where 0147 deterministically mirrored that Agent.
update public.recorder_health rh
   set storage_state=nh.storage_state,
       storage_reason_code=nh.storage_reason_code,
       sto_observed_at=nh.sto_observed_at,
       sto_observed_epoch=nh.sto_observed_epoch,
       sto_observed_seq=nh.sto_observed_seq,
       sto_observed_ingest=nh.sto_observed_ingest,
       updated_at=greatest(rh.updated_at,nh.updated_at)
  from public.nvr_health nh
 where nh.agent_id=rh.agent_id
   and nh.tenant_id=rh.tenant_id
   and nh.site_id=rh.site_id
   and exists (
     select 1 from public.recorders r
      where r.id=rh.recorder_id
        and r.tenant_id=rh.tenant_id
        and r.site_id=rh.site_id
        and r.is_primary
   );

-- =====================================================================
-- wl_reconcile_health
-- =====================================================================
create or replace function public.wl_reconcile_health(
  p_agent_id uuid,
  p_agent_key text,
  p_transitions jsonb,
  p_checkpoints jsonb,
  p_max_future_skew_seconds int default 300
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_now timestamptz := now();
  v_skew interval := make_interval(
    secs=>greatest(coalesce(p_max_future_skew_seconds,300),0)
  );
  v_legacy_recorder_id uuid := null;
  v_seen int := 0;
  v_valid int := 0;
  v_applied int := 0;
  v_rejected int := 0;
  v_clamped int := 0;
  v_rej_by jsonb := '{}'::jsonb;
  v_ck_seen int := 0;
  v_ck_valid int := 0;
  v_ck_applied int := 0;
  v_ck_rej int := 0;
  v_ck_deflt int := 0;
  v_ck_rej_by jsonb := '{}'::jsonb;
  v_accepted jsonb := '[]'::jsonb;
  v_duplicate jsonb := '[]'::jsonb;
  v_rejected_ids jsonb := '[]'::jsonb;
  v_ck_accepted jsonb := '[]'::jsonb;
  v_ck_duplicate jsonb := '[]'::jsonb;
  v_ck_rej_ids jsonb := '[]'::jsonb;
  v_n_epochs int := 0;
  v_mixed boolean := false;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if exists (
    select 1 from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
     where nullif(t->>'recorder_id','') is not null
  ) or exists (
    select 1 from jsonb_array_elements(coalesce(p_checkpoints,'[]'::jsonb)) x
     where nullif(x->>'recorder_id','') is not null
  ) then
    perform public.wl_assert_current_agent_authority(
      v_agent.id,v_agent.site_id
    );
  end if;

  -- Any legacy row needs a deterministic singleton recorder. On a true
  -- multi-recorder site wl_legacy_recorder_for_agent raises "ambiguous".
  if exists (
    select 1 from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
     where nullif(t->>'recorder_id','') is null
  ) or exists (
    select 1 from jsonb_array_elements(coalesce(p_checkpoints,'[]'::jsonb)) c
     where nullif(c->>'recorder_id','') is null
  ) then
    v_legacy_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);
  end if;

  select count(distinct coalesce(
           nullif(t->>'store_epoch',''),
           split_part(t->>'id',':',2)
         ))
    into v_n_epochs
    from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
    join public.cameras cm
      on cm.site_id=v_agent.site_id
     and cm.tenant_id=v_agent.tenant_id
     and cm.recorder_id=case
       when nullif(t->>'recorder_id','') is null then v_legacy_recorder_id
       else public.wl_try_uuid(t->>'recorder_id')
     end
     and cm.channel=t->>'entity'
   where lower(coalesce(t->>'layer','camera'))='camera'
     and coalesce(t->>'to','')<>''
     and coalesce(t->>'id','')<>''
     and public.wl_try_timestamptz(t->>'device_ts') is not null
     and public.wl_try_bigint(t->>'seq') is not null;
  v_mixed := v_n_epochs>1;

  with raw as (
    select
      t,
      t->>'id' as dedupe_key,
      t->>'entity' as channel,
      lower(coalesce(t->>'layer','camera')) as layer,
      lower(t->>'to') as to_raw,
      public.wl_try_timestamptz(t->>'device_ts') as device_ts,
      public.wl_try_bigint(t->>'seq') as seq,
      (nullif(t->>'recorder_id','') is not null) as recorder_present,
      case
        when nullif(t->>'recorder_id','') is null then v_legacy_recorder_id
        else public.wl_try_uuid(t->>'recorder_id')
      end as recorder_id
    from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
  ),
  classified as (
    select r.*,cm.id as camera_id,
      case
        when coalesce(r.dedupe_key,'')='' then 'missing_id'
        when r.layer<>'camera' then 'wrong_layer'
        when coalesce(r.to_raw,'')='' then 'missing_state'
        when r.device_ts is null then 'invalid_timestamp'
        when r.seq is null then 'invalid_sequence'
        when r.recorder_present and r.recorder_id is null then 'invalid_recorder_id'
        when r.recorder_id is null then 'missing_recorder'
        when rr.id is null then 'invalid_recorder'
        when cm.id is null then 'unmapped_channel'
        when v_mixed then 'mixed_epoch_batch'
        else 'valid'
      end as verdict
    from raw r
    left join public.recorders rr
      on rr.id=r.recorder_id
     and rr.tenant_id=v_agent.tenant_id
     and rr.site_id=v_agent.site_id
     and rr.is_configured
    left join public.cameras cm
      on cm.site_id=v_agent.site_id
     and cm.tenant_id=v_agent.tenant_id
     and cm.recorder_id=rr.id
     and cm.channel=r.channel
  ),
  valid as (
    select
      dedupe_key,camera_id,seq,device_ts,
      coalesce(nullif(t->>'store_epoch',''),split_part(t->>'id',':',2)) as store_epoch,
      case when device_ts>v_now+v_skew then v_now else device_ts end as effective_at,
      (device_ts>v_now+v_skew) as clamped,
      case when lower(coalesce(t->>'from','')) in (
        'operational','degraded','offline','unknown'
      ) then lower(t->>'from') else 'unknown' end as from_state,
      case when to_raw in (
        'operational','degraded','offline','unknown'
      ) then to_raw else 'unknown' end as to_state,
      case when lower(coalesce(t->>'reason','unknown')) in (
        'ok','unknown','probe_timeout','stale_frame','video_loss',
        'channel_missing','channel_disabled','nvr_unreachable',
        'nvr_auth_failed','agent_unreachable','storage_fault',
        'not_recording','tamper','disk_error','disk_full'
      ) then lower(t->>'reason') else 'unknown' end as reason,
      case when lower(coalesce(t->>'source','probe')) in (
        'native','probe','inventory','upper_layer'
      ) then lower(t->>'source') else 'probe' end as source
    from classified
    where verdict='valid'
  ),
  ins as (
    insert into public.camera_health_transitions(
      tenant_id,site_id,camera_id,from_state,to_state,reason_code,
      at,effective_at,received_at,source,dedupe_key,store_epoch,seq
    )
    select
      v_agent.tenant_id,v_agent.site_id,camera_id,from_state,to_state,reason,
      device_ts,effective_at,v_now,source,dedupe_key,store_epoch,seq
    from valid
    on conflict (dedupe_key) where dedupe_key is not null do nothing
    returning dedupe_key
  )
  select
    (select count(*) from raw),
    (select count(*) from valid),
    (select count(*) from ins),
    (select count(*) from classified where verdict<>'valid'),
    (select count(*) from valid where clamped),
    (select coalesce(jsonb_object_agg(verdict,c),'{}'::jsonb)
       from (
         select verdict,count(*) c
         from classified
         where verdict<>'valid'
         group by verdict
       ) z),
    (select coalesce(jsonb_agg(dedupe_key),'[]'::jsonb) from ins),
    (select coalesce(jsonb_agg(v.dedupe_key),'[]'::jsonb)
       from valid v
      where v.dedupe_key not in (select dedupe_key from ins)),
    (select coalesce(
       jsonb_agg(jsonb_build_object('id',dedupe_key,'reason',verdict)),
       '[]'::jsonb
     ) from classified where verdict<>'valid')
  into
    v_seen,v_valid,v_applied,v_rejected,v_clamped,v_rej_by,
    v_accepted,v_duplicate,v_rejected_ids;

  if not v_mixed then
    with batch_ids as (
      select distinct t->>'id' as dedupe_key
      from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
      where coalesce(t->>'id','')<>''
    ),
    authoritative as (
      select
        cht.camera_id,cht.effective_at,cht.store_epoch,cht.seq,
        cht.id as ingest,cht.to_state,cht.reason_code
      from public.camera_health_transitions cht
      join batch_ids b on b.dedupe_key=cht.dedupe_key
      where cht.site_id=v_agent.site_id
    ),
    latest as (
      select distinct on (camera_id)
        camera_id,to_state,reason_code,effective_at,store_epoch,seq,ingest
      from authoritative
      order by camera_id,effective_at desc,seq desc nulls last,ingest desc
    )
    update public.camera_health ch
       set health_state=l.to_state,
           reason_code=l.reason_code,
           observed_at=l.effective_at,
           observed_epoch=l.store_epoch,
           observed_seq=l.seq,
           observed_ingest=l.ingest,
           last_change_at=v_now,
           updated_at=v_now,
           last_offline_at=case
             when l.to_state='offline' then l.effective_at
             else ch.last_offline_at end,
           last_recovery_at=case
             when l.to_state='operational' and ch.health_state='offline'
             then l.effective_at else ch.last_recovery_at end
      from latest l
     where ch.camera_id=l.camera_id
       and (
         l.effective_at>coalesce(ch.observed_at,'-infinity'::timestamptz)
         or (
           l.effective_at=ch.observed_at
           and l.store_epoch is not distinct from ch.observed_epoch
           and l.seq>coalesce(ch.observed_seq,-1)
         )
         or (
           l.effective_at=ch.observed_at
           and l.store_epoch is distinct from ch.observed_epoch
           and l.ingest>coalesce(ch.observed_ingest,-1)
         )
       );
  end if;

  with raw as (
    select
      c->>'id' as checkpoint_id,
      c->>'store_epoch' as store_epoch,
      public.wl_try_bigint(c->>'seq') as seq_val,
      public.wl_try_timestamptz(c->>'device_ts') as device_ts,
      lower(coalesce(c->>'nvr_state','unknown')) as nvr_state,
      public.wl_try_int(c->>'cameras_observed') as cams_val,
      public.wl_try_bool(c->>'cycle_ok') as cycle_val,
      (c ? 'seq') as has_seq,
      (c ? 'cameras_observed') as has_cams,
      (c ? 'cycle_ok') as has_cycle,
      (nullif(c->>'recorder_id','') is not null) as recorder_present,
      case
        when nullif(c->>'recorder_id','') is null then v_legacy_recorder_id
        else public.wl_try_uuid(c->>'recorder_id')
      end as recorder_id
    from jsonb_array_elements(coalesce(p_checkpoints,'[]'::jsonb)) c
  ),
  classified as (
    select r.*,
      case
        when coalesce(r.checkpoint_id,'')='' then 'missing_id'
        when r.device_ts is null then 'invalid_timestamp'
        when r.recorder_present and r.recorder_id is null then 'invalid_recorder_id'
        when r.recorder_id is null then 'missing_recorder'
        when rr.id is null then 'invalid_recorder'
        else 'valid'
      end as verdict,
      (
        (r.has_seq and r.seq_val is null)
        or (r.has_cams and r.cams_val is null)
        or (r.has_cycle and r.cycle_val is null)
      ) as metadata_defaulted
    from raw r
    left join public.recorders rr
      on rr.id=r.recorder_id
     and rr.tenant_id=v_agent.tenant_id
     and rr.site_id=v_agent.site_id
     and rr.is_configured
  ),
  insc as (
    insert into public.local_monitoring_checkpoints(
      tenant_id,site_id,agent_id,recorder_id,
      checkpoint_id,store_epoch,agent_seq,device_ts,
      nvr_state,cameras_observed,cycle_ok
    )
    select
      v_agent.tenant_id,v_agent.site_id,v_agent.id,
      case when recorder_present then recorder_id else null end,
      checkpoint_id,store_epoch,coalesce(seq_val,0),device_ts,nvr_state,
      coalesce(cams_val,0),coalesce(cycle_val,true)
    from classified
    where verdict='valid'
    on conflict (checkpoint_id) do nothing
    returning checkpoint_id
  )
  select
    (select count(*) from raw),
    (select count(*) from classified where verdict='valid'),
    (select count(*) from insc),
    (select count(*) from classified where verdict<>'valid'),
    (select count(*) from classified where verdict='valid' and metadata_defaulted),
    (select coalesce(jsonb_object_agg(verdict,c),'{}'::jsonb)
       from (
         select verdict,count(*) c
         from classified
         where verdict<>'valid'
         group by verdict
       ) z),
    (select coalesce(jsonb_agg(checkpoint_id),'[]'::jsonb) from insc),
    (select coalesce(jsonb_agg(r.checkpoint_id),'[]'::jsonb)
       from classified r
      where r.verdict='valid'
        and r.checkpoint_id not in (select checkpoint_id from insc)),
    (select coalesce(
       jsonb_agg(jsonb_build_object('id',checkpoint_id,'reason',verdict)),
       '[]'::jsonb
     ) from classified where verdict<>'valid')
  into
    v_ck_seen,v_ck_valid,v_ck_applied,v_ck_rej,v_ck_deflt,v_ck_rej_by,
    v_ck_accepted,v_ck_duplicate,v_ck_rej_ids;

  update public.agents set last_seen_at=v_now where id=v_agent.id;

  return jsonb_build_object(
    'ok',true,
    'transitions_received',v_seen,
    'transitions_valid',v_valid,
    'transitions_applied',v_applied,
    'transitions_duplicate',v_valid-v_applied,
    'transitions_rejected',v_rejected,
    'transitions_rejected_by',v_rej_by,
    'transitions_clamped',v_clamped,
    'accepted_ids',v_accepted,
    'duplicate_ids',v_duplicate,
    'rejected',v_rejected_ids,
    'checkpoints_received',v_ck_seen,
    'checkpoints_valid',v_ck_valid,
    'checkpoints_applied',v_ck_applied,
    'checkpoints_rejected',v_ck_rej,
    'checkpoints_rejected_by',v_ck_rej_by,
    'checkpoints_field_defaulted',v_ck_deflt,
    'checkpoints_accepted_ids',v_ck_accepted,
    'checkpoints_duplicate_ids',v_ck_duplicate,
    'checkpoints_rejected_ids',v_ck_rej_ids,
    'server_time',v_now
  );
end
$function$;

revoke all on function public.wl_reconcile_health(
  uuid,text,jsonb,jsonb,int
) from public,anon,authenticated,service_role;
grant execute on function public.wl_reconcile_health(
  uuid,text,jsonb,jsonb,int
) to anon,authenticated;

-- =====================================================================
-- wl_reconcile_recording_storage
-- =====================================================================
create or replace function public.wl_reconcile_recording_storage(
  p_agent_id uuid,
  p_agent_key text,
  p_transitions jsonb,
  p_max_future_skew_seconds int default 300
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_now timestamptz := now();
  v_skew interval := make_interval(
    secs=>greatest(coalesce(p_max_future_skew_seconds,300),0)
  );
  v_legacy_recorder_id uuid := null;
  v_seen int := 0;
  v_valid int := 0;
  v_applied int := 0;
  v_rejected int := 0;
  v_rej_by jsonb := '{}'::jsonb;
  v_accepted jsonb := '[]'::jsonb;
  v_duplicate jsonb := '[]'::jsonb;
  v_rejected_ids jsonb := '[]'::jsonb;
  v_n_epochs int := 0;
  v_mixed boolean := false;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if exists (
    select 1 from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
     where nullif(t->>'recorder_id','') is not null
  ) then
    perform public.wl_assert_current_agent_authority(
      v_agent.id,v_agent.site_id
    );
  end if;

  if exists (
    select 1 from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
     where nullif(t->>'recorder_id','') is null
  ) then
    v_legacy_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);
  end if;

  select count(distinct coalesce(
           nullif(t->>'store_epoch',''),
           split_part(t->>'id',':',2)
         ))
    into v_n_epochs
    from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
   where lower(coalesce(t->>'layer','')) in ('camera_recording','nvr_storage')
     and coalesce(t->>'to','')<>''
     and coalesce(t->>'id','')<>''
     and public.wl_try_timestamptz(t->>'device_ts') is not null
     and public.wl_try_bigint(t->>'seq') is not null;
  v_mixed := v_n_epochs>1;

  with raw as (
    select
      t,
      t->>'id' as dedupe_key,
      lower(coalesce(t->>'layer','')) as layer,
      t->>'entity' as entity,
      lower(t->>'to') as to_raw,
      public.wl_try_timestamptz(t->>'device_ts') as device_ts,
      public.wl_try_bigint(t->>'seq') as seq,
      (nullif(t->>'recorder_id','') is not null) as recorder_present,
      case
        when nullif(t->>'recorder_id','') is null then v_legacy_recorder_id
        else public.wl_try_uuid(t->>'recorder_id')
      end as recorder_id
    from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
  ),
  classified as (
    select r.*,cm.id as camera_id,
      case
        when coalesce(r.dedupe_key,'')='' then 'missing_id'
        when r.layer not in ('camera_recording','nvr_storage') then 'wrong_layer'
        when coalesce(r.to_raw,'')='' then 'missing_state'
        when r.device_ts is null then 'invalid_timestamp'
        when r.seq is null then 'invalid_sequence'
        when r.recorder_present and r.recorder_id is null then 'invalid_recorder_id'
        when r.recorder_id is null then 'missing_recorder'
        when rr.id is null then 'invalid_recorder'
        when r.layer='camera_recording' and cm.id is null then 'unmapped_channel'
        when v_mixed then 'mixed_epoch_batch'
        else 'valid'
      end as verdict
    from raw r
    left join public.recorders rr
      on rr.id=r.recorder_id
     and rr.tenant_id=v_agent.tenant_id
     and rr.site_id=v_agent.site_id
     and rr.is_configured
    left join public.cameras cm
      on r.layer='camera_recording'
     and cm.site_id=v_agent.site_id
     and cm.tenant_id=v_agent.tenant_id
     and cm.recorder_id=rr.id
     and cm.channel=r.entity
  ),
  valid as (
    select
      dedupe_key,layer,camera_id,recorder_id,recorder_present,seq,device_ts,
      coalesce(nullif(t->>'store_epoch',''),split_part(t->>'id',':',2)) as store_epoch,
      case when device_ts>v_now+v_skew then v_now else device_ts end as effective_at,
      case when layer='nvr_storage'
        then case when to_raw in ('ok','degraded','fault','unknown')
                  then to_raw else 'unknown' end
        else case when to_raw in (
          'recording','not_recording','storage_fault','unknown'
        ) then to_raw else 'unknown' end
      end as to_state,
      case when layer='nvr_storage'
        then case when lower(coalesce(t->>'from','')) in (
          'ok','degraded','fault','unknown'
        ) then lower(t->>'from') else 'unknown' end
        else case when lower(coalesce(t->>'from','')) in (
          'recording','not_recording','storage_fault','unknown'
        ) then lower(t->>'from') else 'unknown' end
      end as from_state,
      case when lower(coalesce(t->>'reason','unknown')) in (
        'ok','unknown','probe_timeout','stale_frame','video_loss',
        'channel_missing','channel_disabled','nvr_unreachable',
        'nvr_auth_failed','agent_unreachable','storage_fault',
        'not_recording','tamper','disk_error','disk_full'
      ) then lower(t->>'reason') else 'unknown' end as reason,
      case when lower(coalesce(t->>'source','probe')) in (
        'native','probe','inventory','upper_layer'
      ) then lower(t->>'source') else 'probe' end as source
    from classified
    where verdict='valid'
  ),
  ins_rec as (
    insert into public.recording_transitions(
      tenant_id,site_id,camera_id,from_state,to_state,
      reason_code,at,effective_at,received_at,source,
      store_epoch,seq,dedupe_key
    )
    select
      v_agent.tenant_id,v_agent.site_id,camera_id,from_state,to_state,
      reason,device_ts,effective_at,v_now,source,store_epoch,seq,dedupe_key
    from valid
    where layer='camera_recording'
    on conflict (dedupe_key) where dedupe_key is not null do nothing
    returning dedupe_key
  ),
  ins_sto as (
    insert into public.storage_transitions(
      tenant_id,site_id,agent_id,recorder_id,
      from_state,to_state,reason_code,at,effective_at,received_at,
      source,store_epoch,seq,dedupe_key
    )
    select
      v_agent.tenant_id,v_agent.site_id,v_agent.id,
      case when recorder_present then recorder_id else null end,
      from_state,to_state,reason,device_ts,effective_at,v_now,
      source,store_epoch,seq,dedupe_key
    from valid
    where layer='nvr_storage'
    on conflict (dedupe_key) where dedupe_key is not null do nothing
    returning dedupe_key
  ),
  accepted as (
    select dedupe_key from ins_rec
    union all
    select dedupe_key from ins_sto
  )
  select
    (select count(*) from raw),
    (select count(*) from valid),
    (select count(*) from accepted),
    (select count(*) from classified where verdict<>'valid'),
    (select coalesce(jsonb_object_agg(verdict,c),'{}'::jsonb)
       from (
         select verdict,count(*) c
         from classified
         where verdict<>'valid'
         group by verdict
       ) z),
    (select coalesce(jsonb_agg(dedupe_key),'[]'::jsonb) from accepted),
    (select coalesce(jsonb_agg(v.dedupe_key),'[]'::jsonb)
       from valid v
      where v.dedupe_key not in (select dedupe_key from accepted)),
    (select coalesce(
       jsonb_agg(jsonb_build_object('id',dedupe_key,'reason',verdict)),
       '[]'::jsonb
     ) from classified where verdict<>'valid')
  into
    v_seen,v_valid,v_applied,v_rejected,v_rej_by,
    v_accepted,v_duplicate,v_rejected_ids;

  if not v_mixed then
    -- Camera recording current state remains keyed by camera_id; recorder
    -- identity was resolved before the ledger insert.
    with bids as (
      select distinct t->>'id' as dedupe_key
      from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
      where coalesce(t->>'id','')<>''
    ),
    a_cam as (
      select
        rt.camera_id,rt.effective_at,rt.store_epoch,rt.seq,
        rt.id as ingest,rt.to_state,rt.reason_code
      from public.recording_transitions rt
      join bids b on b.dedupe_key=rt.dedupe_key
      where rt.site_id=v_agent.site_id
    ),
    l_cam as (
      select distinct on (camera_id)
        camera_id,to_state,reason_code,effective_at,store_epoch,seq,ingest
      from a_cam
      order by camera_id,effective_at desc,seq desc nulls last,ingest desc
    )
    insert into public.camera_health as ch(
      camera_id,tenant_id,site_id,
      recording_state,recording_reason_code,
      rec_observed_at,rec_observed_epoch,rec_observed_seq,rec_observed_ingest,
      updated_at
    )
    select
      l.camera_id,v_agent.tenant_id,v_agent.site_id,
      l.to_state,l.reason_code,
      l.effective_at,l.store_epoch,l.seq,l.ingest,v_now
    from l_cam l
    on conflict (camera_id) do update
       set recording_state=excluded.recording_state,
           recording_reason_code=excluded.recording_reason_code,
           rec_observed_at=excluded.rec_observed_at,
           rec_observed_epoch=excluded.rec_observed_epoch,
           rec_observed_seq=excluded.rec_observed_seq,
           rec_observed_ingest=excluded.rec_observed_ingest,
           updated_at=v_now
     where (
       excluded.rec_observed_at>coalesce(ch.rec_observed_at,'-infinity'::timestamptz)
       or (
         excluded.rec_observed_at=ch.rec_observed_at
         and excluded.rec_observed_epoch is not distinct from ch.rec_observed_epoch
         and excluded.rec_observed_seq>coalesce(ch.rec_observed_seq,-1)
       )
       or (
         excluded.rec_observed_at=ch.rec_observed_at
         and excluded.rec_observed_epoch is distinct from ch.rec_observed_epoch
         and excluded.rec_observed_ingest>coalesce(ch.rec_observed_ingest,-1)
       )
     );

    -- Explicit recorder-aware storage current state.
    with bids as (
      select distinct t->>'id' as dedupe_key
      from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
      where coalesce(t->>'id','')<>''
    ),
    a_sto as (
      select
        st.recorder_id,st.effective_at,st.store_epoch,st.seq,
        st.id as ingest,st.to_state,st.reason_code
      from public.storage_transitions st
      join bids b on b.dedupe_key=st.dedupe_key
      where st.agent_id=v_agent.id
        and st.recorder_id is not null
    ),
    l_sto as (
      select distinct on (recorder_id)
        recorder_id,to_state,reason_code,effective_at,store_epoch,seq,ingest
      from a_sto
      order by recorder_id,effective_at desc,seq desc nulls last,ingest desc
    )
    -- updated_at stays the connectivity report's clock (as in 0147): a
    -- replayed storage transition must not make stale reachability look
    -- fresh. A row created here has no reachability, so it reads unknown.
    insert into public.recorder_health as rh(
      recorder_id,agent_id,tenant_id,site_id,
      storage_state,storage_reason_code,
      sto_observed_at,sto_observed_epoch,sto_observed_seq,sto_observed_ingest
    )
    select
      l.recorder_id,v_agent.id,v_agent.tenant_id,v_agent.site_id,
      l.to_state,l.reason_code,
      l.effective_at,l.store_epoch,l.seq,l.ingest
    from l_sto l
    on conflict (recorder_id,agent_id) do update
       set storage_state=excluded.storage_state,
           storage_reason_code=excluded.storage_reason_code,
           sto_observed_at=excluded.sto_observed_at,
           sto_observed_epoch=excluded.sto_observed_epoch,
           sto_observed_seq=excluded.sto_observed_seq,
           sto_observed_ingest=excluded.sto_observed_ingest
     where (
       excluded.sto_observed_at>coalesce(rh.sto_observed_at,'-infinity'::timestamptz)
       or (
         excluded.sto_observed_at=rh.sto_observed_at
         and excluded.sto_observed_epoch is not distinct from rh.sto_observed_epoch
         and excluded.sto_observed_seq>coalesce(rh.sto_observed_seq,-1)
       )
       or (
         excluded.sto_observed_at=rh.sto_observed_at
         and excluded.sto_observed_epoch is distinct from rh.sto_observed_epoch
         and excluded.sto_observed_ingest>coalesce(rh.sto_observed_ingest,-1)
       )
     );

    -- Legacy singleton storage current state remains exactly on nvr_health
    -- (and is mirrored onto the singleton recorder below).
    with bids as (
      select distinct t->>'id' as dedupe_key
      from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
      where coalesce(t->>'id','')<>''
    ),
    a_sto as (
      select
        st.effective_at,st.store_epoch,st.seq,
        st.id as ingest,st.to_state,st.reason_code
      from public.storage_transitions st
      join bids b on b.dedupe_key=st.dedupe_key
      where st.agent_id=v_agent.id
        and st.recorder_id is null
    ),
    l_sto as (
      select
        to_state,reason_code,effective_at,store_epoch,seq,ingest
      from a_sto
      order by effective_at desc,seq desc nulls last,ingest desc
      limit 1
    )
    insert into public.nvr_health as nh(
      agent_id,tenant_id,site_id,
      storage_state,storage_reason_code,
      sto_observed_at,sto_observed_epoch,sto_observed_seq,sto_observed_ingest,
      updated_at
    )
    select
      v_agent.id,v_agent.tenant_id,v_agent.site_id,
      l.to_state,l.reason_code,
      l.effective_at,l.store_epoch,l.seq,l.ingest,v_now
    from l_sto l
    on conflict (agent_id) do update
       set storage_state=excluded.storage_state,
           storage_reason_code=excluded.storage_reason_code,
           sto_observed_at=excluded.sto_observed_at,
           sto_observed_epoch=excluded.sto_observed_epoch,
           sto_observed_seq=excluded.sto_observed_seq,
           sto_observed_ingest=excluded.sto_observed_ingest,
           updated_at=v_now
     where (
       excluded.sto_observed_at>coalesce(nh.sto_observed_at,'-infinity'::timestamptz)
       or (
         excluded.sto_observed_at=nh.sto_observed_at
         and excluded.sto_observed_epoch is not distinct from nh.sto_observed_epoch
         and excluded.sto_observed_seq>coalesce(nh.sto_observed_seq,-1)
       )
       or (
         excluded.sto_observed_at=nh.sto_observed_at
         and excluded.sto_observed_epoch is distinct from nh.sto_observed_epoch
         and excluded.sto_observed_ingest>coalesce(nh.sto_observed_ingest,-1)
       )
     );

    -- Mirror the same legacy storage state onto the singleton recorder's
    -- recorder_health row (same forward-only watermark). The owner read
    -- model reads recorder_health; without this a one-recorder site's
    -- storage stays frozen at the copy made when this migration ran.
    with bids as (
      select distinct t->>'id' as dedupe_key
      from jsonb_array_elements(coalesce(p_transitions,'[]'::jsonb)) t
      where coalesce(t->>'id','')<>''
    ),
    a_sto as (
      select
        st.effective_at,st.store_epoch,st.seq,
        st.id as ingest,st.to_state,st.reason_code
      from public.storage_transitions st
      join bids b on b.dedupe_key=st.dedupe_key
      where st.agent_id=v_agent.id
        and st.recorder_id is null
    ),
    l_sto as (
      select
        to_state,reason_code,effective_at,store_epoch,seq,ingest
      from a_sto
      order by effective_at desc,seq desc nulls last,ingest desc
      limit 1
    )
    -- updated_at stays the connectivity report's clock (as in 0147): a
    -- replayed storage transition must not make stale reachability look
    -- fresh. A row created here has no reachability, so it reads unknown.
    insert into public.recorder_health as rh(
      recorder_id,agent_id,tenant_id,site_id,
      storage_state,storage_reason_code,
      sto_observed_at,sto_observed_epoch,sto_observed_seq,sto_observed_ingest
    )
    select
      v_legacy_recorder_id,v_agent.id,v_agent.tenant_id,v_agent.site_id,
      l.to_state,l.reason_code,
      l.effective_at,l.store_epoch,l.seq,l.ingest
    from l_sto l
    where v_legacy_recorder_id is not null
    on conflict (recorder_id,agent_id) do update
       set storage_state=excluded.storage_state,
           storage_reason_code=excluded.storage_reason_code,
           sto_observed_at=excluded.sto_observed_at,
           sto_observed_epoch=excluded.sto_observed_epoch,
           sto_observed_seq=excluded.sto_observed_seq,
           sto_observed_ingest=excluded.sto_observed_ingest
     where (
       excluded.sto_observed_at>coalesce(rh.sto_observed_at,'-infinity'::timestamptz)
       or (
         excluded.sto_observed_at=rh.sto_observed_at
         and excluded.sto_observed_epoch is not distinct from rh.sto_observed_epoch
         and excluded.sto_observed_seq>coalesce(rh.sto_observed_seq,-1)
       )
       or (
         excluded.sto_observed_at=rh.sto_observed_at
         and excluded.sto_observed_epoch is distinct from rh.sto_observed_epoch
         and excluded.sto_observed_ingest>coalesce(rh.sto_observed_ingest,-1)
       )
     );
  end if;

  update public.agents set last_seen_at=v_now where id=v_agent.id;

  return jsonb_build_object(
    'ok',true,
    'transitions_received',v_seen,
    'transitions_valid',v_valid,
    'transitions_applied',v_applied,
    'transitions_duplicate',v_valid-v_applied,
    'transitions_rejected',v_rejected,
    'transitions_rejected_by',v_rej_by,
    'accepted_ids',v_accepted,
    'duplicate_ids',v_duplicate,
    'rejected',v_rejected_ids,
    'server_time',v_now
  );
end
$function$;

revoke all on function public.wl_reconcile_recording_storage(
  uuid,text,jsonb,int
) from public,anon,authenticated,service_role;
grant execute on function public.wl_reconcile_recording_storage(
  uuid,text,jsonb,int
) to anon,authenticated;
