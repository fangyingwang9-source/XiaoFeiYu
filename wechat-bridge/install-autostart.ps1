# 一次性安装：登录自动起桥 + 常驻隐藏看护（pythonw）。卸载见 uninstall-autostart.ps1
$ErrorActionPreference = "Stop"
$dir = "E:\useer\deepseek-harness\default-workspace\wechat-bridge"
$pyw = "C:\Users\one\.dsh\wxauto\venv\Scripts\pythonw.exe"
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -Hidden
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
$actBridge = New-ScheduledTaskAction -Execute "wscript.exe" -Argument ('"' + $dir + '\start-bridge-hidden.vbs"') -WorkingDirectory $dir
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $me
$trigLogon.Delay = "PT30S"
Register-ScheduledTask -TaskName "WeChatBridge" -Action $actBridge -Trigger $trigLogon -Settings $settings -Principal $principal -Force | Out-Null
# 看护改成常驻一个进程（pythonw，无窗口），不再每 2 分钟起一个 powershell 窗口
$actWatch = New-ScheduledTaskAction -Execute $pyw -Argument ('"' + $dir + '\watchdog.py"') -WorkingDirectory $dir
$trigWatch = New-ScheduledTaskTrigger -AtLogOn -User $me
$trigWatch.Delay = "PT1M"
Register-ScheduledTask -TaskName "WeChatBridgeWatchdog" -Action $actWatch -Trigger $trigWatch -Settings $settings -Principal $principal -Force | Out-Null
"已安装：WeChatBridge（登录后 30s 起，隐藏 VBS）、WeChatBridgeWatchdog（登录后 1 分钟起，常驻 pythonw 看护，每 60s 查进程、每 12 分钟轻量巡检）"
