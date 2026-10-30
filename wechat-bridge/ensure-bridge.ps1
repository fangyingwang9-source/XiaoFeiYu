# 守护：桥没在跑、且微信开着，就无声拉起来。由计划任务 WeChatBridgeWatchdog 每 2 分钟调一次。
$ErrorActionPreference = "SilentlyContinue"
$dir = "E:\useer\deepseek-harness\default-workspace\wechat-bridge"
$pidFile = Join-Path $dir "bridge.pid"

if (Test-Path $pidFile) {
    $raw = (Get-Content $pidFile -TotalCount 1)
    if ($raw -match '^\s*(\d+)\s*$') {
        if (Get-Process -Id ([int]$Matches[1]) -ErrorAction SilentlyContinue) { exit 0 }
    }
}
# 微信没开就别空跑（登录后由计划任务/守护再拉）
if (-not (Get-Process -Name "WeChat", "Weixin" -ErrorAction SilentlyContinue)) { exit 0 }
Start-Process -FilePath "wscript.exe" -ArgumentList ('"' + $dir + '\start-bridge-hidden.vbs"') -WindowStyle Hidden
