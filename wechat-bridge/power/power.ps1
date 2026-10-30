# power.ps1 - 睡眠执行器（S3；hibernate 已关闭，SetSuspendState 必定走 S3）
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File power.ps1 -Action sleep -Reason wechat -DelaySec 3
[CmdletBinding()]
param(
    [ValidateSet('sleep','hibernate','status')]
    [string]$Action = 'status',
    [string]$Reason = 'manual',
    [int]$DelaySec = 0
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $here 'power.log'

function Log([string]$m) {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Reason, $m
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

Log ("action=" + $Action + " delay=" + $DelaySec + "s pid=" + $PID)
if ($DelaySec -gt 0) { Start-Sleep -Seconds $DelaySec }

if ($Action -eq 'status') {
    Log 'status queried'
    exit 0
}

# 记录本次睡眠开始时间，恢复后可据此判断睡了多久
$marker = Join-Path $here 'last_sleep.txt'
Set-Content -LiteralPath $marker -Value (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') -Encoding UTF8
Log ("recorded last_sleep; entering " + $Action)

try {
    Add-Type -AssemblyName System.Windows.Forms
    $mode = if ($Action -eq 'hibernate') { 'Hibernate' } else { 'Suspend' }
    # 第二个参数 force=true：等价于 rundll32 powrprof.dll,SetSuspendState 0,1,0
    [void][System.Windows.Forms.Application]::SetSuspendState($mode, $true, $false)
    Log ("SetSuspendState(" + $mode + ") returned")
} catch {
    Log ("SetSuspendState failed: " + $_.Exception.Message)
    exit 1
}
Log 'resumed'
exit 0
