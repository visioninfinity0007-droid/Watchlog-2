-- =====================================================================
-- 0061 — Recorder Capability Model (product-level, evidence-graded).
--
-- WatchLog must know, per exact recorder model/firmware, what a recorder can do
-- and how strongly we know it, BEFORE the portal or an AI offers/apples any
-- configuration. This is PRODUCT capability, deliberately separate from AGENT
-- capability (an agent shipping archive code != the attached recorder supports
-- WatchLog archive retrieval), and it is consumed by the Site Control layer
-- instead of hardcoded vendor assumptions.
--
-- Two axes are kept separate so provenance never gets laundered into a verdict:
--   * verdict        — what the capability IS: supported | unsupported | by_camera | unknown
--   * evidence_class — how strongly we know it: FIELD_VERIFIED > OFFICIAL_DOCUMENTED
--                      > IMPLEMENTED_UNVERIFIED > UNKNOWN
-- The Drive taxonomy's "UNSUPPORTED" = verdict 'unsupported' with a FIELD/OFFICIAL
-- evidence_class; its "UNKNOWN" = verdict 'unknown' + evidence_class 'UNKNOWN'. This
-- makes the two hard rules structural: an UNKNOWN verdict can never be read as
-- unsupported, and OFFICIAL documentation is never emitted as FIELD_VERIFIED.
--
-- Seeded here: the live client recorder (Dahua DH-XVR1B08-I) fully, from BOTH its
-- field evidence (FIELD-AKSS-001) and its official datasheet; plus two contrasting
-- models (a WizSense XVR that DOES document IVS, and a Hikvision AcuSense NVR) to
-- prove the model does NOT generalize the seed's "no IVS" across a vendor. Broad
-- multi-model ingestion from docs/research/*.md is a follow-on loader.
-- =====================================================================

create table if not exists public.recorder_capability_sources (
  id            text primary key,          -- e.g. 'DAHUA-S1', 'HIK-S1', 'FIELD-AKSS-001'
  vendor        text not null,
  source_type   text not null check (source_type in ('official','secondary','field')),
  doc_name      text,
  url           text,
  doc_version   text,
  retrieved_date date,
  notes         text
);

create table if not exists public.recorder_capabilities (
  id            uuid primary key default gen_random_uuid(),
  vendor        text not null,
  model         text not null,             -- exact model; preserve regional/firmware suffix
  series        text,
  firmware_applicability text not null default '',   -- '' = unspecified/baseline
  capability    text not null,             -- WatchLog generic key (see terminology maps)
  verdict       text not null check (verdict in ('supported','unsupported','by_camera','unknown')),
  evidence_class text not null check (evidence_class in
                   ('FIELD_VERIFIED','OFFICIAL_DOCUMENTED','IMPLEMENTED_UNVERIFIED','UNKNOWN')),
  ai_location   text check (ai_location in ('recorder','camera','both','na')),
  read_supported  boolean,
  write_supported boolean,
  safety_class  text check (safety_class in ('read','safe_write','high_risk','prohibited','na')),
  constraints   text,                       -- mutual exclusions, channel budgets, hazards
  watchlog_impl text,                       -- where/if WatchLog implements it
  source_ids    text[] not null default '{}',
  notes         text,
  updated_at    timestamptz not null default now(),
  unique (vendor, model, capability, firmware_applicability)
);

create index if not exists recorder_capabilities_model_idx
  on public.recorder_capabilities (vendor, model);

create table if not exists public.recorder_field_evidence (
  id            text primary key,           -- FIELD-AKSS-001
  site_id       uuid,
  vendor        text not null,
  model         text not null,
  firmware      text,
  capability    text not null,
  operation     text,                       -- read | write | read_write
  result        text,
  evidence_class text not null default 'FIELD_VERIFIED',
  test_date     date,
  agent_version text,
  before_state  text,
  after_state   text,
  read_back_verified boolean,
  notes         text
);

-- Reference data, not tenant data: locked to direct access; exposed only via the
-- SECURITY DEFINER read functions below (which the portal/AI call).
alter table public.recorder_capability_sources enable row level security;
alter table public.recorder_capabilities       enable row level security;
alter table public.recorder_field_evidence     enable row level security;

-- ---------------------------------------------------------------------
-- Sources
-- ---------------------------------------------------------------------
insert into public.recorder_capability_sources (id, vendor, source_type, doc_name, url, doc_version, retrieved_date, notes) values
 ('DAHUA-S1','Dahua','official','DH-XVR1B08-I datasheet (Cooper-I)','https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR1B08-I_V2_datasheet_20240229.pdf','Rev 002.000 (C)2024','2026-09-10','Intelligent Alarm = SMD Plus only; no IVS/Perimeter/Face/ANPR'),
 ('DAHUA-S3','Dahua','official','DH-XVR5108HS-I3 datasheet (XVR5000-I3)','https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR5108HS-I3_datasheet_20220530.pdf','Rev 002.000 (C)2022','2026-09-10','SMD 8ch + Perimeter 1-2ch + Face 1ch; AI Mode exclusive SMD|IVS&SMD|Face'),
 ('HIK-S1','Hikvision','official','DS-7616NXI-K1(B) AcuSense NVR datasheet','https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000007/S000000026/OFR000042/M000058877/Data_Sheet/Datasheet-of-DS-7616NXI-K1_NVRB_V4.74.000_20230209.pdf','V4.74.000 2023-02-09','2026-09-10','Recorder-side MD2.0 all-ch + Perimeter 1ch + Face; ANPR/PC/heatmap camera-side; one-of-three engines'),
 ('FIELD-AKSS-001','Dahua','field','Al-Khalid Main Site live probe + safe writes','','agent 0.4.1','2026-09-10','tools/dahua_probe.ps1 + drivers/dahua.py; site-specific, not manufacturer truth')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------
-- Field evidence log (the first field-verified profile)
-- ---------------------------------------------------------------------
insert into public.recorder_field_evidence
 (id, vendor, model, firmware, capability, operation, result, evidence_class, test_date, agent_version, before_state, after_state, read_back_verified, notes) values
 ('FIELD-AKSS-001','Dahua','DH-XVR1B08-I','capture exact firmware next probe',
  'smd_human_vehicle + time_ntp + channel_title + sensitivity + video_loss',
  'read_write','supported on validated path; IVS not exposed; Ch5/7/8 current VideoLoss',
  'FIELD_VERIFIED','2026-09-10','0.4.1',
  'Indoor Ch1-4 Human+Vehicle; DST on/NTP off; generic names',
  'Ch1-4 Human-only; GMT+05/DST off/NTP on; Ch1-5 meaningful names; Ch3 High',
  true,'WatchLog clip retrieval unsupported on validated path; physical Ch5 no-signal fault remains')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------
-- Capabilities — Dahua DH-XVR1B08-I (the LIVE client recorder), field + official
-- ---------------------------------------------------------------------
insert into public.recorder_capabilities
 (vendor, model, series, capability, verdict, evidence_class, ai_location, read_supported, write_supported, safety_class, constraints, watchlog_impl, source_ids, notes) values
 ('Dahua','DH-XVR1B08-I','Cooper-I','human_vehicle_classification','supported','FIELD_VERIFIED','recorder',true,true,'safe_write','SMD Plus on 4 analog ch only; DISABLED if IP extension enabled','drivers/dahua.py configManager SmartMotionDetect',array['FIELD-AKSS-001','DAHUA-S1'],'Human+Vehicle targets; indoor Vehicle disabled in field'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','sensitivity_config','supported','FIELD_VERIFIED','recorder',true,true,'safe_write','per-channel SMD sensitivity','drivers/dahua.py',array['FIELD-AKSS-001'],'Ch3 raised to High in field'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','line_crossing','unsupported','FIELD_VERIFIED','na',null,null,'na','No IVS/Perimeter on Cooper-I; VideoAnalyseRule has nothing to bind','software-defined analytic required (Handoff 10)',array['FIELD-AKSS-001','DAHUA-S1'],'field + official agree; do NOT generalize to other Dahua'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','intrusion','unsupported','FIELD_VERIFIED','na',null,null,'na','No IVS/Perimeter on Cooper-I','software-defined analytic required',array['FIELD-AKSS-001','DAHUA-S1'],'field + official agree'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','video_loss','supported','FIELD_VERIFIED','recorder',true,false,'read','present-tense via eventManager getEventIndexes','drivers/dahua.py current_faults() (0.4.2)',array['FIELD-AKSS-001','DAHUA-S1'],'authoritative camera-down signal; drives Site Health truth'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','tamper','supported','FIELD_VERIFIED','recorder',true,false,'read','VideoBlind index','drivers/dahua.py current_faults()',array['FIELD-AKSS-001','DAHUA-S1'],'checked in field (none active)'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','channel_title','supported','FIELD_VERIFIED','recorder',true,true,'safe_write','recorder rejects & and apostrophe in OSD text','drivers/dahua.py ChannelTitle',array['FIELD-AKSS-001'],'WatchLog keeps full names; OSD uses safe ASCII'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','time_ntp_config','supported','FIELD_VERIFIED','recorder',true,true,'safe_write','Locales/NTP/global setCurrentTime','drivers/dahua.py',array['FIELD-AKSS-001','DAHUA-S1'],'DST off + NTP on + GMT+5 applied in field'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','snapshot','supported','FIELD_VERIFIED','recorder',true,false,'read','snapshot.cgi; video-loss ch returns black placeholder','drivers/dahua.py get_snapshot',array['FIELD-AKSS-001'],'do not use snapshot alone for liveness'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','event_stream','supported','FIELD_VERIFIED','recorder',true,false,'read','eventManager attach','drivers/dahua.py stream_events',array['FIELD-AKSS-001'],'fresh live events confirmed'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','storage_health','unknown','IMPLEMENTED_UNVERIFIED','recorder',true,false,'read','storageDevice.cgi','drivers/dahua.py storage_status (not field-proven)',array['DAHUA-S1'],'code exists; fails safe to UNKNOWN'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','recording_mode','unknown','IMPLEMENTED_UNVERIFIED','recorder',true,false,'read','RecordMode','drivers/dahua.py recording_status',array['DAHUA-S1'],'config != proof of writing frames'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','clip_export','unsupported','FIELD_VERIFIED','na',null,null,'na','not exposed on validated WatchLog path (recorder may have GUI/RTSP/USB backup)','get_clip() intentionally unimplemented',array['FIELD-AKSS-001','DAHUA-S1'],'UI must not offer Retrieve Footage for this recorder'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','face_detection','unsupported','OFFICIAL_DOCUMENTED','na',null,null,'na','Cooper-I Intelligent Alarm = SMD Plus only','',array['DAHUA-S1'],'datasheet-exhaustive'),
 ('Dahua','DH-XVR1B08-I','Cooper-I','anpr_lpr','unsupported','OFFICIAL_DOCUMENTED','na',null,null,'na','not on Cooper-I','',array['DAHUA-S1'],NULL),
 ('Dahua','DH-XVR1B08-I','Cooper-I','people_counting','unsupported','OFFICIAL_DOCUMENTED','na',null,null,'na','not on Cooper-I','',array['DAHUA-S1'],NULL),
 ('Dahua','DH-XVR1B08-I','Cooper-I','heatmap','unsupported','OFFICIAL_DOCUMENTED','na',null,null,'na','not on Cooper-I','',array['DAHUA-S1'],NULL)
on conflict (vendor, model, capability, firmware_applicability) do nothing;

-- ---------------------------------------------------------------------
-- Contrast 1 — Dahua DH-XVR5108HS-I3 DOES document IVS (official only): proves
-- the KB never generalizes the seed's "no IVS" across the Dahua brand.
-- ---------------------------------------------------------------------
insert into public.recorder_capabilities
 (vendor, model, series, capability, verdict, evidence_class, ai_location, read_supported, write_supported, safety_class, constraints, source_ids) values
 ('Dahua','DH-XVR5108HS-I3','XVR5000-I3','human_vehicle_classification','supported','OFFICIAL_DOCUMENTED','recorder',true,true,'safe_write','SMD 8 ch; AI Mode exclusive (SMD | IVS&SMD | Face)',array['DAHUA-S3']),
 ('Dahua','DH-XVR5108HS-I3','XVR5000-I3','line_crossing','supported','OFFICIAL_DOCUMENTED','recorder',true,true,'safe_write','Perimeter 1-2 ch, <=10 IVS/ch; needs AI Mode IVS&SMD; IP extension disables it',array['DAHUA-S3']),
 ('Dahua','DH-XVR5108HS-I3','XVR5000-I3','intrusion','supported','OFFICIAL_DOCUMENTED','recorder',true,true,'safe_write','Perimeter 1-2 ch; same AI-Mode exclusion',array['DAHUA-S3'])
on conflict (vendor, model, capability, firmware_applicability) do nothing;

-- ---------------------------------------------------------------------
-- Contrast 2 — Hikvision DS-7616NXI-K1 (AcuSense NVR, official/code-only):
-- recorder-side vs camera-side split, never FIELD_VERIFIED.
-- ---------------------------------------------------------------------
insert into public.recorder_capabilities
 (vendor, model, series, capability, verdict, evidence_class, ai_location, read_supported, write_supported, safety_class, constraints, source_ids) values
 ('Hikvision','DS-7616NXI-K1','K-series AcuSense','human_vehicle_classification','supported','OFFICIAL_DOCUMENTED','recorder',true,true,'safe_write','MD2.0 all-ch; one-of-three engines (face|MD2.0|perimeter)',array['HIK-S1']),
 ('Hikvision','DS-7616NXI-K1','K-series AcuSense','line_crossing','supported','OFFICIAL_DOCUMENTED','recorder',true,true,'safe_write','recorder-side Perimeter 1-ch (K1); ISAPI Smart write sub-path UNKNOWN',array['HIK-S1']),
 ('Hikvision','DS-7616NXI-K1','K-series AcuSense','anpr_lpr','by_camera','OFFICIAL_DOCUMENTED','camera',null,null,'na','camera-side only even on this AcuSense NVR',array['HIK-S1']),
 ('Hikvision','DS-7616NXI-K1','K-series AcuSense','people_counting','by_camera','OFFICIAL_DOCUMENTED','camera',null,null,'na','camera-side only',array['HIK-S1']),
 ('Hikvision','DS-7616NXI-K1','K-series AcuSense','heatmap','by_camera','OFFICIAL_DOCUMENTED','camera',null,null,'na','camera-side only',array['HIK-S1'])
on conflict (vendor, model, capability, firmware_applicability) do nothing;

-- ---------------------------------------------------------------------
-- Resolver: effective capability for a model, honest UNKNOWN default.
-- ---------------------------------------------------------------------
create or replace function public.wl_recorder_capability(
  p_vendor text, p_model text, p_capability text, p_firmware text default null
) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(
    (select jsonb_build_object(
        'vendor', c.vendor, 'model', c.model, 'capability', c.capability,
        'verdict', c.verdict, 'evidence_class', c.evidence_class,
        'ai_location', c.ai_location, 'read', c.read_supported, 'write', c.write_supported,
        'safety_class', c.safety_class, 'constraints', c.constraints,
        'watchlog_impl', c.watchlog_impl, 'source_ids', to_jsonb(c.source_ids), 'notes', c.notes)
       from recorder_capabilities c
      where c.vendor = p_vendor and c.model = p_model and c.capability = p_capability
        and (p_firmware is null or c.firmware_applicability in ('', p_firmware))
      order by (c.firmware_applicability = coalesce(p_firmware,'')) desc,
               case c.evidence_class
                 when 'FIELD_VERIFIED' then 0 when 'OFFICIAL_DOCUMENTED' then 1
                 when 'IMPLEMENTED_UNVERIFIED' then 2 else 3 end
      limit 1),
    -- No row -> honest unknown (never invent support or unsupported)
    jsonb_build_object('vendor', p_vendor, 'model', p_model, 'capability', p_capability,
                       'verdict', 'unknown', 'evidence_class', 'UNKNOWN',
                       'read', null, 'write', null, 'safety_class', 'na',
                       'source_ids', '[]'::jsonb, 'notes', 'no capability evidence on record')
  );
$$;

create or replace function public.wl_recorder_profile(
  p_vendor text, p_model text
) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object(
           'capability', c.capability, 'verdict', c.verdict,
           'evidence_class', c.evidence_class, 'ai_location', c.ai_location,
           'read', c.read_supported, 'write', c.write_supported,
           'safety_class', c.safety_class, 'constraints', c.constraints,
           'source_ids', to_jsonb(c.source_ids)) order by c.capability), '[]'::jsonb)
    from recorder_capabilities c
   where c.vendor = p_vendor and c.model = p_model;
$$;

revoke all on function public.wl_recorder_capability(text,text,text,text) from public;
revoke all on function public.wl_recorder_profile(text,text) from public;
grant execute on function public.wl_recorder_capability(text,text,text,text) to authenticated, service_role;
grant execute on function public.wl_recorder_profile(text,text) to authenticated, service_role;
