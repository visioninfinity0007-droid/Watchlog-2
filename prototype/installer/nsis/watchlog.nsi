; WatchLog Site Agent - authoritative production installer (NSIS).
;
; Produces WatchLog-Setup.exe with Add/Remove Programs registration,
; first-run recorder setup, background startup registration and uninstaller.
; Built only through tools/build_windows_release.ps1.

Unicode true

!define APPNAME "WatchLog"
!define APPVERSION "0.3.0"
!define PUBLISHER "Vision Infinity"
!define TASKNAME "WatchLog Agent"
!define ARPKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\WatchLog"

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
!define MUI_WELCOMEPAGE_TITLE "Install WatchLog Site Agent"
!define MUI_WELCOMEPAGE_TEXT "WatchLog adds intelligence to the CCTV recorder you already use.$\r$\n$\r$\nThis PC must stay at the site on the same network as the recorder. Setup will find the recorder, verify its own login locally and link this installation with your one-time WatchLog enrollment code.$\r$\n$\r$\nRecorder credentials stay on this PC. WatchLog connects outward only; no port forwarding or inbound access is required."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "WatchLog is ready on this PC"
!define MUI_FINISHPAGE_TEXT "The WatchLog Site Agent is installed and registered to start automatically with Windows.$\r$\n$\r$\nReturn to the WatchLog portal to confirm the site reaches Ready, then use Analytics Studio if you want to assign camera purposes, monitoring lines, zones or schedules.$\r$\n$\r$\nLocal diagnostics: C:\ProgramData\WatchLog\agent.log"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

VIProductVersion "0.3.0.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileVersion" "${APPVERSION}"
VIAddVersionKey "ProductVersion" "${APPVERSION}"
VIAddVersionKey "FileDescription" "WatchLog Site Agent installer"
VIAddVersionKey "LegalCopyright" "${PUBLISHER}"

Section "Install"
  ; An upgrade can have the existing long-running task holding the executable.
  ; Stop it before replacing files, but do NOT delete the task yet. If the new
  ; interactive setup fails we can resume the already-registered installation.
  StrCpy $8 "0"
  nsExec::ExecToStack '"$SYSDIR\schtasks.exe" /Query /TN "${TASKNAME}"'
  Pop $9
  Pop $7
  ${If} $9 == 0
    StrCpy $8 "1"
    DetailPrint "Stopping the existing WatchLog background task for upgrade..."
    ExecWait '"$SYSDIR\schtasks.exe" /End /TN "${TASKNAME}"' $9
  ${EndIf}

  SetOutPath "$INSTDIR"
  File "watchlog-agent.exe"
  File "run-agent.cmd"
  File "register-service.ps1"
  File "READ ME FIRST.txt"

  ; Preserve a proven recorder configuration on upgrades. The setup wizard
  ; writes a replacement only after local recorder checks have succeeded.
  IfFileExists "$INSTDIR\watchlog.ini" +2 0
    File "/oname=watchlog.ini" "watchlog.defaults.ini"

  ; The AI executable normally contains the model. A loose model is supported
  ; for diagnostic/legacy builds but is genuinely optional at compile time.
  File /nonfatal "yolov8n.onnx"

  ; Explicit --setup is a finite validation command in the packaged release:
  ; it proves the recorder locally, validates WatchLog enrollment, syncs camera
  ; metadata, then exits. Any cancellation/failure is a non-zero process code.
  DetailPrint "Opening WatchLog recorder setup..."
  ExecWait '"$INSTDIR\watchlog-agent.exe" --setup' $0
  DetailPrint "Recorder/setup validation exited with code $0"
  ${If} $0 != 0
    ${If} $8 == "1"
      DetailPrint "Setup did not complete; attempting to resume the previous background task..."
      ExecWait '"$SYSDIR\schtasks.exe" /Run /TN "${TASKNAME}"' $9
    ${EndIf}
    MessageBox MB_ICONSTOP|MB_OK "WatchLog setup did not complete, so this installer will not mark the new installation as ready. The background Site Agent was not newly registered. Local recorder settings may remain on this PC so setup can be retried safely. Correct the recorder, enrollment, or network issue, then run the installer again."
    Abort "WatchLog recorder/setup validation did not complete"
  ${EndIf}

  ; Register background startup only after interactive recorder + enrollment
  ; validation has completed successfully. register-service.ps1 updates the task
  ; in place with -Force, then proves it reached Running state.
  DetailPrint "Registering WatchLog to run in the background and start with Windows..."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\register-service.ps1" -InstallDir "$INSTDIR"' $1
  DetailPrint "Background startup registration exited with code $1"
  ${If} $1 != 0
    ${If} $8 == "1"
      DetailPrint "Registration failed; attempting to resume the existing WatchLog task..."
      ExecWait '"$SYSDIR\schtasks.exe" /Run /TN "${TASKNAME}"' $9
    ${Else}
      ; A fresh install must not leave a partially-created startup task behind.
      ExecWait '"$SYSDIR\schtasks.exe" /Delete /TN "${TASKNAME}" /F' $9
    ${EndIf}
    MessageBox MB_ICONSTOP|MB_OK "The recorder and WatchLog enrollment were validated, but automatic background startup could not be proven. Setup will stop so this is not mistaken for a complete installation. Correct the Windows Task Scheduler issue, then run the installer again."
    Abort "WatchLog background startup registration failed"
  ${EndIf}

  ; Only a fully validated installation is registered with Windows. On a fresh
  ; failure there is therefore no misleading Add/Remove Programs entry; on an
  ; upgrade the previous registration stays intact until success reaches here.
  WriteRegStr HKLM "${ARPKEY}" "DisplayName" "WatchLog Site Agent"
  WriteRegStr HKLM "${ARPKEY}" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKLM "${ARPKEY}" "Publisher" "${PUBLISHER}"
  !ifdef PUBLISHER_URL
    WriteRegStr HKLM "${ARPKEY}" "URLInfoAbout" "${PUBLISHER_URL}"
  !endif
  WriteRegStr HKLM "${ARPKEY}" "DisplayIcon" "$INSTDIR\watchlog-agent.exe"
  WriteRegStr HKLM "${ARPKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${ARPKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD HKLM "${ARPKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${ARPKEY}" "NoRepair" 1
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
  ExecWait '"$SYSDIR\schtasks.exe" /End /TN "${TASKNAME}"'
  ExecWait '"$SYSDIR\schtasks.exe" /Delete /TN "${TASKNAME}" /F'
  Delete "$INSTDIR\watchlog-agent.exe"
  Delete "$INSTDIR\run-agent.cmd"
  Delete "$INSTDIR\register-service.ps1"
  Delete "$INSTDIR\READ ME FIRST.txt"
  Delete "$INSTDIR\watchlog.ini"
  Delete "$INSTDIR\yolov8n.onnx"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${ARPKEY}"
  ; C:\ProgramData\WatchLog is deliberately retained so reinstall keeps local
  ; logs and spool history. Delete that folder manually for a full local wipe.
SectionEnd
