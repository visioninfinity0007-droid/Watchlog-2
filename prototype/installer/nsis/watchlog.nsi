; WatchLog site agent - NSIS installer (the contractual installer technology).
;
; Produces a single branded WatchLog-Setup.exe: a wizard, an Add/Remove
; Programs entry, and an uninstaller. Built by tools/build_windows_release.ps1,
; which stages the payload beside this script and calls makensis.
;
; Payload expected in the makensis working directory (the build script stages it):
;   watchlog-agent.exe  run-agent.cmd  register-service.ps1
;   watchlog.defaults.ini  "READ ME FIRST.txt"  setup.ico
;   yolov8n.onnx  (optional - the AI build already bundles the model in the exe)
;
; UNSIGNED until a code-signing certificate exists; SmartScreen will warn.
; The build script adds a signtool step when a cert is provided.

Unicode true

!define APPNAME "WatchLog"
!define APPVERSION "0.2.0"
!define PUBLISHER "Vision Infinity"
!define TASKNAME "WatchLog Agent"
!ifndef PUBLISHER_URL
  !define PUBLISHER_URL "https://watchlog.example"
!endif
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
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Install ${APPNAME}"
!define MUI_WELCOMEPAGE_TEXT "This installs the WatchLog agent on this PC.$\r$\n$\r$\nWatchLog watches the CCTV recorder you already have and reports what it saw. It connects OUTWARD only - nothing on your network is exposed to the internet.$\r$\n$\r$\nYou will need the recorder's admin password and your one-time setup code."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TEXT "WatchLog is installed and running in the background. It will start automatically at boot.$\r$\n$\r$\nThe log is at C:\ProgramData\WatchLog\agent.log."
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

VIProductVersion "0.2.0.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileVersion" "${APPVERSION}"
VIAddVersionKey "FileDescription" "WatchLog site agent installer"
VIAddVersionKey "LegalCopyright" "${PUBLISHER}"

Section "Install"
  SetOutPath "$INSTDIR"

  File "watchlog-agent.exe"
  File "run-agent.cmd"
  File "register-service.ps1"
  File "READ ME FIRST.txt"

  ; Preserve an existing recorder config on reinstall; only lay down the
  ; default on a first install.
  IfFileExists "$INSTDIR\watchlog.ini" +2 0
    File "/oname=watchlog.ini" "watchlog.defaults.ini"

  ; The AI exe self-contains the model; ship a loose copy too if staged.
  IfFileExists "yolov8n.onnx" 0 +2
    File "yolov8n.onnx"

  ; Add/Remove Programs
  WriteRegStr HKLM "${ARPKEY}" "DisplayName" "WatchLog Agent"
  WriteRegStr HKLM "${ARPKEY}" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKLM "${ARPKEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKLM "${ARPKEY}" "URLInfoAbout" "${PUBLISHER_URL}"
  WriteRegStr HKLM "${ARPKEY}" "DisplayIcon" "$INSTDIR\watchlog-agent.exe"
  WriteRegStr HKLM "${ARPKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${ARPKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD HKLM "${ARPKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${ARPKEY}" "NoRepair" 1
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; 1) Interactive setup: find the recorder, ask for its login + the one-time
  ;    code, test, link the site. Console is visible so the person can type.
  DetailPrint "Finding your recorder and linking this site..."
  ExecWait '"$INSTDIR\watchlog-agent.exe" --setup' $0
  DetailPrint "Setup wizard exited with code $0"

  ; 2) Register the background SYSTEM task + harden power settings (no window).
  DetailPrint "Setting WatchLog to run in the background and start on boot..."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\register-service.ps1" -InstallDir "$INSTDIR"' $1
  DetailPrint "Service registration exited with code $1"
SectionEnd

Section "Uninstall"
  ExecWait 'schtasks.exe /End /TN "${TASKNAME}"'
  ExecWait 'schtasks.exe /Delete /TN "${TASKNAME}" /F'

  Delete "$INSTDIR\watchlog-agent.exe"
  Delete "$INSTDIR\run-agent.cmd"
  Delete "$INSTDIR\register-service.ps1"
  Delete "$INSTDIR\READ ME FIRST.txt"
  Delete "$INSTDIR\watchlog.ini"
  Delete "$INSTDIR\yolov8n.onnx"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  DeleteRegKey HKLM "${ARPKEY}"
  ; C:\ProgramData\WatchLog (logs, event spool, config) is deliberately left
  ; in place so a reinstall keeps history. Delete it by hand to wipe fully.
SectionEnd
