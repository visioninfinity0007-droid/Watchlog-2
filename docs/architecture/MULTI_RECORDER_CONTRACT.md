# WatchLog Multi-Recorder Contract

Status: **FOUNDATION CONTRACT — implementation may proceed only within these invariants**

Applies to: database, Agent, installer/setup, evidence/archive/recovery, Site Control, recorder push, portal health and future tests.

## 1. Architecture

WatchLog keeps **one current Agent authority per site** and allows that Agent installation to manage **one or more recorders**.

Canonical hierarchy:

Tenant
  └─ Site
      ├─ Agent authority
      │   ├─ Recorder A
      │   ├─ Recorder B
      │   └─ Recorder N
      └─ Cameras
          ├─ Recorder A / channel 1
          ├─ Recorder A / channel 2
          ├─ Recorder B / channel 1
          └─ Recorder N / channel X

Recorder is a first-class deterministic entity between Site and Camera.

Do **not** model one recorder as one WatchLog site, one tenant, or one Windows service. Multiple Agent machines for a site remain a separate high-availability/fencing concern and do not change recorder identity.

## 2. Security boundary

The model is never the recorder-selection or authorization boundary.

Tenant/site/recorder/camera authorization, recorder assignment, evidence access, write approval and capability checks are deterministic application/Postgres controls.

Recorder credentials remain on-site. No password, API key, recorder session secret or equivalent credential is stored in Supabase.

Network connectivity remains outbound-only. Multi-recorder support does not introduce port-forwarding, VPN or inbound recorder-management access.

## 3. Recorder identity

Each configured recorder has:

- a cloud recorders.id UUID;
- tenant_id;
- site_id;
- optional assigned current Agent identity where required operationally;
- stable local recorder key/UUID used by the Agent;
- owner/operator display name;
- vendor/model/driver/firmware facts when actually observed;
- capability/evidence records scoped to that recorder;
- configured/enabled state;
- timestamps/audit metadata.

A recorder's local network address is local operational configuration, not its canonical cloud identity.

No identity may be inferred solely from vendor/model family.

## 4. Camera identity

cameras.id remains the canonical camera identity and existing camera UUIDs must be preserved during migration.

Channel number is **not** site-unique.

Target uniqueness:

UNIQUE(recorder_id, channel)

Legacy uniqueness:

UNIQUE(site_id, channel)

must be removed only after every existing configured camera is attached to exactly one recorder.

Two recorders may both have channel 1 without collision.

Camera/site/tenant lineage remains explicit even when recorder_id is present so existing tenant-scoped read models remain understandable and auditable.

## 5. Existing-site migration

For every existing single-recorder site:

1. create exactly one default/primary recorder row;
2. preserve the site's existing recorder vendor/model/driver facts on that recorder where supported by evidence;
3. attach every existing camera to that recorder;
4. preserve every existing camera.id;
5. preserve events, incidents, evidence, health history, reports, rules, names, purposes and topology through those camera IDs;
6. only then replace the site/channel uniqueness constraint with recorder/channel uniqueness.

The migration must be idempotent and fail closed if camera-to-recorder attribution is ambiguous.

Unknown stays Unknown. A historical site/model fact is not field proof of every recorder capability.

## 6. Backward compatibility

Existing single-recorder Agents must continue to function during rollout.

Legacy camera sync:

wl_sync_cameras(...)
    -> resolve the site's single/default recorder
    -> sync cameras under that recorder

New multi-recorder runtime uses explicit recorder-aware RPCs. Preferred conceptual surface:

wl_sync_recorders(...)
wl_sync_recorder_cameras(...)

Exact names/signatures follow repository conventions, but recorder identity must be explicit on the new path.

Legacy behavior must fail closed if a site has multiple active recorders and the caller does not identify which recorder owns the submitted channels. Never silently assign a multi-recorder payload to an arbitrary recorder.

## 7. Local Agent configuration and credentials

Target local shape:

%ProgramData%\WatchLog\recorders.json

%ProgramData%\WatchLog\Secrets\recorders\
    <local-recorder-id>.dpapi
    <local-recorder-id>.dpapi
    ...

recorders.json contains non-secret configuration only.

Each recorder credential is stored independently using the existing approved Windows secret mechanism.

Legacy singleton migration must be transactional:

read legacy config/credential
-> create local recorder identity
-> copy/write new credential
-> decrypt/verify
-> write recorder registry
-> verify complete runtime config
-> commit migration
-> only then retire legacy singleton form

Repair/Upgrade must preserve or restore the previous working state if conversion fails.

Adding a recorder later must not require reinstalling WatchLog.

## 8. Runtime model

One Agent process manages independent recorder contexts.

Conceptually:

RecorderContext
  local_id
  cloud_recorder_id
  display_name
  driver
  local_address
  credential reference
  cameras
  health
  capabilities

Recorder operations must be independently bounded. One unreachable/slow recorder must not block event ingestion, health, recovery or evidence work for another recorder.

No global singleton recorder connection/session may remain in a path that needs recorder-specific behavior.

## 9. Failure and coverage semantics

Recorder failure is scoped to that recorder and its cameras.

Example:

Recorder A: LIVE
Recorder B: unavailable
Recorder C: LIVE

must not become "site offline".

Only cameras whose evidence path depends on Recorder B may become UNVERIFIED for the affected interval.

Coverage truth remains:

- LIVE
- RECOVERED
- UNVERIFIED

Unverified time is never "nothing happened".

Site-level health may summarize recorder faults, but underlying recorder/camera truth remains inspectable and deterministic.

## 10. Event identity and deduplication

Any event/dedupe key that currently assumes channel is unique within a site must gain recorder namespace.

At minimum, recorder identity participates wherever channel is used to map or deduplicate:

- native events;
- Agent spool/reconciliation;
- camera maps;
- health transitions;
- recording/recovery state;
- archive scans;
- configuration drift;
- recorder-push attribution.

Conceptual key:

recorder_id + channel + device/event identity

Never resolve an event only as site + channel on a multi-recorder site.

## 11. Capabilities

Capability truth becomes recorder-scoped.

A mixed-vendor site may have different capability/evidence verdicts for each recorder.

A site does not automatically support a capability because one recorder supports it.

For camera operations:

camera_id
 -> recorder_id
 -> exact recorder capability/evidence
 -> permitted operation

Evidence classes remain:

FIELD_VERIFIED > OFFICIAL_DOCUMENTED > IMPLEMENTED_UNVERIFIED > UNSUPPORTED > UNKNOWN

Code existence, vendor family or another recorder's capability never upgrades capability truth.

## 12. Evidence, archive and recovery routing

Recorder-dependent evidence work is always routed:

camera_id -> recorder_id -> local RecorderContext -> exact recorder -> exact channel

Never search multiple NVRs for "channel 4".

Incident clips, bounded archive reads, snapshots requiring recorder access and recovered evidence must all use this mapping.

A failed retrieval from one recorder must not cause retry storms or blocking against unrelated recorders.

## 13. Site Control and writes

Recorder-level actions require explicit recorder_id.

Camera-level actions resolve recorder deterministically through camera_id -> recorder_id.

The model never chooses a recorder.

Existing approval, safety-class, tenant and capability gates continue to apply independently of recorder selection.

## 14. Recorder push

Recorder-push identity must be recorder-specific before multi-recorder recorder-push is considered complete.

Inbound recorder-originated events must establish recorder identity before channel-to-camera resolution.

Do not use one ambiguous site-level push identity for several recorders.

## 15. Recorder health

Health must support recorder-level current state and transitions without collapsing all recorders into one NVR state.

Required semantics include:

- recorder state;
- current/last observed liveness;
- credential/auth state where supportable without exposing secrets;
- configured/observed channel count;
- recording/storage facts only where actually verified;
- reason/provenance;
- observed timestamp.

Portal/customer language should normally group affected cameras under the recorder/root cause instead of emitting many duplicate alerts.

## 16. Portal and reporting

Business reporting remains primarily site/camera/activity/coverage oriented.

Recorder is operational provenance, not a business KPI.

Use recorder detail primarily for:

- System Health;
- Cameras & Evidence grouping;
- setup/configuration;
- root-cause grouping;
- capabilities;
- evidence/archive diagnostics.

Customer-facing copy must not expose internal capture/review mechanics, driver names, RPC/schema/queue vocabulary or secrets.

## 17. Recorder removal/disable semantics

Removing/disabling a recorder must never delete historical lineage automatically.

Historical cameras/events/incidents/evidence/reports remain attributable to the recorder.

Default behavior for an ordinary secondary recorder is to disable future runtime use while preserving history.

**5.1 release boundary:** the immutable `continuity_owner` recorder cannot be disabled by ordinary Setup/Manage Recorders flows, even after preferred-primary moves. It owns the legacy singleton spool/health namespace; retiring it safely requires a separately designed quiesce + drain workflow. Fail closed rather than risk stranding pre-cutover events or health evidence.

A recorder with historical references must not be hard-deleted by ordinary customer/operator flows.

Re-adding the same physical recorder must not silently create duplicate active camera identities; reconciliation must be deterministic.

## 18. Rollout order

1. stabilize/reconcile Agent 5.0.27 baseline;
2. land this contract;
3. add database compatibility layer and tests;
4. add Agent multi-recorder registry/credential compatibility;
5. add independent RecorderContext runtime;
6. add installer/manage-recorders UI;
7. make health/evidence/archive/recovery/Site Control recorder-aware;
8. add portal recorder grouping;
9. run full upgrade/rollback and mixed-recorder integration suite;
10. perform real field acceptance.

No production migration, Agent deployment or customer configuration change is implied by repository implementation.

## 19. Minimum automated acceptance

Automated tests must prove at least:

- existing single-recorder sites backfill to one recorder without changing camera IDs;
- legacy wl_sync_cameras still works on a one-recorder site;
- legacy ambiguous sync fails closed on a multi-recorder site;
- Recorder A channel 1 and Recorder B channel 1 remain distinct;
- tenant A cannot read/write tenant B recorders;
- camera/recorder tenant+site lineage cannot cross;
- helpers/internal RPCs have the narrow intended ACL;
- event/dedupe identities do not collide across recorders;
- one recorder failure does not mark unrelated recorder cameras unavailable;
- evidence request for a camera resolves exactly one recorder;
- unsupported/unknown capabilities remain unsupported/unknown;
- adding/removing/disabling a recorder preserves historical camera/event lineage;
- installer upgrade preserves a legacy singleton credential or rolls back safely.

## 20. Real field acceptance

"Multi-recorder supported" requires a physical test, not CI alone.

Minimum field topology:

one WatchLog site
two physical recorders
overlapping channel numbers
preferably mixed vendors

Prove:

1. both recorders discovered/configured;
2. credentials remain independent and local;
3. cameras remain distinct despite overlapping channels;
4. events map to the correct camera/recorder;
5. one recorder outage does not stop the other;
6. camera/recorder health remains truthful;
7. recovery uses the correct recorder;
8. snapshots/evidence use the correct recorder;
9. bounded incident clip comes from the correct recorder;
10. reboot preserves both recorder contexts;
11. Repair/Upgrade preserves both or rolls back;
12. adding another recorder works without reinstall;
13. disabling/removing one recorder preserves history;
14. portal groups/root-causes the fault correctly;
15. report coverage remains truthful.

Until these are field-tested, implementation is not field proof.
