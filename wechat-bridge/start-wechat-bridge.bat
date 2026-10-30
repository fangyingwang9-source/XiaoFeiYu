@echo off
title WeChat Bridge (xiao fei yu)
chcp 65001 >nul
rem ---- hardcoded workdir: MUST equal the cwd recorded in the DSH session ----
cd /d "E:\useer\deepseek-harness\default-workspace"
if errorlevel 1 (
  echo [ERROR] cannot cd to workspace root
  pause
  exit /b 1
)
set "PY=C:\Users\one\.dsh\wxauto\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] python not found: %PY%
  pause
  exit /b 1
)
echo ============================================
echo  WeChat bridge starting, keep this window open
echo  Ctrl+C to stop. Log also in wechat_bridge.log
echo ============================================

rem ---- switches ----
rem 1 = send stickers, 0 = text only
set "STICKER_ENABLED=1"
rem 1 = also answer friends in the group (default: only owner / @mention / questions)
rem set "GROUP_REPLY_ALL=1"

"%PY%" "%~dp0wechat_bridge.py"
echo.
echo [bridge exited, code %ERRORLEVEL%]
pause
