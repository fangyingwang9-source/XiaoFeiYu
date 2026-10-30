# install.ps1 - 管理员权限：开启 RTC 唤醒定时器 + 按 config.json 的 wake_slots 注册定时唤醒任务
#Requires -RunAsAdministrator
[CmdletBinding()]
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $here 'install.log'
function Log([string]$m) {
    Add-Content -LiteralPath $log -Value ('{0} [install] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8
    Write-Host $m
}
Log ("start pid=" + $PID + " uninstall=" + $Uninstall)

if ($Uninstall) {
    foreach ($n in 'DSH-Power-Wake','DSH-Power-SelfTest') {
        try { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction Stop; Log ("removed task " + $n) } catch { Log ("task " + $n + ": " + $_.Exception.Message) }
    }
    exit 0
}

# 1) 系统级：允许唤醒定时器（AC + DC 都开）
$out = & powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>&1
Log ("ac set: " + ($out -join ' | '))
$out = & powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>&1
Log ("dc set: " + ($out -join ' | '))
$out = & powercfg /setactive SCHEME_CURRENT 2>&1
Log ("setactive: " + ($out -join ' | '))
$out = & powercfg /h off 2>&1
Log ("hibernate off: " + ($out -join ' | '))

# 2) 计划任务
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$ps = Join-Path $here 'on-wake.ps1'
function NextOccurrence([string]$hhmm) {
    $parts = $hhmm -split ':'
    $t = (Get-Date).Date.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
    if ($t -le (Get-Date)) { return $t.AddDays(1) }
    return $t
}

# 唤醒时段来自统一配置（config.json 的 schedule.wake_slots），不再写死 09:00 / 20:30
$slots = @()
foreach ($cfgPath in @((Join-Path $here '..\config.json'), (Join-Path $here 'config.json'))) {
    if (-not (Test-Path -LiteralPath $cfgPath)) { continue }
    try {
        $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $raw = $null
        if ($cfg.schedule -and $cfg.schedule.wake_slots) { $raw = $cfg.schedule.wake_slots }
        elseif ($cfg.wake_slots) { $raw = $cfg.wake_slots }
        if ($raw) { $slots = @($raw); break }
    } catch {
        Log ('read ' + $cfgPath + ' failed: ' + $_.Exception.Message)
    }
}
$slots = @($slots | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -match '^\d{1,2}:\d{2}$' } | Select-Object -Unique)
if ($slots.Count -eq 0) {
    $slots = @('09:00', '20:30')
    Log 'wake_slots 未配置，回退默认 09:00 / 20:30'
}
$desc = 'DSH 定时唤醒 ' + ($slots -join ' / ') + '；唤醒后自愈并启动 30 分钟无消息看门狗'
$trigXml = ($slots | ForEach-Object {
    $d = NextOccurrence $_
    Log ('trigger ' + $d.ToString('yyyy-MM-dd HH:mm:ss'))
    @"
    <CalendarTrigger>
      <StartBoundary>$($d.ToString('yyyy-MM-ddTHH:mm:ss'))</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
"@
}) -join "`r`n"

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$env:USERNAME</Author>
    <Description>$desc</Description>
  </RegistrationInfo>
  <Triggers>
$trigXml
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$userId</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>true</WakeToRun>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$ps" -Slot scheduled -Notify</Arguments>
    </Exec>
  </Actions>
</Task>
"@
try {
    $t = Register-ScheduledTask -TaskName 'DSH-Power-Wake' -Xml $xml -Force
    Log ("registered DSH-Power-Wake state=" + $t.State)
} catch {
    Log ("register failed: " + $_.Exception.Message)
}

# 3) 自检
Log '--- verification ---'
$q = & powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE 2>&1
Log ($q -join ' / ')
$wt = & powercfg /waketimers 2>&1
Log ("waketimers: " + ($wt -join ' | '))
$ti = Get-ScheduledTaskInfo -TaskName 'DSH-Power-Wake' -ErrorAction SilentlyContinue
if ($ti) { Log ("next run: " + $ti.NextRunTime) }
Log 'done'
