# WatchLog production acceptance matrix

Generated from `docs/acceptance/production_acceptance_matrix.json` by `tools/acceptance_matrix.py render`. Rules: see the tool docstring.

## Scope 5.1.2

| family | capability | impl | unit | integ | Dahua field | Hik field | remote obs | fail/recover | Dahua verdict | Hik verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 core | `agent_enrollment` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `agent_heartbeat` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `agent_identity` | PASS | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `agent_self_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `agent_capability_reporting` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `agent_runtime_status` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `remote_update_v1` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `remote_diagnostics` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `multi_agent_fencing` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `offline_spooling` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 1 core | `automatic_recovery` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `multi_recorder` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_identity` | PASS | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_auth` | PASS | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_probe_v2` | PASS | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_registry` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_reconnect` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_capability_sync` | PASS | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_failure_isolation` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_inventory` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_channel_mapping` | PASS | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_snapshot` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_live_snapshot` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_health` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_video_loss` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_offline_detection` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_tamper_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `native_event_stream` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `motion_event` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `video_loss_event` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `video_restore_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `tamper_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `camera_disconnect_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `camera_reconnect_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `storage_fault_event` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `disk_full_event` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `disk_error_event` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `human_detection_event` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `vehicle_detection_event` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `line_crossing_event` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `intrusion_event` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `local_analytics_runtime` | PASS | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `person_detection` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `event_snapshot` | PASS | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `incident_clip` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `pre_event_clip` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `post_event_clip` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `manual_clip_request` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `evidence_metadata` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `evidence_integrity` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `evidence_attribution` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `archive_search` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `archive_clip_retrieval` | PASS | PASS | PASS | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `gap_detection` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `gap_recovery` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `recovery_interval_processing` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `recording_availability` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `recorder_health` | PASS | PASS | PASS | PASS | NOT_RUN | PASS | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `camera_health_state` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `storage_health` | FAIL | NOT_RUN | NOT_RUN | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `recording_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `event_stream_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `spool_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `credential_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `health_state_transitions` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 9 recording | `recording_state` | FAIL | NOT_RUN | NOT_RUN | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 9 recording | `recording_storage_available` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 9 recording | `latest_recording_timestamp` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 9 recording | `per_camera_recording_check` | FAIL | NOT_RUN | NOT_RUN | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 10 storage | `disk_inventory` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 10 storage | `disk_health` | FAIL | NOT_RUN | NOT_RUN | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 10 storage | `disk_capacity` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 10 storage | `disk_free_space` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 10 storage | `disk_failure` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 10 storage | `disk_full` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `site_control_runtime` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `refresh_inventory` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `refresh_capabilities` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `reconnect_recorder` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `run_recording_check` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `run_archive_check` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `run_acceptance_test` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `collect_diagnostics` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_update_check` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_update_download` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_update_verify` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_update_install` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_update_rollback` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_restart` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_log_collection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_acceptance_test` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `outbound_only` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `monitoring_coverage` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `coverage_gap_detection` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `recorder_unreachable_interval` | PASS | NOT_RUN | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `archive_recovery_coverage` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `machine_bound_credentials` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `per_recorder_credentials` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `signed_updates` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `command_authentication` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `sensitive_data_redaction` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `least_privilege_commands` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `tenant_isolation` | PASS | NOT_RUN | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `setup_camera_sync` | PASS | NOT_RUN | PASS | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |

## Scope later

| family | capability | impl | unit | integ | Dahua field | Hik field | remote obs | fail/recover | Dahua verdict | Hik verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 core | `agent_config_sync` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_discovery` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_add` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_remove` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_enable_disable` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_rename` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_credential_update` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_clock_check` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_push_configuration` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_configuration_read` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_discovery` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_name_sync` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_enable_disable` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_configuration_status` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_stream_probe` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_quality_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_obstruction_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_frozen_frame_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_dark_image_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_blur_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 3 camera | `camera_status_metadata` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `recorder_restart_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `alarm_input_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `alarm_output_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `face_detection_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 4 native events | `audio_alarm_event` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `vehicle_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `object_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `person_tracking` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `vehicle_tracking` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `restricted_zone` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `line_crossing` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `loitering` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `crowd_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `occupancy_counting` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `people_counting` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `object_left` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `object_removed` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `after_hours_activity` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `camera_scene_change` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `custom_detection_rule` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `analytics_rule_engine` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `analytics_confidence_threshold` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `analytics_schedule` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `analytics_roi` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `analytics_event_deduplication` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `operations_evidence_still` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `operations_evidence_clip` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `incident_snapshot` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `manual_snapshot` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `evidence_upload` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `evidence_retry` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 6 evidence | `evidence_retention` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `archive_processing` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `archive_frame_retrieval` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `archive_event_search` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `historical_snapshot` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `historical_analytics` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `historical_incident_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `recording_timeline` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 7 archive | `archive_integrity_check` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `agent_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `archive_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `analytics_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `cloud_connectivity_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 8 health | `configuration_health` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 9 recording | `recording_continuity` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 9 recording | `recording_gap_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 9 recording | `recording_schedule_read` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 9 recording | `recording_retention_estimate` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 10 storage | `disk_smart_status` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 10 storage | `storage_fault_history` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `request_snapshot` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `request_clip` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `probe_camera` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `probe_recorder` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `restart_agent_worker` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `run_health_check` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 12 remote maintenance | `remote_config_refresh` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_creation` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_classification` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_severity` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_evidence` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_acknowledgement` | PASS | NOT_RUN | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_escalation` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_assignment` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_resolution` | PASS | NOT_RUN | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_deduplication` | PASS | NOT_RUN | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `incident_correlation` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `rule_engine` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `schedule_rules` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `camera_rules` | PASS | PASS | PASS | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `site_rules` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 13 incidents | `notification_rules` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `pc_sleep_detection` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `agent_unreachable_interval` | PASS | PASS | N_A | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `camera_unreachable_interval` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `cloud_outage_tracking` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `analytics_coverage` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 14 coverage | `event_stream_coverage` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 15 config audit | `config_snapshot_requests` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 15 config audit | `recorder_config_snapshot` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 15 config audit | `camera_config_snapshot` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 15 config audit | `recording_config_snapshot` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 15 config audit | `analytics_config_snapshot` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 15 config audit | `configuration_diff` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 15 config audit | `configuration_drift_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 15 config audit | `configuration_change_history` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `credential_rotation` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `command_expiry` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `command_audit` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 16 security | `high_risk_action_approval` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |

## Scope gated

| family | capability | impl | unit | integ | Dahua field | Hik field | remote obs | fail/recover | Dahua verdict | Hik verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 recorder | `recorder_configuration_write` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 2 recorder | `recorder_restart` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `face_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `face_matching` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `license_plate_detection` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 5 local analytics | `license_plate_recognition` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `change_camera_name` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `change_recording_schedule` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `enable_recorder_analytic` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `disable_recorder_analytic` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `configure_motion` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `configure_smd` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `configure_line_crossing` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |
| 11 site control | `configure_intrusion` | FAIL | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_READY | NOT_READY |

**0/230 ready on Dahua, 0/230 ready on Hikvision.**

