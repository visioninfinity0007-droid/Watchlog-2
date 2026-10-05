; WatchLog Windows connector - authoritative production installer (NSIS).
;
; NSIS owns elevation, files, Windows registration, upgrade/uninstall and the
; background task. The customer setup experience itself is the branded
; windowed watchlog-setup-ui.exe, not a console process.

Unicode true

!define APPNAME "WatchLog"
; Single version source: build passes /DAPPVERSION from wl_version.py. The
; fallback must be kept in step (a contract test asserts it).
!ifndef APPVERSION
  !define APPVERSION "5.0.27"
!endif
!define PUBLISHER "Vision Infinity"
!define TASKNAME "WatchLog Agent"
; Context-independent ProgramData root: $COMMONPROGRAMDATA always resolves to
; C:\ProgramData regardless of shell-var context, so install and uninstall can
; never diverge. (The bare PROGRAMDATA constant is invalid in NSIS and silently
; broke credential/upgrade/uninstall path logic in 0.3.3 -> warning 6000.)
!define DATAROOT "$COMMONPROGRAMDATA\WatchLog"
!define ARPKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\WatchLog"
!define STARTMENU "$SMPROGRAMS\WatchLog"

Name "${APPNAME}"
!ifndef OUTFILE
  !define OUTFILE "WatchLog-Setup.exe"
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
!define MUI_WELCOMEPAGE_TITLE "Install WatchLog"
!define MUI_WELCOMEPAGE_TEXT "WatchLog connects this Windows PC to the CCTV recorder already installed at your site.$\r$\n$\r$\nThe next step opens WatchLog Setup to find the recorder, verify its local login, discover cameras and connect the site securely.$\r$\n$\r$\nNo port forwarding or inbound recorder access is required."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "WatchLog is installed"
!define MUI_FINISHPAGE_TEXT "WatchLog is set to start automatically with Windows.$\r$\n$\r$\nReturn to the WatchLog portal to confirm Site Health and Analytics. Local support logs are kept under C:\ProgramData\WatchLog."
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

VIProductVersion "${APPVERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileVersion" "${APPVERSION}"
VIAddVersionKey "ProductVersion" "${APPVERSION}"
VIAddVersionKey "FileDescription" "WatchLog Windows installer"
VIAddVersionKey "LegalCopyright" "${PUBLISHER}"

Section "Install"
  ; The transactional upgrade orchestrator must run BEFORE any binary is replaced, so extract it to
  ; a temp dir first (the real install-dir copy is written with the other files below).
  InitPluginsDir
  File "/oname=$PLUGINSDIR\wl-upgrade.ps1" "wl-upgrade.ps1"

  ; Two separate truths:
  ;   $5 = ANY previous WatchLog payload/site state exists and must be stopped/unlocked first.
  ;   $6 = previous site is fully enrolled, so setup/enrollment can be preserved.
  ; A stale/partial install can still have a live launcher/UI/agent and locked files, so
  ; preflight is intentionally NOT limited to fully enrolled sites.
  StrCpy $5 "0"
  StrCpy $6 "0"

  ${If} ${FileExists} "$INSTDIR\watchlog-agent.exe"
    StrCpy $5 "1"
  ${ElseIf} ${FileExists} "$INSTDIR\watchlog-setup-ui.exe"
    StrCpy $5 "1"
  ${ElseIf} ${FileExists} "$INSTDIR\run-agent.ps1"
    StrCpy $5 "1"
  ${ElseIf} ${FileExists} "$INSTDIR\watchlog.ini"
    StrCpy $5 "1"
  ${EndIf}

  ${If} ${FileExists} "$INSTDIR\watchlog.ini"
    ${If} ${FileExists} "${DATAROOT}\agent_state.json"
      StrCpy $6 "1"
      StrCpy $5 "1"
    ${EndIf}
  ${EndIf}

  ; Complete modern existing sites use the staged Repair/Upgrade path.
  ; This check happens BEFORE any process is stopped or installed file is touched.
  ${If} $6 == "1"
    ${If} ${FileExists} "${DATAROOT}\Secrets\agent_key.dpapi"
      ${If} ${FileExists} "${DATAROOT}\Secrets\nvr_credential.dpapi"
        MessageBox MB_ICONINFORMATION|MB_OK "WatchLog is already connected on this PC. For an existing site, use WatchLog-Repair-Upgrade.exe instead of the full installer. Repair/Upgrade validates the new Agent before replacing anything and does not run recorder discovery again."
        Abort "Existing connected site: use WatchLog-Repair-Upgrade.exe"
      ${EndIf}
    ${EndIf}
  ${EndIf}

  ; UPGRADE PREFLIGHT: suspend the WatchLog watchdog/task, stop the exact launcher,
  ; Setup UI and agent for THIS install, prove every replace-target file is unlocked,
  ; and back up the previous payload. $8 records a rollback-capable existing install.
  StrCpy $8 "0"
  ${If} $5 == "1"
    StrCpy $8 "1"
    DetailPrint "Closing the existing WatchLog processes before updating files..."
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage preflight -InstallDir "$INSTDIR"' $9
    ${If} $9 != 0
      MessageBox MB_ICONSTOP|MB_OK "WatchLog could not safely close the existing installation, so no update files were replaced. Close any open WatchLog window and run the installer again."
      Abort "Upgrade preflight failed; previous installation preserved"
    ${EndIf}
  ${EndIf}

  ; Replace the WHOLE core payload only after preflight has stopped the scheduled
  ; launcher, open Setup UI and agent and proved every replace-target file is unlocked.
  ; One extraction error anywhere is a failed upgrade: roll the complete previous
  ; payload back instead of leaving a mixed-version installation.
  SetOutPath "$INSTDIR"
  SetOverwrite try
  ClearErrors
  File "watchlog-agent.exe"
  File "watchlog-setup-ui.exe"
  File "run-agent.ps1"
  File "register-service.ps1"
  File "apply-remote-update.ps1"
  File "wl-upgrade.ps1"
  File "READ ME FIRST.txt"
  File "setup.ico"
  ${If} ${Errors}
    ${If} $8 == "1"
      DetailPrint "A WatchLog update file could not be replaced; restoring the previous installation..."
      ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage rollback -InstallDir "$INSTDIR"' $9
      ${If} $9 == 0
        MessageBox MB_ICONSTOP|MB_OK "WatchLog could not replace all update files safely. The previous working version was restored and its Agent restart was verified. No new version was kept."
      ${Else}
        MessageBox MB_ICONSTOP|MB_OK "WatchLog could not replace all update files safely. The previous files were restored, but the old Agent restart could not be proven. Do not uninstall anything. Restart Windows once and contact WatchLog support with C:\ProgramData\WatchLog\upgrade.log."
      ${EndIf}
    ${Else}
      MessageBox MB_ICONSTOP|MB_OK "WatchLog could not write the installation files safely. No working prior WatchLog installation was available to roll back."
    ${EndIf}
    Abort "WatchLog payload replacement failed"
  ${EndIf}
  SetOverwrite on

  ; UPGRADE VERSION TRUTH: before starting anything, verify the on-disk binary's file ProductVersion
  ; AND its runtime --version both equal this release. If the binary was not actually replaced, roll
  ; back and abort rather than register/start/report a version that is not installed.
  ; -VerifySetupUi: this installer also wrote the Setup UI, so its version must match too.
  ${If} $6 == "1"
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage verify-version -InstallDir "$INSTDIR" -ExpectedVersion "${APPVERSION}" -VerifySetupUi' $9
    ${If} $9 != 0
      ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage rollback -InstallDir "$INSTDIR"' $9
      ${If} $9 == 0
        MessageBox MB_ICONSTOP|MB_OK "WatchLog was not able to install the new version correctly. The previous working version was restored and its Agent restart was verified. No new version was kept."
        Abort "Installed version did not match; rollback proven"
      ${Else}
        MessageBox MB_ICONSTOP|MB_OK "WatchLog was not able to install the new version correctly. The previous files were restored, but WatchLog could not prove the old Agent restarted. Do not uninstall anything. Restart Windows once and contact WatchLog support with C:\ProgramData\WatchLog\upgrade.log."
        Abort "Installed version did not match; rollback restart not proven"
      ${EndIf}
    ${EndIf}
  ${EndIf}

  ; Keep existing site/enrollment configuration on upgrades. A fresh install
  ; receives public defaults only; the graphical setup writes recorder values.
  IfFileExists "$INSTDIR\watchlog.ini" +2 0
    File "/oname=watchlog.ini" "watchlog.defaults.ini"
  ; Always stage the defaults alongside, so --migrate-only can merge public keys that an
  ; EXISTING ini predates (push_bridge_url today). Without this an upgraded site can never
  ; receive a new public setting -- and the upgraded fleet is the fleet that matters.
  SetOverwrite try
  ClearErrors
  File "watchlog.defaults.ini"
  ${If} ${Errors}
    ${If} $8 == "1"
      ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage rollback -InstallDir "$INSTDIR"' $9
      ${If} $9 == 0
        MessageBox MB_ICONSTOP|MB_OK "WatchLog could not update its public defaults safely. The previous working version was restored and its Agent restart was verified."
      ${Else}
        MessageBox MB_ICONSTOP|MB_OK "WatchLog could not update its public defaults safely. The previous files were restored, but the old Agent restart could not be proven. Restart Windows once and contact WatchLog support with C:\ProgramData\WatchLog\upgrade.log."
      ${EndIf}
    ${Else}
      MessageBox MB_ICONSTOP|MB_OK "WatchLog could not write its public defaults safely. Setup stopped without claiming success."
    ${EndIf}
    Abort "WatchLog defaults replacement failed"
  ${EndIf}
  SetOverwrite on

  ; The AI model (yolov8n.onnx) is bundled inside watchlog-agent.exe (PyInstaller
  ; --add-data), so no separate model file is shipped. (Removed the vestigial
  ; File /nonfatal yolov8n.onnx that was never staged and only produced NSIS
  ; warning 7010, which is now fatal.)

  ${If} $6 == "1"
    DetailPrint "Updating the existing WatchLog installation..."
    ; Older builds stored nvr_password as plaintext in the INI. Migrate it into
    ; the ACL-restricted env credential file before the SYSTEM task restarts.
    ExecWait '"$INSTDIR\watchlog-setup-ui.exe" --migrate-only --config "$INSTDIR\watchlog.ini"' $0
    ${If} $0 != 0
      ${If} $8 == "1"
        DetailPrint "Credential migration failed; restoring the complete previous WatchLog installation..."
        ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage rollback -InstallDir "$INSTDIR"' $9
        ${If} $9 == 0
          MessageBox MB_ICONSTOP|MB_OK "WatchLog could not migrate the recorder credential. The previous working version was restored and its Agent restart was verified."
        ${Else}
          MessageBox MB_ICONSTOP|MB_OK "WatchLog could not migrate the recorder credential. The previous files were restored, but the old Agent restart could not be proven. Restart Windows once and contact WatchLog support with C:\ProgramData\WatchLog\upgrade.log."
        ${EndIf}
      ${Else}
        MessageBox MB_ICONSTOP|MB_OK "WatchLog could not migrate the recorder credential. Setup stopped without claiming a completed installation."
      ${EndIf}
      Abort "WatchLog credential migration failed"
    ${EndIf}
    ; A reinstall can keep enrollment state while the credential file was
    ; removed (uninstall deletes it), and pre-env-store installs have no env
    ; file to migrate. If there is still no credential, open setup to re-enter
    ; it rather than dead-ending on the check below.
    ${IfNot} ${FileExists} "${DATAROOT}\Secrets\nvr_credential.dpapi"
      DetailPrint "No recorder credential found; opening WatchLog Setup to repair..."
      ExecWait '"$INSTDIR\watchlog-setup-ui.exe" --installer-child --config "$INSTDIR\watchlog.ini"' $0
      DetailPrint "WatchLog setup exited with code $0"
      ${If} $0 != 0
        ${If} $8 == "1"
          DetailPrint "Recorder credential repair did not finish; restoring the previous WatchLog installation..."
          ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage rollback -InstallDir "$INSTDIR"' $9
          ${If} $9 == 0
            MessageBox MB_ICONSTOP|MB_OK "WatchLog setup did not finish. The previous working version was restored and its Agent restart was verified."
          ${Else}
            MessageBox MB_ICONSTOP|MB_OK "WatchLog setup did not finish. The previous files were restored, but the old Agent restart could not be proven. Restart Windows once and contact WatchLog support with C:\ProgramData\WatchLog\upgrade.log."
          ${EndIf}
        ${Else}
          MessageBox MB_ICONSTOP|MB_OK "WatchLog setup did not finish. Setup stopped without claiming a completed installation."
        ${EndIf}
        Abort "WatchLog setup did not complete"
      ${EndIf}
    ${EndIf}
  ${Else}
    DetailPrint "Opening WatchLog Setup..."
    ExecWait '"$INSTDIR\watchlog-setup-ui.exe" --installer-child --config "$INSTDIR\watchlog.ini"' $0
    DetailPrint "WatchLog setup exited with code $0"
    ${If} $0 != 0
      MessageBox MB_ICONSTOP|MB_OK "WatchLog setup did not finish. If the setup window showed that WatchLog is running in the background, this site IS connected and reporting - leave it alone and contact support. Otherwise run the installer again when the recorder, site code and network are ready."
      Abort "WatchLog setup did not complete"
    ${EndIf}
  ${EndIf}

  ; The recorder credential must exist before a background task can start.
  ${IfNot} ${FileExists} "${DATAROOT}\Secrets\nvr_credential.dpapi"
    ${If} $8 == "1"
      DetailPrint "Recorder credential is missing after upgrade; restoring the previous WatchLog installation..."
      ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage rollback -InstallDir "$INSTDIR"' $9
      ${If} $9 == 0
        MessageBox MB_ICONSTOP|MB_OK "WatchLog could not find the recorder credential after the attempted upgrade. The previous working version was restored and its Agent restart was verified."
      ${Else}
        MessageBox MB_ICONSTOP|MB_OK "WatchLog could not find the recorder credential after the attempted upgrade. Previous files were restored, but the old Agent restart could not be proven. Restart Windows once and contact WatchLog support with C:\ProgramData\WatchLog\upgrade.log."
      ${EndIf}
    ${Else}
      MessageBox MB_ICONSTOP|MB_OK "WatchLog could not find the recorder credential. Setup stopped without registering background monitoring."
    ${EndIf}
    Abort "Recorder credential missing"
  ${EndIf}

  ; Register/update background startup only after customer setup (fresh) or
  ; credential migration (upgrade) has completed successfully.
  DetailPrint "Setting WatchLog to run securely in the background..."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\register-service.ps1" -InstallDir "$INSTDIR"' $1
  DetailPrint "Background startup registration exited with code $1"
  ${If} $1 != 0
    ${If} $8 == "1"
      DetailPrint "Registration failed; rolling back to the previous working WatchLog..."
      ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage rollback -InstallDir "$INSTDIR"' $9
      ${If} $9 == 0
        MessageBox MB_ICONSTOP|MB_OK "The new WatchLog background startup could not be proven. The previous working version was restored and its Agent restart was verified."
      ${Else}
        MessageBox MB_ICONSTOP|MB_OK "The new WatchLog background startup could not be proven. Previous files were restored, but the old Agent restart could not be proven. Restart Windows once and contact WatchLog support with C:\ProgramData\WatchLog\upgrade.log."
      ${EndIf}
    ${Else}
      ExecWait '"$SYSDIR\schtasks.exe" /Delete /TN "${TASKNAME}" /F' $9
      MessageBox MB_ICONSTOP|MB_OK "WatchLog connected the site, but automatic background startup could not be proven. Setup stopped so this is not mistaken for a complete installation."
    ${EndIf}
    Abort "WatchLog background startup registration failed"
  ${EndIf}

  ; UPGRADE COMMIT: the task is registered + started; prove the EXACT new agent binary is actually
  ; RUNNING as a single instance and stays alive. If not, roll back to the previous working agent
  ; and abort. Only after this can the upgrade be considered real.
  ${If} $6 == "1"
    DetailPrint "Verifying the upgraded WatchLog agent is running..."
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage commit -InstallDir "$INSTDIR" -ExpectedVersion "${APPVERSION}"' $9
    ${If} $9 != 0
      ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$PLUGINSDIR\wl-upgrade.ps1" -Stage rollback -InstallDir "$INSTDIR"' $9
      ${If} $9 == 0
        MessageBox MB_ICONSTOP|MB_OK "WatchLog installed the update but the new Agent did not stay healthy. The previous working version was restored and its Agent restart was verified. No new version was kept."
        Abort "Upgrade commit failed; rollback proven"
      ${Else}
        MessageBox MB_ICONSTOP|MB_OK "The new WatchLog Agent did not stay healthy. The previous files were restored, but WatchLog could not prove the old Agent restarted. Do not uninstall anything. Restart Windows once and contact WatchLog support with C:\ProgramData\WatchLog\upgrade.log."
        Abort "Upgrade commit failed; rollback restart not proven"
      ${EndIf}
    ${EndIf}
  ${EndIf}

  CreateDirectory "${STARTMENU}"
  CreateShortcut "${STARTMENU}\WatchLog Setup.lnk" "$INSTDIR\watchlog-setup-ui.exe" '--config "$INSTDIR\watchlog.ini"' "$INSTDIR\setup.ico"
  CreateShortcut "${STARTMENU}\WatchLog Site Status.lnk" "$INSTDIR\watchlog-setup-ui.exe" '--status --config "$INSTDIR\watchlog.ini"' "$INSTDIR\setup.ico"
  CreateShortcut "${STARTMENU}\WatchLog Manage Recorders.lnk" "$INSTDIR\watchlog-setup-ui.exe" '--manage-recorders --config "$INSTDIR\watchlog.ini"' "$INSTDIR\setup.ico"

  WriteRegStr HKLM "${ARPKEY}" "DisplayName" "WatchLog"
  WriteRegStr HKLM "${ARPKEY}" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKLM "${ARPKEY}" "Publisher" "${PUBLISHER}"
  !ifdef PUBLISHER_URL
    WriteRegStr HKLM "${ARPKEY}" "URLInfoAbout" "${PUBLISHER_URL}"
  !endif
  WriteRegStr HKLM "${ARPKEY}" "DisplayIcon" "$INSTDIR\setup.ico"
  WriteRegStr HKLM "${ARPKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${ARPKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD HKLM "${ARPKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${ARPKEY}" "NoRepair" 1
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateShortcut "${STARTMENU}\Uninstall WatchLog.lnk" "$INSTDIR\uninstall.exe" "" "$INSTDIR\setup.ico"
SectionEnd

Section "Uninstall"
  ; schtasks /End kills the run-agent.ps1 LAUNCHER; the watchlog-agent.exe grandchild
  ; survives it. Deleting a locked exe then silently fails and leaves a ghost agent
  ; running against a site that has been uninstalled. wl-upgrade.ps1 -Stage preflight is
  ; the already-proven primitive that stops the task AND the process and verifies the
  ; binary is unlocked, so use it before touching any file.
  IfFileExists "$INSTDIR\wl-upgrade.ps1" 0 +2
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\wl-upgrade.ps1" -Stage preflight -InstallDir "$INSTDIR"' $9
  ExecWait '"$SYSDIR\schtasks.exe" /End /TN "${TASKNAME}"'
  ExecWait '"$SYSDIR\schtasks.exe" /Delete /TN "${TASKNAME}" /F'
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
  ; failed and the folder (plus a stale rollback backup) survived forever.
  Delete "$INSTDIR\wl-upgrade.ps1"
  Delete "$INSTDIR\watchlog-agent.exe.wlbak"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${ARPKEY}"

  ; Multi-recorder state follows the same rule as the legacy credential. recorders.json
  ; binds local recorder ids to this site's cloud recorder identities and names the
  ; per-recorder credentials under Secrets\recorders. Remove it BEFORE those credentials:
  ; a registry left without them made every later reinstall fail until someone deleted
  ; it by hand.
  Delete "${DATAROOT}\recorders.json"
  Delete "${DATAROOT}\recorders.json.tmp"
  RMDir /r "${DATAROOT}\Secrets\recorders"

  ; Remove the encrypted credential + agent key (the whole Secrets directory)
  ; and any legacy plaintext/blob remnants. Non-secret state and logs remain in
  ; ProgramData for support/reinstall continuity; a reinstall re-runs setup
  ; because is_enrolled requires a decryptable key, which is now gone.
  RMDir /r "${DATAROOT}\Secrets"

  ; IDENTITY-BOUND local state must go too. Keeping agent_state.json and the spool meant
  ; a PC uninstalled at customer A and reinstalled at customer B DRAINED A's queued events
  ; into B's site on first connect. Logs stay for support; identity and queued data do not.
  Delete "${DATAROOT}\agent_state.json"
  Delete "${DATAROOT}\spool.sqlite"
  Delete "${DATAROOT}\spool.sqlite-wal"
  Delete "${DATAROOT}\spool.sqlite-shm"
  Delete "${DATAROOT}\health.sqlite"
  Delete "${DATAROOT}\health.sqlite-wal"
  Delete "${DATAROOT}\health.sqlite-shm"
  Delete "${DATAROOT}\last_live.json"
  Delete "${DATAROOT}\watchlog.env"
  Delete "${DATAROOT}\nvr_password.dpapi"
  ; Secondary recorders keep their own spool, health ledger and last-live marker here.
  RMDir /r "${DATAROOT}\recorders"
SectionEnd
