# WatchLog 0.4.4 — SM-HP / Al-Khalid field validation harness

The single, exact procedure that proves the 0.4.4 site appliance on real hardware: install once,
keep watching, recover supportable missed intelligence after an outage, distinguish LIVE / RECOVERED
/ UNVERIFIED coverage, update securely, and roll back a bad update automatically.

Everything below is **read-only against the recorder**. It changes no recorder setting. Recorder
credentials never leave the site; no secret is ever printed or collected.

> Status of this document: authored from the shipped agent CLI. The steps are executable; the
> **evidence is only real once run on SM-HP with the client's Dahua** (DH-XVR1B08-I @ the site LAN).
> Record every `*_JSON` line and exit code into the closure evidence file.

## 0. RC identity — record before the window
Run on the site PC (needs no config, no cloud):
```bash
watchlog-agent.exe --version
```
Capture: version (must be `0.4.4`), and the build SHA from `--accept`'s `runtime` check
(`ACCEPTANCE_JSON`). A build with no stamped SHA is **not** a release build — stop and rebuild.

## 1. Fresh install + enrollment
1. Run the branded installer. Enter site enrollment code + recorder IP / user / password.
2. Let discovery + login prove the recorder; classify each channel Active / Disabled / Ignore.
3. Confirm the setup **archive check** verdict (VERIFIED / EMPTY / UNSUPPORTED / FAILED). Record it.
4. Finish. The scheduled task must be registered and the agent running as SYSTEM at startup.

Prove the whole chain automatically:
```bash
watchlog-agent.exe --accept
```
Record `ACCEPTANCE_JSON`. Expected: `RESULT: ACCEPTED` (warnings allowed for a genuinely quiet
site or an empty/unsupported archive). Hard checks that MUST pass: `config, identity, cloud,
recorder, cameras, spool, security`. The `security` check must confirm **no plaintext recorder
password on disk** (it lives only in the encrypted DPAPI store).

## 2. Internet outage → recovery (the core promise)
1. Note the current time. Disconnect the site PC's WAN (leave the recorder recording).
2. Wait ≥ 15 min so a real monitoring gap forms; generate some real activity in view of an Active
   camera (walk through the entrance).
3. Reconnect WAN. Within a few minutes:
   * the local spool uploads its buffered events;
   * `recovery` opens a **recovery interval** for the exact missed window;
   * automatic backfill runs — recorder-native event replay AND WatchLog AI over the recovered
     footage — bounded, throttled, yielding to live.
4. Prove it:
```bash
watchlog-agent.exe --accept
```
   In the portal / daily intelligence for the site, confirm:
   * coverage now reports **LIVE + RECOVERED + UNVERIFIED** separately (never blended), summing to
     wall time;
   * recovered events carry `recovered` provenance and the **historical footage timestamp** (not the
     recovery time), with representative snapshots where footage was retrievable;
   * a period with neither live nor archive evidence stays UNVERIFIED (nothing invented).

Record the coverage-class figures and one recovered incident's timestamp + provenance.

## 3. Agent / PC outage → recovery
1. Stop the WatchLog task (or reboot the PC). The recorder keeps recording.
2. Cause real activity during the down window. Bring the agent back (reboot completes / task starts).
3. The persisted last-live vs now yields the missed interval; recovery runs as in §2.
4. Confirm recovered snapshots/events retain their **original historical timestamps** and there are
   **no duplicates** on a second pass (idempotent). Re-running `--accept` still ACCEPTED.

## 4. Secure self-update
Pre-req: publish a **validly Ed25519-signed** newer build to the site's `update_channel` manifest
(`update_url`), with correct `sha256` + `size`. The site's `update_public_key` must be set.
1. Check (read-only):
```bash
watchlog-agent.exe --check-update
```
   Expect `UPDATE_JSON` with `action: update` and the target version. An unsigned/mis-signed manifest
   MUST report `blocked` (`manifest_unsigned` / `bad_signature`) and apply nothing.
2. Apply (transactional):
```bash
watchlog-agent.exe --update
```
   Expect `UPDATE_APPLY_JSON` with `ok: true, stage: commit`. Then confirm:
   * `--version` now reports the new version; `--accept` still ACCEPTED;
   * enrollment + recorder credentials + local spool/recovery state all survived;
   * exactly one agent process is running.

## 5. Rollback (a bad update must self-heal)
1. Publish a validly signed build that fails to start / fails version verification (a controlled
   failure fixture), on an internal/pilot channel.
2. `watchlog-agent.exe --update`.
3. Expect `UPDATE_APPLY_JSON` with `ok: false` and `rolled_back: true`. Confirm:
   * `--version` reports the PREVIOUS working version;
   * the agent is running (single instance); `--accept` ACCEPTED;
   * no two agents, no zero agents.

## 6. Collect the evidence bundle (no secrets)
```bash
watchlog-agent.exe --support-bundle
```
Attach the resulting `.zip` (build identity, redacted config, non-secret identity, spool count,
redacted setup log). Verify by inspection that it contains **no** recorder password, agent key, or
enrollment code.

## Pass / fail
The window PASSES only when §1–§5 each meet their stated expectation and §6 is secret-free. Record,
per section: the command output (`ACCEPTANCE_JSON` / `UPDATE_JSON` / `UPDATE_APPLY_JSON`), exit
codes, portal coverage-class figures, and one recovered-intelligence sample. File it alongside
`docs/acceptance/`.

## Notes / current external blockers
- **Code-signing certificate** and the **Ed25519 release signing keypair** are external blockers:
  the installer/manifest signing steps in §4/§5 assume they are provisioned. Do not weaken signing to
  pass.
- Do **not** enable `operations_runtime_enabled` / `site_control_enabled` on the production site as
  part of validation — they are separate, deliberate deployment steps.
