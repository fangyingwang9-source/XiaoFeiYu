param(
    [switch]$Elevated
)

$bridge = "E:\useer\deepseek-harness\default-workspace\wechat-bridge"
$log = Join-Path $bridge "restart.log"
function Note($msg) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Out-File -FilePath $log -Append -Encoding utf8 }

Note "restart start (elevated=$Elevated)"

if (-not $Elevated) { Start-Sleep -Seconds 8 }

$killed = 0
try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop
    foreach ($p in $procs) {
        if ($p.CommandLine -like "*wechat_bridge.py*") {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Note "kill attempt pid=$($p.ProcessId)"
            $killed++
        }
    }
} catch {
    Note "cim failed: $($_.Exception.Message)"
}

Note "killed=$killed"
if ($killed -eq 0 -and -not $Elevated) {
    "1" | Out-File -FilePath (Join-Path $bridge "restart-need-admin.flag") -Encoding ascii
    Note "need admin, giving up here"
    return
}

Start-Sleep -Seconds 3
try {
    Start-Process -FilePath "wscript.exe" -ArgumentList "`"$bridge\start-bridge-hidden.vbs`"" -WindowStyle Hidden
    Note "relaunch requested"
} catch {
    Note "relaunch failed: $($_.Exception.Message)"
}

Start-Sleep -Seconds 12
$now = @()
try {
    $now = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*wechat_bridge.py*" }
} catch { }
Note "bridge procs after relaunch: $($now.Count)"
if ($now.Count -eq 0) {
    Note "console tail:"
    if (Test-Path (Join-Path $bridge "console.log")) {
        Get-Content (Join-Path $bridge "console.log") -Tail 15 -Encoding UTF8 | Out-File -FilePath $log -Append -Encoding utf8
    }
} elseif ($killed -gt 0) {
    $line = '{"to": "分不清大小王", "text": "重启完了，这回应该正常了"}'
    [System.IO.File]::WriteAllText((Join-Path $bridge "outbox.jsonl"), $line + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Note "outbox queued"
}
