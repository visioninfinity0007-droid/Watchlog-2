@echo off
REM WatchLog agent runner — launched by the "WatchLog Agent" scheduled task
REM at every startup, as SYSTEM. Keeps the agent running and captures its
REM output to a rotating-ish log. The agent is a long-running loop; the
REM outer restart loop here is only a safety net if it ever exits.

setlocal
set "WATCHLOG_STATE_DIR=%ProgramData%\WatchLog"
set "LOG=%ProgramData%\WatchLog\agent.log"
cd /d "%~dp0"

REM Keep the log from growing without bound: if over ~5 MB, roll it once.
for %%A in ("%LOG%") do if %%~zA GTR 5000000 move /y "%LOG%" "%LOG%.old" >nul 2>&1

:loop
echo. >> "%LOG%"
echo ==== agent starting %date% %time% ==== >> "%LOG%"
"%~dp0watchlog-agent.exe" >> "%LOG%" 2>&1
echo ==== agent exited, restarting in 15s %date% %time% ==== >> "%LOG%"
timeout /t 15 /nobreak >nul
goto loop
