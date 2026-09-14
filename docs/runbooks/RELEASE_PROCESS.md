# WatchLog release process (0.4.4+) — build once, promote the same bytes

The repeatable procedure for shipping a WatchLog installer to customers. The governing rule:
**one approved, immutable binary is the single source of truth** — website, portal and the update
manifest all point at the SAME version + build SHA + SHA-256. We never rebuild a different binary
when moving pilot → production; we promote the exact verified artifact.

## Channels
`internal → pilot → beta → production`. Al-Khalid validates on **pilot**; on success the SAME
artifact/hash is promoted to **production**.

## 1. Code → CI
Merge only on green CI (`ci.yml`, 6 jobs). CI includes the packaged-source proofs (acceptance
fail-closed, updater sign/verify, camera config e2e, recovery ingest, etc.).

## 2. Windows production build (real artifact)
Run **Windows Release** (`.github/workflows/windows-release.yml`, `workflow_dispatch`) from the
release ref. It builds the **production AI** agent (`build_exe.ps1 -WithAI`), the Qt Setup/Site
Status app, and the NSIS installer `WatchLog-Setup.exe`; verifies real payloads + `--selftest` +
wl-upgrade version gate; and emits SHA-256 for the agent exe, setup-ui exe and installer.
- Manual dispatch with `production=false` → **PILOT / UNSIGNED** (allowed for validation).
- Tag push `v*` or `production=true` → **PRODUCTION**, which **hard-fails without a valid signing
  cert** (`WATCHLOG_SIGN_PFX_B64`) and requires `WATCHLOG_SUPABASE_URL` / `_PUBLISHABLE_KEY` /
  `WATCHLOG_PUBLISHER_URL` repo config.

Capture from the run: installer filename, size, **SHA-256**, agent version, build Git SHA, run id,
signing state.

## 3. Packaged proofs (on the built binary)
`--version`, `--selftest`, `--status-json`, `--accept`, `--check-update`, `--support-bundle`, and
freeze-launch Setup + Site Status. (Windows Release already runs the core of these.)

## 4. Signing
- **Authenticode**: sign the installer/exes with the real cert. If unavailable → mark artifact
  `PILOT / UNSIGNED`; do not bypass.
- **Update manifest (Ed25519)**: sign with the out-of-band private key (never in git, never shipped;
  the agent holds only the public key). Without it, publish an unsigned manifest only to a non-
  production channel — the agent refuses unsigned production updates by policy.

## 5. Field-validate the EXACT artifact (pilot)
Run `docs/runbooks/RC_0.4.4_FIELD_VALIDATION.md` on SM-HP with the **same** `WatchLog-Setup-<ver>.exe`
that will be published (verify its SHA-256 matches the build). Prove install/upgrade, reboot,
live, recording, archive, agent/PC-outage recovery (historical timestamps + provenance + snapshot
where the decoder supports it), internet-outage spool, update, and rollback.

## 6. Publish (promote the same bytes)
Host the immutable versioned artifact on WatchLog infrastructure and repoint the canonical latest:
- versioned (immutable): `https://<watchlog-domain>/downloads/releases/<ver>/WatchLog-Setup-<ver>.exe`
- canonical latest: `https://<watchlog-domain>/downloads/WatchLog-Setup.exe` → current approved release
Never overwrite the bytes behind an existing versioned filename.

## 7. Manifest
Generate the update manifest from the built artifact's identity and sign it:
```bash
python tools/make_release_manifest.py --channel production --version <ver> \
  --build-sha <sha> --sha256 <hex> --size <bytes> \
  --url https://<watchlog-domain>/downloads/releases/<ver>/WatchLog-Setup-<ver>.exe \
  --privkey-b64 "$WATCHLOG_MANIFEST_PRIVKEY_B64" > manifest.json
```
Publish `manifest.json` to the WatchLog update endpoint. `download_url` must be WatchLog-controlled
HTTPS. `update_url` in the agent points at this endpoint (config-driven, not GitHub).

## 8. Website + Portal
Point the single "Download WatchLog for Windows" CTA (website) and the tenant Site-Setup download
(portal) at the canonical latest route — one route/API, not ten hard-coded links. Remove/deprecate
any customer-facing GitHub or legacy-installer links.

## 9. Consistency gate (release blocker)
```bash
python tools/check_release_consistency.py \
  --artifact-sha <A> --manifest-sha <A> --website-sha <A> --portal-sha <A> \
  --artifact-version <ver> --manifest-version <ver>
```
Must exit 0: **Website SHA = Portal SHA = Manifest SHA = Artifact SHA** (and versions match).

## 10. Promote pilot → production
After SM-HP pass, take the SAME artifact/hash validated on pilot and: update the production manifest,
the canonical website download, and the portal download to it. Do not rebuild.

## Publication gate (all required before "production")
CI green · Windows Release green · production AI packaged · packaged selftest green · Setup +
Site Status packaged · acceptance fail-closed · update package verified · **SM-HP install/upgrade +
live + recovery + rollback proof** · archive honestly classified · consistency gate green ·
signing state truthfully labelled (PILOT vs PRODUCTION). Do not promote an un-field-tested artifact.
