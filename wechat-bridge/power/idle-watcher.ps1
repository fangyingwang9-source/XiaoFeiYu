# idle-watcher.ps1 - 唤醒后 N 分钟内没收到任何微信消息 -> 自动睡回去
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][long]$WakeEpoch,
    [int]$Minutes = 30,
    [string]$RespectInput = 'true',
    [int]$InputGraceMinutes = 10,
    [int]$MaxDeferMinutes = 120
)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $here 'idle-watcher.log'
function Log([string]$m) {
    Add-Content -LiteralPath $log -Value ('{0} [idle] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8
}

# 只允许一个看门狗
$pidFile = Join-Path $here 'idle-watcher.pid'
if (Test-Path -LiteralPath $pidFile) {
    $old = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($old -match '^\d+$' -and [int]$old -ne $PID) {
        Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue
        Log ("结束旧看门狗 pid=" + $old)
    }
}
Set-Content -LiteralPath $pidFile -Value $PID -Encoding UTF8

try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class LastInput {
    [StructLayout(LayoutKind.Sequential)] public struct INFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref INFO plii);
    public static uint IdleMs() {
        INFO i = new INFO(); i.cbSize = (uint)Marshal.SizeOf(i);
        GetLastInputInfo(ref i);
        return (uint)Environment.TickCount - i.dwTime;
    }
}
'@ -ErrorAction Stop
    $hasInput = $true
} catch { $hasInput = $false }

$wake = [datetimeoffset]::FromUnixTimeSeconds($WakeEpoch).LocalDateTime
$wakeEpoch = [long]$WakeEpoch
$deadline = $wake.AddMinutes($Minutes)
$hardDeadline = $wake.AddMinutes($MaxDeferMinutes)
$lastMsgFile = Join-Path $here 'last_msg_ts.txt'
$power = Join-Path $here 'power.ps1'
Log ("启动 wake=" + $wake.ToString('yyyy-MM-dd HH:mm:ss') + " 截止=" + $deadline.ToString('HH:mm:ss') + " 无消息=" + $Minutes + " 分钟 键鼠保护=" + $RespectInput)

while ($true) {
    Start-Sleep -Seconds 20
    $last = 0
    try { $last = [long]((Get-Content -LiteralPath $lastMsgFile -Raw -ErrorAction Stop).Trim()) } catch { $last = 0 }
    if ($last -ge $wakeEpoch) {
        $seen = [datetimeoffset]::FromUnixTimeSeconds($last).LocalDateTime.ToString('HH:mm:ss')
        Log ("唤醒后收到微信消息（" + $seen + "）-> 取消自动睡眠")
        exit 0
    }
    $now = Get-Date
    if ($now -lt $deadline) { continue }

    # 到点了：如果键鼠还在动，推迟一小段，最多推迟到 hardDeadline
    if ($RespectInput -eq 'true' -and $hasInput -and ([LastInput]::IdleMs() -lt ($InputGraceMinutes * 60000))) {
        if ($now -lt $hardDeadline) {
            $deadline = $now.AddMinutes($InputGraceMinutes)
            Log ("有键鼠活动 idle=" + [int]([LastInput]::IdleMs()/1000) + "s -> 推迟到 " + $deadline.ToString('HH:mm:ss'))
            continue
        }
    }
    break
}

# 最后再确认一次
$last = 0
try { $last = [long]((Get-Content -LiteralPath $lastMsgFile -Raw -ErrorAction Stop).Trim()) } catch { $last = 0 }
if ($last -ge $wakeEpoch) { Log '最后确认时发现新消息 -> 不睡'; exit 0 }

Log ($Minutes.ToString() + " 分钟内没有任何微信消息 -> 执行睡眠")
try {
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$power,'-Action','sleep','-Reason','idle-no-message','-DelaySec','5') -WindowStyle Hidden
} catch { Log ("启动睡眠失败: " + $_.Exception.Message) }
exit 0
