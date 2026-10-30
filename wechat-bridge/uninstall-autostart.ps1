$ErrorActionPreference = "SilentlyContinue"
foreach ($n in @("WeChatBridge", "WeChatBridgeWatchdog")) { Unregister-ScheduledTask -TaskName $n -Confirm:$false; "已卸载计划任务：$n" }
