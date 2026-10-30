#Requires -RunAsAdministrator
# selftest.ps1 - 一次性自测：修正 RTCWAKE、注册 75 秒后的唤醒任务，用于验证「睡眠 -> 定时唤醒 -> 桥恢复」
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $here 'install.log'
function Log([string]$m) {
    Add-Content -LiteralPath $log -Value ('{0} [selftest] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8
}

$o1 = & powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>&1
$o2 = & powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>&1
$o3 = & powercfg /setactive SCHEME_CURRENT 2>&1
Log ("ac=" + ($o1 -join '') + " dc=" + ($o2 -join '') + " setactive=" + ($o3 -join ''))
$q = & powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE 2>&1
Log ("query: " + ($q -join ' / '))

$start = (Get-Date).AddSeconds(75)
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$ps = Join-Path $here 'on-wake.ps1'
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$env:USERNAME</Author>
    <Description>DSH 睡眠-唤醒自测（一次性）</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>$($start.ToString('yyyy-MM-ddTHH:mm:ss'))</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
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
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$ps" -Slot selftest -Notify</Arguments>
    </Exec>
  </Actions>
</Task>
"@
try {
    $t = Register-ScheduledTask -TaskName 'DSH-Power-SelfTest' -Xml $xml -Force
    Log ("selftest task registered state=" + $t.State + " at " + $start.ToString('yyyy-MM-dd HH:mm:ss'))
} catch { Log ("register selftest failed: " + $_.Exception.Message) }
$wt = & powercfg /waketimers 2>&1
Log ("waketimers: " + ($wt -join ' | '))
Write-Host 'SELFTEST_READY'
