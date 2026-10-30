@echo off
rem Restart the WeChat bridge (waits for the pending reply, then kills + relaunches hidden).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-bridge.ps1"
if exist "%~dp0restart-need-admin.flag" (
  del "%~dp0restart-need-admin.flag" >nul 2>&1
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~dp0restart-bridge-admin.bat' -Verb RunAs"
)
exit /b 0
