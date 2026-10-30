# on-wake.ps1 - 睡眠/休眠恢复后的自愈 + 30 分钟无人消息自动睡回去
# 由计划任务 DSH-Power-Wake（09:00 / 20:30，WakeToRun）或自测任务调用。
[CmdletBinding()]
param(
    [string]$Slot = 'manual',
    [switch]$Notify,
    [switch]$Force,
    [string]$Greeting = '',
    [int]$FakeHour = -1
)
$ErrorActionPreference = 'Continue'
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridge = Split-Path -Parent $here
$log    = Join-Path $here 'on-wake.log'
$cfg    = $null
try { $cfg = Get-Content -LiteralPath (Join-Path $here 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
if (-not $cfg) { $cfg = [pscustomobject]@{ ssid='Xiaomi_9FE2'; idle_minutes=30; respect_input=$true; input_grace_minutes=10; max_defer_minutes=120; notify_on_wake=$true } }

function Log([string]$m) {
    Add-Content -LiteralPath $log -Value ('{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Slot, $m) -Encoding UTF8
}

function Test-Tcp([string]$H, [int]$Port, [int]$TimeoutMs = 1500) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($H, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { $c.Close(); return $false }
        $c.EndConnect($iar); $ok = $c.Connected; $c.Close(); return $ok
    } catch { return $false }
}

Log ("task fired pid=" + $PID)

# ---------- 1. 判断是不是刚被唤醒 ----------
$fresh = $false; $evtAge = -1
try {
    $e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Power-Troubleshooter'; Id=1} -MaxEvents 1 -ErrorAction Stop
    $evtAge = [int]((Get-Date) - $e.TimeCreated).TotalSeconds
    if ($evtAge -ge 0 -and $evtAge -le 300) { $fresh = $true }
} catch {
    Log ("读取唤醒事件失败: " + $_.Exception.Message)
}
Log ("上次恢复事件 " + $evtAge + "s 前 fresh=" + $fresh)
if (-not $fresh -and -not $Force) {
    Log '不是刚唤醒（机器本来就醒着）-> 不做恢复动作，直接退出'
    exit 0
}
if ($Force) { Log 'Force 模式：跳过「刚唤醒」检查（仅手动测试用）' }
Set-Content -LiteralPath (Join-Path $here 'last_wake.txt') -Value (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') -Encoding UTF8

# ---------- 2. 等网络 ----------
$net = $false
for ($i = 0; $i -lt 40; $i++) {
    if (Test-Tcp '223.5.5.5' 53 1500) { $net = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $net) {
    Log ("唤醒后 " + ($i*3) + "s 仍无网络，尝试重连 WiFi " + $cfg.ssid)
    try { netsh wlan connect name="$($cfg.ssid)" | Out-Null } catch { Log ("netsh wlan connect 失败: " + $_.Exception.Message) }
    for ($j = 0; $j -lt 20; $j++) { if (Test-Tcp '223.5.5.5' 53 1500) { $net = $true; break }; Start-Sleep -Seconds 3 }
}
Log ("网络可用: " + $net)

# ---------- 3. DSH 桌面端（8712） ----------
$dshOk = $false
for ($i = 0; $i -lt 10; $i++) { if (Test-Tcp '127.0.0.1' 8712 800) { $dshOk = $true; break }; Start-Sleep -Seconds 2 }
if (-not $dshOk) {
    Log 'DSH 8712 未监听 -> 尝试启动桌面端'
    try { Start-Process explorer.exe -ArgumentList '"E:\A_deepseek\DeepSeek Harness.exe"' } catch { Log ("启动 DSH 失败: " + $_.Exception.Message) }
    for ($i = 0; $i -lt 30; $i++) { if (Test-Tcp '127.0.0.1' 8712 800) { $dshOk = $true; break }; Start-Sleep -Seconds 2 }
}
Log ("DSH 8712 可连接: " + $dshOk)

# ---------- 4. 微信主进程 ----------
if (-not (Get-Process -Name Weixin -ErrorAction SilentlyContinue)) {
    Log '微信未运行 -> 尝试启动'
    try { Start-Process explorer.exe -ArgumentList '"D:\Program Files\Tencent\Weixin\Weixin.exe"' } catch { Log ("启动微信失败: " + $_.Exception.Message) }
    for ($i = 0; $i -lt 20; $i++) { if (Get-Process -Name Weixin -ErrorAction SilentlyContinue) { break }; Start-Sleep -Seconds 2 }
}
Log ("微信进程数: " + ((Get-Process -Name Weixin -ErrorAction SilentlyContinue | Measure-Object).Count))

# ---------- 5. 微信桥 ----------
$pidFile = Join-Path $bridge 'bridge.pid'
$alive = $false; $bp = ''
if (Test-Path -LiteralPath $pidFile) {
    $bp = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($bp -match '^\d+$') { $alive = [bool](Get-Process -Id ([int]$bp) -ErrorAction SilentlyContinue) }
}
if (-not $alive) {
    Log ("桥进程不在了（bridge.pid=" + $bp + "）-> 用 wscript 重新拉起")
    try {
        Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + (Join-Path $bridge 'start-bridge-hidden.vbs') + '"') -WorkingDirectory $bridge
    } catch { Log ("拉起桥失败: " + $_.Exception.Message) }
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Path -LiteralPath $pidFile) {
            $bp = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue).Trim()
            if ($bp -match '^\d+$' -and (Get-Process -Id ([int]$bp) -ErrorAction SilentlyContinue)) { $alive = $true; break }
        }
    }
}
Log ("桥存活: " + $alive + " pid=" + $bp)

# ---------- 6. 通知主人（桥下一轮把 power/notify.txt 发出去） ----------
if ($Notify -and $cfg.notify_on_wake) {
    try {
        $text = $Greeting
        if (-not $text) {
            $h = (Get-Date).Hour
            if ($FakeHour -ge 0) { $h = $FakeHour }
            if ($h -ge 5 -and $h -lt 12) { $text = [string]$cfg.greeting_morning }
            elseif ($h -ge 18) { $text = [string]$cfg.greeting_evening }
            if (-not $text) { $text = "醒了（" + (Get-Date -Format 'HH:mm') + "）" }
        }
        Set-Content -LiteralPath (Join-Path $here 'notify.txt') -Value $text -Encoding UTF8
        Log ("已写 notify.txt: " + $text)
    } catch { Log ("写 notify.txt 失败: " + $_.Exception.Message) }
}

# ---------- 7. 启动 30 分钟无人消息看门狗 ----------
try {
    $wakeTs = [DateTimeOffset]::Now.ToUnixTimeSeconds()
    $a = @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',(Join-Path $here 'idle-watcher.ps1'),
           '-WakeEpoch', $wakeTs,
           '-Minutes', [string]$cfg.idle_minutes,
           '-RespectInput', [string]$cfg.respect_input,
           '-InputGraceMinutes', [string]$cfg.input_grace_minutes,
           '-MaxDeferMinutes', [string]$cfg.max_defer_minutes)
    Start-Process -FilePath 'powershell.exe' -ArgumentList $a -WindowStyle Hidden
    Log ("已启动 idle-watcher（" + $cfg.idle_minutes + " 分钟无消息则睡回去）")
} catch { Log ("启动 idle-watcher 失败: " + $_.Exception.Message) }

Log 'on-wake 完成'
exit 0
