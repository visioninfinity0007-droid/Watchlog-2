@echo off
REM Double-click this to install WatchLog. It will ask for Administrator
REM permission (needed to install and to start automatically at boot),
REM then run the setup.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','""%~dp0Install-WatchLog.ps1""'"
