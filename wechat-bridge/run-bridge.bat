@echo off
cd /d E:\useer\deepseek-harness\default-workspace
set STICKER_ENABLED=1
"C:\Users\one\.dsh\wxauto\venv\Scripts\python.exe" "E:\useer\deepseek-harness\default-workspace\wechat-bridge\wechat_bridge.py" >> "E:\useer\deepseek-harness\default-workspace\wechat-bridge\console.log" 2>&1
