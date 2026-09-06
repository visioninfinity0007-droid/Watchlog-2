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
  !define APPVERSION "0.3.4"
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
  ; Detect an already-enrolled installation before replacing any binaries.
  StrCpy $6 "0"
  IfFileExists "$INSTDIR\watchlog.ini" 0 +3
  IfFileExists "${DATAROOT}\agent_state.json" 0 +2
    StrCpy $6 "1"

  ; An upgrade may have the existing long-running task holding files. Stop it,
  ; but remember it so a failed upgrade can attempt to resume the old task.
  StrCpy $8 "0"
  nsExec::ExecToStack '"$SYSDIR\schtasks.exe" /Query /TN "${TASKNAME}"'
  Pop $9
  Pop $7
  ${If} $9 == 0
    StrCpy $8 "1"
    DetailPrint "Stopping the existing WatchLog background connection for upgrade..."
    ExecWait '"$SYSDIR\schtasks.exe" /End /TN "${TASKNAME}"' $9
  ${EndIf}

  SetOutPath "$INSTDIR"
  File "watchlog-agent.exe"
  File "watchlog-setup-ui.exe"
  File "run-agent.ps1"
  File "register-service.ps1"
  File "READ ME FIRST.txt"
  File "setup.ico"

  ; Keep existing site/enrollment configuration on upgrades. A fresh install
  ; receives public defaults only; the graphical setup writes recorder values.
  IfFileExists "$INSTDIR\watchlog.ini" +2 0
    File "/oname=watchlog.ini" "watchlog.defaults.ini"

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
        ExecWait '"$SYSDIR\schtasks.exe" /Run /TN "${TASKNAME}"' $9
      ${EndIf}
      MessageBox MB_ICONSTOP|MB_OK "WatchLog could not migrate the existing recorder credential. The upgrade was stopped so the site is not left in a misleading state."
      Abort "WatchLog credential migration failed"
    ${EndIf}
    ; A reinstall can keep enrollment state while the credential file was
    ; removed (uninstall deletes it), and pre-env-store installs have no env
    ; file to migrate. If there is still no credential, open setup to re-enter
    ; it rather than dead-ending on the check below.
    ${IfNot} ${FileExists} "${DATAROOT}\watchlog.env"
      DetailPrint "No recorder credential found; opening WatchLog Setup to repair..."
      ExecWait '"$INSTDIR\watchlog-setup-ui.exe" --config "$INSTDIR\watchlog.ini"' $0
      DetailPrint "WatchLog setup exited with code $0"
      ${If} $0 != 0
        MessageBox MB_ICONSTOP|MB_OK "WatchLog setup did not complete. The background connection was not started. Run the installer again when the recorder, site code and network are ready."
        Abort "WatchLog setup did not complete"
      ${EndIf}
    ${EndIf}
  ${Else}
    DetailPrint "Opening WatchLog Setup..."
    ExecWait '"$INSTDIR\watchlog-setup-ui.exe" --config "$INSTDIR\watchlog.ini"' $0
    DetailPrint "WatchLog setup exited with code $0"
    ${If} $0 != 0
      MessageBox MB_ICONSTOP|MB_OK "WatchLog setup did not complete. The background connection was not started. Run the installer again when the recorder, site code and network are ready."
      Abort "WatchLog setup did not complete"
    ${EndIf}
  ${EndIf}

  ; The recorder credential must exist before a background task can start.
  ${IfNot} ${FileExists} "${DATAROOT}\watchlog.env"
    MessageBox MB_ICONSTOP|MB_OK "WatchLog could not find the recorder credential after setup. Run WatchLog Setup again."
    Abort "Recorder credential missing"
  ${EndIf}

  ; Register/update background startup only after customer setup (fresh) or
  ; credential migration (upgrade) has completed successfully.
  DetailPrint "Setting WatchLog to run securely in the background..."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\register-service.ps1" -InstallDir "$INSTDIR"' $1
  DetailPrint "Background startup registration exited with code $1"
  ${If} $1 != 0
    ${If} $8 == "1"
      DetailPrint "Registration failed; attempting to resume the existing WatchLog task..."
      ExecWait '"$SYSDIR\schtasks.exe" /Run /TN "${TASKNAME}"' $9
    ${Else}
      ExecWait '"$SYSDIR\schtasks.exe" /Delete /TN "${TASKNAME}" /F' $9
    ${EndIf}
    MessageBox MB_ICONSTOP|MB_OK "WatchLog connected the site, but automatic background startup could not be proven. Setup stopped so this is not mistaken for a complete installation."
    Abort "WatchLog background startup registration failed"
  ${EndIf}

  CreateDirectory "${STARTMENU}"
  CreateShortcut "${STARTMENU}\WatchLog Setup.lnk" "$INSTDIR\watchlog-setup-ui.exe" '--config "$INSTDIR\watchlog.ini"' "$INSTDIR\setup.ico"

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
  ExecWait '"$SYSDIR\schtasks.exe" /End /TN "${TASKNAME}"'
  ExecWait '"$SYSDIR\schtasks.exe" /Delete /TN "${TASKNAME}" /F'
  Delete "${STARTMENU}\WatchLog Setup.lnk"
  Delete "${STARTMENU}\Uninstall WatchLog.lnk"
  RMDir "${STARTMENU}"
  Delete "$INSTDIR\watchlog-agent.exe"
  Delete "$INSTDIR\watchlog-setup-ui.exe"
  Delete "$INSTDIR\run-agent.ps1"
  Delete "$INSTDIR\run-agent.cmd"
  Delete "$INSTDIR\register-service.ps1"
  Delete "$INSTDIR\READ ME FIRST.txt"
  Delete "$INSTDIR\watchlog.ini"
  Delete "$INSTDIR\setup.ico"
  Delete "$INSTDIR\yolov8n.onnx"  ; legacy: remove any externally-shipped model from older installs
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${ARPKEY}"

  ; Remove the recorder credential on uninstall. Logs/spool/state remain in
  ; ProgramData intentionally for support/reinstall continuity; delete that
  ; folder manually only when a full local data wipe is required.
  Delete "${DATAROOT}\watchlog.env"
  Delete "${DATAROOT}\nvr_password.dpapi"
SectionEnd
