-- =====================================================================
-- 0087 - DH-XVR1B08-I clip-export truth correction
--
-- The exact recorder datasheet documents network backup/playback and CGI/SDK
-- interoperability. The prior row incorrectly turned "WatchLog get_clip is not
-- implemented/field-validated" into "recorder unsupported". Keep product truth
-- and WatchLog implementation truth separate, as 0061 requires.
-- =====================================================================

update public.recorder_capabilities
   set verdict = 'supported',
       evidence_class = 'OFFICIAL_DOCUMENTED',
       ai_location = 'recorder',
       read_supported = true,
       write_supported = false,
       safety_class = 'read',
       constraints = 'Recorder documents recording playback and network backup; WatchLog playback/export path still requires implementation and field validation on this unit.',
       watchlog_impl = 'get_clip() intentionally unimplemented; network playback/export integration required',
       source_ids = array['DAHUA-S1'],
       notes = 'Hardware/network backup support is official-documented. Do not offer Retrieve Footage until WatchLog implementation is hardware-validated.',
       updated_at = now()
 where vendor = 'Dahua'
   and model = 'DH-XVR1B08-I'
   and capability = 'clip_export'
   and firmware_applicability = '';

-- Preserve the field evidence wording as implementation-path evidence, but make
-- explicit that it did not prove the recorder itself lacks network backup.
update public.recorder_field_evidence
   set notes = 'WatchLog clip retrieval was unsupported on the validated 0.4.1 path; this is an implementation-path limitation, not proof the recorder lacks network backup. Physical Ch5 no-signal fault remained during that field test.'
 where id = 'FIELD-AKSS-001';
