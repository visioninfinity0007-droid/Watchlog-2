; WatchLog Existing-Site Repair/Upgrade - NOT a first-time installer.
; Carries the Site Agent/runtime payload plus the Setup UI that provides Manage Recorders
; and Site Status. It never runs recorder discovery or the first-run setup wizard.

Unicode true

!define APPNAME "WatchLog Repair/Upgrade"
!ifndef APPVERSION
  !define APPVERSION "5.1.0"
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

  ; The proven payload includes the Setup UI, so an upgraded site can add or repair
  ; recorders without reinstalling. Written only after success: a rolled-back site keeps
  ; its previous Setup UI and Start Menu.
  CreateDirectory "${STARTMENU}"
  CreateShortcut "${STARTMENU}\WatchLog Site Status.lnk" "$INSTDIR\watchlog-setup-ui.exe" '--status --config "$INSTDIR\watchlog.ini"' "$INSTDIR\setup.ico"
  CreateShortcut "${STARTMENU}\WatchLog Manage Recorders.lnk" "$INSTDIR\watchlog-setup-ui.exe" '--manage-recorders --config "$INSTDIR\watchlog.ini"' "$INSTDIR\setup.ico"

  ; A multi-recorder site commits when the original recorder and every recorder that
  ; answered before the update are back. Say so plainly if others are still unreachable.
  ReadINIStr $7 "${RESULTFILE}" "recorders" "not_live_after"
  ${If} $7 != ""
  ${AndIf} $7 != "0"
    MessageBox MB_ICONINFORMATION|MB_OK "WatchLog was updated.$\r$\n$\r$\n$7 recorder(s) could not be reached before the update and still cannot be reached. Every recorder that was reachable before the update is back online.$\r$\n$\r$\nCheck them in WatchLog Site Status or WatchLog Manage Recorders." /SD IDOK
  ${EndIf}

  RMDir /r "${CANDIDATE}"
SectionEnd
