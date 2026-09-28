; WatchLog Existing-Site Repair/Upgrade - NOT a first-time installer.
; Carries only the Site Agent/runtime payload. No Qt Setup UI, no discovery wizard.

Unicode true

!define APPNAME "WatchLog Repair/Upgrade"
!ifndef APPVERSION
  !define APPVERSION "5.0.24"
!endif
!define PUBLISHER "Vision Infinity"
!define TASKNAME "WatchLog Agent"
!define DATAROOT "$COMMONPROGRAMDATA\WatchLog"
!define ARPKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\WatchLog"
!define CANDIDATE "${DATAROOT}\repair-candidate"

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
!endif

!include "MUI2.nsh"
!include "LogicLib.nsh"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Repair or update this WatchLog site"
!define MUI_WELCOMEPAGE_TEXT "This tool is only for a PC where WatchLog is already connected.$\r$\n$\r$\nIt validates the new Agent against this site's existing encrypted credentials, CCTV recorder and WatchLog cloud identity before replacing the working version.$\r$\n$\r$\nIt does not run recorder discovery or ask for recorder credentials again."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "WatchLog update completed"
!define MUI_FINISHPAGE_TEXT "The new WatchLog Agent is online, the recorder is reachable, and online-update polling has been proven.$\r$\n$\r$\nFuture approved WatchLog updates can now be delivered remotely."
!insertmacro MUI_PAGE_FINISH
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
    Abort "Existing WatchLog config not found"
  ${EndIf}
  ${IfNot} ${FileExists} "${DATAROOT}\agent_state.json"
    MessageBox MB_ICONSTOP|MB_OK "This WatchLog site is not fully enrolled. Use WatchLog-Setup.exe to repair the site first."
    Abort "Existing WatchLog identity not found"
  ${EndIf}
  ${IfNot} ${FileExists} "${DATAROOT}\Secrets\agent_key.dpapi"
    MessageBox MB_ICONSTOP|MB_OK "The encrypted WatchLog site identity is incomplete. No files were changed. Use the full WatchLog installer."
    Abort "Agent key not found"
  ${EndIf}
  ${IfNot} ${FileExists} "${DATAROOT}\Secrets\nvr_credential.dpapi"
    MessageBox MB_ICONSTOP|MB_OK "This older installation has not yet migrated its recorder credential to the secure store. No files were changed. Use the full WatchLog installer once."
    Abort "Recorder credential not repair-upgrade ready"
  ${EndIf}

  DetailPrint "Staging WatchLog ${APPVERSION} without changing the installed version..."
  RMDir /r "${CANDIDATE}"
  CreateDirectory "${CANDIDATE}"
  SetOutPath "${CANDIDATE}"
  SetOverwrite on
  File "watchlog-agent.exe"
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
    ${If} $9 == 30
      MessageBox MB_ICONSTOP|MB_OK "This update did not pass the existing-site compatibility check. Your current WatchLog was not stopped or replaced. No new version was installed."
    ${ElseIf} $9 == 38
      MessageBox MB_ICONSTOP|MB_OK "The new WatchLog started but did not prove cloud, recorder and online-update health in time. The previous working WatchLog was restored and restarted."
    ${ElseIf} $9 >= 31
      MessageBox MB_ICONSTOP|MB_OK "WatchLog could not prove a safe automatic recovery. Do not uninstall anything. Contact WatchLog support and provide C:\ProgramData\WatchLog\repair-upgrade.log."
    ${Else}
      MessageBox MB_ICONSTOP|MB_OK "WatchLog could not safely perform this Repair/Upgrade. The installed WatchLog was not replaced. See C:\ProgramData\WatchLog\repair-upgrade.log."
    ${EndIf}
    Abort "WatchLog Repair/Upgrade failed safely"
  ${EndIf}

  WriteRegStr HKLM "${ARPKEY}" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKLM "${ARPKEY}" "InstallLocation" "$INSTDIR"
  RMDir /r "${CANDIDATE}"
SectionEnd
