@echo off
rem Same as restart-bridge.bat but expects an elevated console (kills the bridge process).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-bridge.ps1" -Elevated
exit /b 0
