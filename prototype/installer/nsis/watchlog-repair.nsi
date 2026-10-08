; WatchLog Existing-Site Repair/Upgrade - NOT a first-time installer.
; Carries the Site Agent/runtime payload plus the Setup UI that provides Manage Recorders
; and Site Status. It never runs recorder discovery or the first-run setup wizard.

Unicode true

!define APPNAME "WatchLog Repair/Upgrade"
!ifndef APPVERSION
  !define APPVERSION "5.1.4"
!endif
!define PUBLISHER "Vision Infinity"
!define TASKNAME "WatchLog Agent"
!define DATAROOT "$COMMONPROGRAMDATA\WatchLog"
!define ARPKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\WatchLog"
!define CANDIDATE "${DATAROOT}\repair-candidate"
!define RESULTFILE "${DATAROOT}\repair-upgrade-result.ini"
!define STARTMENU "$SMPROGRAMS\WatchLog"

Name "${APPNAME}"
!ifndef OUTFILE
  !define OUTFILE "WatchLog-Repair-Upgrade.exe"
!endif
OutFile "${OUTFILE}"
RequestExecutionLevel admin
InstallDir "$PROGRAMFILES64\WatchLog"
SetCompressor /SOLID lzma
!ifdef ICON
  Icon "${ICON}"
  UninstallIcon "${ICON}"
!endif

!include "MUI2.nsh"
!include "LogicLib.nsh"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Repair or update this WatchLog site"
!define MUI_WELCOMEPAGE_TEXT "This tool is only for a PC where WatchLog is already connected.$\r$\n$\r$\nIt validates the new Agent against this site's existing encrypted credentials, CCTV recorder and WatchLog cloud identity before replacing the working version.$\r$\n$\r$\nIt does not run recorder discovery or ask for recorder credentials again."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "WatchLog update completed"
!define MUI_FINISHPAGE_TEXT "The new WatchLog Agent is online, every recorder that was reachable before the update is reachable again, and online-update polling has been proven.$\r$\n$\r$\nFuture approved WatchLog updates can now be delivered remotely."
!insertmacro MUI_PAGE_FINISH
; Repair/Upgrade writes the current uninstaller (below), so it carries the uninstall pages.
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

VIProductVersion "${APPVERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileVersion" "${APPVERSION}"
VIAddVersionKey "ProductVersion" "${APPVERSION}"
VIAddVersionKey "FileDescription" "WatchLog existing-site Repair/Upgrade"
VIAddVersionKey "LegalCopyright" "${PUBLISHER}"

Function .onInit
  ReadRegStr $0 HKLM "${ARPKEY}" "InstallLocation"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${EndIf}
FunctionEnd

Section "Repair/Upgrade"
  ${IfNot} ${FileExists} "$INSTDIR\watchlog.ini"
    MessageBox MB_ICONSTOP|MB_OK "This PC does not have a complete existing WatchLog site. Use WatchLog-Setup.exe for a new installation."
    SetErrorLevel 20
    Quit
  ${EndIf}
  ${IfNot} ${FileExists} "${DATAROOT}\agent_state.json"
    MessageBox MB_ICONSTOP|MB_OK "This WatchLog site is not fully enrolled. Use WatchLog-Setup.exe to repair the site first."
    SetErrorLevel 20
    Quit
  ${EndIf}
  ${IfNot} ${FileExists} "${DATAROOT}\Secrets\agent_key.dpapi"
    MessageBox MB_ICONSTOP|MB_OK "The encrypted WatchLog site identity is incomplete. No files were changed. Use the full WatchLog installer."
    SetErrorLevel 20
    Quit
  ${EndIf}
  ${IfNot} ${FileExists} "${DATAROOT}\Secrets\nvr_credential.dpapi"
    MessageBox MB_ICONSTOP|MB_OK "This older installation has not yet migrated its recorder credential to the secure store. No files were changed. Use the full WatchLog installer once."
    SetErrorLevel 20
    Quit
  ${EndIf}

  DetailPrint "Staging WatchLog ${APPVERSION} without changing the installed version..."
  RMDir /r "${CANDIDATE}"
  CreateDirectory "${CANDIDATE}"
  SetOutPath "${CANDIDATE}"
  SetOverwrite on
  File "watchlog-agent.exe"
  File "watchlog-setup-ui.exe"
  File "run-agent.ps1"
  File "register-service.ps1"
  File "apply-remote-update.ps1"
  File "wl-upgrade.ps1"
  File "watchlog.defaults.ini"
  File "wl-repair-upgrade.ps1"

  DetailPrint "Validating the new Agent against this existing site before replacing files..."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "${CANDIDATE}\wl-repair-upgrade.ps1" -CandidateDir "${CANDIDATE}" -InstallDir "$INSTDIR" -ExpectedVersion "${APPVERSION}"' $9

  ${If} $9 != 0
    RMDir /r "${CANDIDATE}"

    ; The orchestrator writes a human-readable result outside the staged candidate.
    ; Read it before exiting so a field operator sees the real failed stage instead
    ; of getting stranded on NSIS's generic "Installation Aborted" page.
    ReadINIStr $7 "${RESULTFILE}" "repair" "stage"
    ReadINIStr $8 "${RESULTFILE}" "repair" "message"
    ReadINIStr $6 "${RESULTFILE}" "repair" "recovery"

    ${If} $7 == ""
      StrCpy $7 "Repair/Upgrade validation"
    ${EndIf}
    ${If} $8 == ""
      StrCpy $8 "WatchLog could not safely complete this update."
    ${EndIf}
    ${If} $6 == ""
      StrCpy $6 "Do not uninstall WatchLog. The detailed support log is preserved."
    ${EndIf}

    MessageBox MB_ICONSTOP|MB_OK "WatchLog update stopped.$\r$\n$\r$\n$8$\r$\n$\r$\nStage: $7$\r$\n$6$\r$\n$\r$\nSupport log: C:\ProgramData\WatchLog\repair-upgrade.log$\r$\nError code: $9"
    SetErrorLevel $9
    Quit
  ${EndIf}

  WriteRegStr HKLM "${ARPKEY}" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKLM "${ARPKEY}" "InstallLocation" "$INSTDIR"
  ; The component set this package installed (see watchlog.nsi): the Agent refuses an
  ; Agent-only update that needs newer components than these.
  WriteRegStr HKLM "${ARPKEY}" "ComponentsVersion" "${APPVERSION}"
  ; The uninstaller of THIS release, so a site upgraded from 5.0.x by Repair/Upgrade is
  ; uninstalled by code that knows what 5.1 added (recorder registry, per-recorder
  ; credentials, shortcuts, power baseline, recovery task).
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${ARPKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""

  ; The proven payload includes the Setup UI, so an upgraded site can add or repair
  ; recorders without reinstalling. Written only after success: a rolled-back site keeps
  ; its previous Setup UI and Start Menu. All users; an older per-admin copy is removed.
  SetShellVarContext current
  Delete "${STARTMENU}\WatchLog Site Status.lnk"
  Delete "${STARTMENU}\WatchLog Manage Recorders.lnk"
  Delete "${STARTMENU}\Uninstall WatchLog.lnk"
  SetShellVarContext all
  CreateDirectory "${STARTMENU}"
  CreateShortcut "${STARTMENU}\WatchLog Site Status.lnk" "$INSTDIR\watchlog-setup-ui.exe" '--status --config "$INSTDIR\watchlog.ini"' "$INSTDIR\watchlog-setup-ui.exe"
  CreateShortcut "${STARTMENU}\WatchLog Manage Recorders.lnk" "$INSTDIR\watchlog-setup-ui.exe" '--manage-recorders --config "$INSTDIR\watchlog.ini"' "$INSTDIR\watchlog-setup-ui.exe"
  CreateShortcut "${STARTMENU}\Uninstall WatchLog.lnk" "$INSTDIR\uninstall.exe"

  ; A multi-recorder site commits when the original recorder and every recorder that
  ; answered before the update are back. Say so plainly if others are still unreachable.
  ReadINIStr $7 "${RESULTFILE}" "recorders" "not_live_after"
  ${If} $7 != ""
  ${AndIf} $7 != "0"
    MessageBox MB_ICONINFORMATION|MB_OK "WatchLog was updated.$\r$\n$\r$\n$7 recorder(s) could not be reached before the update and still cannot be reached. Every recorder that was reachable before the update is back online.$\r$\n$\r$\nCheck them in WatchLog Site Status or WatchLog Manage Recorders." /SD IDOK
  ${EndIf}

  RMDir /r "${CANDIDATE}"
SectionEnd

Section "Uninstall"
  ; IDENTICAL in watchlog.nsi and watchlog-repair.nsi (a test compares them): a site upgraded
  ; by Repair/Upgrade gets this uninstaller too, so it removes what 5.1 added.
  ;
  ; schtasks /End kills the run-agent.ps1 LAUNCHER; the watchlog-agent.exe grandchild survives
  ; it. wl-upgrade.ps1 -Stage uninstall stops the task, launcher, Setup UI and Agent and proves
  ; the files unlocked (no backup), removes every WatchLog scheduled task (Agent, upgrade
  ; recovery, orphaned candidate preflight), restores the power settings recorded at install,
  ; and removes ProgramData state by the uninstall policy: support logs stay, identity,
  ; credentials, queues, caches and staging go. The deletes below repeat that policy for the
  ; files NSIS can name, so an uninstall still cleans up if PowerShell cannot run.
  IfFileExists "$INSTDIR\wl-upgrade.ps1" 0 +2
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\wl-upgrade.ps1" -Stage uninstall -InstallDir "$INSTDIR"' $9
  ExecWait '"$SYSDIR\schtasks.exe" /End /TN "${TASKNAME}"'
  ExecWait '"$SYSDIR\schtasks.exe" /Delete /TN "${TASKNAME}" /F'
  ExecWait '"$SYSDIR\schtasks.exe" /Delete /TN "${TASKNAME} Upgrade Recovery" /F'
  ; Shortcuts: all users (5.1.1+) and the installing admin's own profile (older installs).
  SetShellVarContext current
  Delete "${STARTMENU}\WatchLog Setup.lnk"
  Delete "${STARTMENU}\WatchLog Site Status.lnk"
  Delete "${STARTMENU}\WatchLog Manage Recorders.lnk"
  Delete "${STARTMENU}\Uninstall WatchLog.lnk"
  RMDir "${STARTMENU}"
  SetShellVarContext all
  Delete "${STARTMENU}\WatchLog Setup.lnk"
  Delete "${STARTMENU}\WatchLog Site Status.lnk"
  Delete "${STARTMENU}\WatchLog Manage Recorders.lnk"
  Delete "${STARTMENU}\Uninstall WatchLog.lnk"
  RMDir "${STARTMENU}"
  Delete "$INSTDIR\watchlog-agent.exe"
  Delete "$INSTDIR\watchlog-setup-ui.exe"
  Delete "$INSTDIR\run-agent.ps1"
  Delete "$INSTDIR\apply-remote-update.ps1"
  Delete "$INSTDIR\run-agent.cmd"
  Delete "$INSTDIR\register-service.ps1"
  Delete "$INSTDIR\READ ME FIRST.txt"
  Delete "$INSTDIR\watchlog.ini"
  Delete "$INSTDIR\setup.ico"
  Delete "$INSTDIR\yolov8n.onnx"  ; legacy: remove any externally-shipped model from older installs
  ; Leftovers that kept $INSTDIR alive after every uninstall, so RMDir below always
  ; failed and the folder (plus stale rollback images) survived forever.
  Delete "$INSTDIR\wl-upgrade.ps1"
  Delete "$INSTDIR\wl-upgrade-recover.ps1"
  Delete "$INSTDIR\watchlog.defaults.ini"
  Delete "$INSTDIR\watchlog-agent.exe.wlbak"
  Delete "$INSTDIR\watchlog-agent.exe.remote.bak"
  Delete "$INSTDIR\watchlog-agent.next.verify"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${ARPKEY}"

  ; Multi-recorder state follows the same rule as the legacy credential. recorders.json
  ; binds local recorder ids to this site's cloud recorder identities and names the
  ; per-recorder credentials under Secrets\recorders. Remove it BEFORE those credentials:
  ; a registry left without them made every later reinstall fail until someone deleted
  ; it by hand. Quarantined copies hold queued secondary events: they go too.
  Delete "${DATAROOT}\recorders.json"
  Delete "${DATAROOT}\recorders.json.tmp"
  Delete "${DATAROOT}\recorders.json.quarantine-*"
  RMDir /r "${DATAROOT}\Secrets\recorders"

  ; Remove the encrypted credential + agent key (the whole Secrets directory, with the
  ; runtime-health proof and the power baseline already applied above)
  ; and any legacy plaintext/blob remnants. A reinstall re-runs setup
  ; because is_enrolled requires a decryptable key, which is now gone.
  RMDir /r "${DATAROOT}\Secrets"

  ; IDENTITY-BOUND local state must go too. Keeping agent_state.json and the spool meant
  ; a PC uninstalled at customer A and reinstalled at customer B DRAINED A's queued events
  ; into B's site on first connect. WatchLog has no "keep this site for a reinstall"
  ; choice, so logs stay for support and identity, queued data and caches do not.
  Delete "${DATAROOT}\agent_state.json"
  Delete "${DATAROOT}\spool.sqlite"
  Delete "${DATAROOT}\spool.sqlite-wal"
  Delete "${DATAROOT}\spool.sqlite-shm"
  Delete "${DATAROOT}\health.sqlite"
  Delete "${DATAROOT}\health.sqlite-wal"
  Delete "${DATAROOT}\health.sqlite-shm"
  Delete "${DATAROOT}\analytics_spool.sqlite"
  Delete "${DATAROOT}\analytics_spool.sqlite-wal"
  Delete "${DATAROOT}\analytics_spool.sqlite-shm"
  Delete "${DATAROOT}\analytics_config.json"
  Delete "${DATAROOT}\analytics_status.json"
  Delete "${DATAROOT}\analytics_bootstrap_sent.json"
  Delete "${DATAROOT}\analytics_action_dedup.json"
  Delete "${DATAROOT}\recorder_identity.json"
  Delete "${DATAROOT}\camera_profiles.json"
  Delete "${DATAROOT}\recorder_auth_backoff.json"
  Delete "${DATAROOT}\last_live.json"
  Delete "${DATAROOT}\watchlog.env"
  Delete "${DATAROOT}\nvr_password.dpapi"
  Delete "${DATAROOT}\background-ready.json"
  Delete "${DATAROOT}\run-agent.pid"
  Delete "${DATAROOT}\upgrade-in-progress.json"
  ; 5.1.1: the site stamp of the queued data, another site's set-aside queue/health files
  ; (<name>.site-<id>-<time>), rows the server rejected (<spool>.rejected.jsonl) and the
  ; registry's write temps. All hold site data or identity.
  Delete "${DATAROOT}\site_runtime.json"
  Delete "${DATAROOT}\site_runtime.tmp"
  Delete "${DATAROOT}\agent_state.tmp"
  Delete "${DATAROOT}\*.site-*"
  Delete "${DATAROOT}\*.rejected.jsonl"
  Delete "${DATAROOT}\.recorders.*.tmp"
  ; Secondary recorders keep their own spool, health ledger and last-live marker here.
  RMDir /r "${DATAROOT}\recorders"
  ; Staging and rollback copies: a remote-update stage, the upgrade backup (a full payload
  ; copy) and an interrupted Repair candidate.
  RMDir /r "${DATAROOT}\remote-update"
  RMDir /r "${DATAROOT}\upgrade-backup"
  RMDir /r "${DATAROOT}\repair-candidate"
SectionEnd
