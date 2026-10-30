' Launch WeChat bridge without a console window (used by Task Scheduler / watchdog).
' stdout+stderr go to console.log so crashes are visible. ASCII only: keep this file plain.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "E:\useer\deepseek-harness\default-workspace"
sh.Environment("PROCESS")("STICKER_ENABLED") = "1"
cmd = "cmd /c """"C:\Users\one\.dsh\wxauto\venv\Scripts\python.exe"" ""E:\useer\deepseek-harness\default-workspace\wechat-bridge\wechat_bridge.py"" >> ""E:\useer\deepseek-harness\default-workspace\wechat-bridge\console.log"" 2>&1"""
sh.Run cmd, 0, False
