@echo off
rem 手动重启入口：转给 pythonw 的 restart_bridge.py（无控制台、无 UAC）。
rem 桥自重启已改为直接起 pythonw restart_bridge.py，不再经过本脚本。
set "PYW=C:\Users\one\.dsh\wxauto\venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
start "" /B "%PYW%" "%~dp0restart_bridge.py" %*
exit /b 0
