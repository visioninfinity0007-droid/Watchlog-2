; WatchLog site agent — Inno Setup installer.
;
; Produces a single branded Setup.exe: a wizard, a Start-Menu entry, an
; entry in Add/Remove Programs, and an uninstaller — the "installed
; software" experience.
;
; Build it with Inno Setup 6 (free: https://jrsoftware.org/isdl.php):
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" watchlog.iss
; or run tools\make_installer.ps1, which calls ISCC if it is installed.
;
; Expects these beside this .iss (the packager assembles them):
;     watchlog-agent.exe  run-agent.cmd  register-service.ps1
;     watchlog.defaults.ini  "READ ME FIRST.txt"  setup.ico
;     yolov8n.onnx   (optional — the false-alarm model, if shipped)
;
; UNSIGNED until a code-signing certificate is bought; Windows SmartScreen
; will warn. SignTool config goes here once the cert exists.

#define AppName "WatchLog"
#define AppVer "0.2.0"
#define Publisher "Vision Infinity"

[Setup]
AppId={{7E1C9A54-0B2E-4C6A-9E77-WATCHLOG0001}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher={#Publisher}
AppPublisherURL=https://watchlogsite.161.97.175.15.sslip.io
DefaultDirName={autopf}\WatchLog
DefaultGroupName=WatchLog
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\watchlog-agent.exe
UninstallDisplayName=WatchLog Agent
SetupIconFile=setup.ico
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=WatchLog-Setup
Compression=lzma2
SolidCompression=yes
DisableWelcomePage=no
LicenseFile=

[Messages]
WelcomeLabel2=This will install the WatchLog agent on this PC.%n%nWatchLog watches the CCTV recorder you already have and reports what it saw. It connects OUTWARD only — nothing on your network is exposed to the internet.%n%nYou will need the recorder's admin password and your one-time setup code.

[Files]
Source: "watchlog-agent.exe";     DestDir: "{app}"; Flags: ignoreversion
Source: "run-agent.cmd";          DestDir: "{app}"; Flags: ignoreversion
Source: "register-service.ps1";   DestDir: "{app}"; Flags: ignoreversion
Source: "watchlog.defaults.ini";  DestDir: "{app}"; DestName: "watchlog.ini"; Flags: onlyifdoesntexist
Source: "READ ME FIRST.txt";      DestDir: "{app}"; Flags: isreadme
Source: "yolov8n.onnx";           DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\WatchLog log";        Filename: "{commonappdata}\WatchLog\agent.log"
Name: "{group}\Re-run WatchLog setup"; Filename: "{app}\watchlog-agent.exe"; Parameters: "--setup"; WorkingDir: "{app}"
Name: "{group}\Uninstall WatchLog";  Filename: "{uninstallexe}"

[Run]
; 1) Interactive setup — finds the recorder, asks for its login + the code,
;    tests, links the site. This is the one step the installer person sees.
Filename: "{app}\watchlog-agent.exe"; Parameters: "--setup"; WorkingDir: "{app}"; Flags: waituntilterminated; StatusMsg: "Finding your recorder and linking this site..."
; 2) Register the background service + harden power settings.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\register-service.ps1"" -InstallDir ""{app}"""; Flags: runhidden waituntilterminated; StatusMsg: "Setting WatchLog to run in the background and start on boot..."

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/End /TN ""WatchLog Agent"""; Flags: runhidden; RunOnceId: "StopTask"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""WatchLog Agent"" /F"; Flags: runhidden; RunOnceId: "DelTask"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

; NOTE: {commonappdata}\WatchLog (logs, local event spool, config) is left
; in place on uninstall so a reinstall keeps history. Delete it by hand to
; wipe completely.
