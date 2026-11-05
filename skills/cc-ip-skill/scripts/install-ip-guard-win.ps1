# Windows 端 Claude IP 哨兵 安装器 (v2)
# 逻辑与 Mac 端 claude-ip-guard.sh v2 对齐：
#   - 出口 IP 跳变（拿到非白名单 IP）→ 立刻强杀 Claude
#   - 探测不到 IP（代理抖动/断网）→ 第 3 次预警、第 6 次才强杀
#   - Claude 没开但 IP 不对 → 每 10 分钟提醒一次"先别开"
# 用法：powershell -ExecutionPolicy Bypass -File install-ip-guard-win.ps1 [-SafeIP 1.2.3.4]
param([string]$SafeIP = "<YOUR_SAFE_EXIT_IP>")

$ErrorActionPreference = "Stop"
$GuardDir    = Join-Path $env:USERPROFILE ".claude\scripts"
$GuardScript = Join-Path $GuardDir "claude-ip-guard.ps1"
$SafeIpFile  = Join-Path $env:USERPROFILE ".claude\safe_ip.txt"
$TaskName    = "ClaudeIPGuard"

New-Item -ItemType Directory -Force $GuardDir | Out-Null
Set-Content -Path $SafeIpFile -Value $SafeIP -Encoding ASCII -NoNewline

$body = @'
# Windows Claude IP 哨兵 v2 —— 由 install-ip-guard-win.ps1 生成
$SafeIpFile = Join-Path $env:USERPROFILE ".claude\safe_ip.txt"
$LogFile    = Join-Path $env:USERPROFILE ".claude\ip-guard.log"
$MsgFile    = Join-Path $env:TEMP "claude-guard-msg.txt"
$ProxyUrl   = "http://127.0.0.1:7897"
$BusyInterval = 8
$IdleInterval = 30
$GraceTicks   = 6
$WarnGapSec   = 600

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Rotate-Log {
    try {
        if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 524288)) {
            Move-Item $LogFile "$LogFile.1" -Force
        }
    } catch {}
}

function Get-SafeIP {
    try { $v = (Get-Content $SafeIpFile -Raw).Trim() } catch { $v = "" }
    if ([string]::IsNullOrWhiteSpace($v)) { return "<YOUR_SAFE_EXIT_IP>" }
    return $v
}

function Get-CurrentExitIP {
    foreach ($u in @("https://checkip.amazonaws.com", "https://ipinfo.io/ip", "https://ifconfig.me/ip")) {
        try {
            $ip = (& curl.exe -x $ProxyUrl -s --max-time 4 $u 2>$null)
            if ($ip -and ($ip.Trim() -match '^\d+\.\d+\.\d+\.\d+$')) { return $ip.Trim() }
        } catch {}
    }
    foreach ($u in @("https://checkip.amazonaws.com", "https://ipinfo.io/ip")) {
        try {
            $ip = (& curl.exe --noproxy "*" -s --max-time 4 $u 2>$null)
            if ($ip -and ($ip.Trim() -match '^\d+\.\d+\.\d+\.\d+$')) { return $ip.Trim() }
        } catch {}
    }
    return ""
}

function Test-ClaudeRunning {
    $p = Get-Process -Name "Claude", "claude" -ErrorAction SilentlyContinue
    return [bool]$p
}

function Show-Toast([string]$title, [string]$msg) {
    try {
        Set-Content -Path $MsgFile -Value $msg -Encoding UTF8
        $cmd = "(New-Object -ComObject Wscript.Shell).Popup((Get-Content -Raw '$MsgFile'),10,'$title',48) | Out-Null"
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $cmd
    } catch {}
}

$script:LastWarn = [datetime]::MinValue
function Warn-Once([string]$msg) {
    $now = Get-Date
    if (($now - $script:LastWarn).TotalSeconds -ge $WarnGapSec) {
        $script:LastWarn = $now
        Write-Log ("WARN: " + $msg)
        Show-Toast "Claude IP Guard" $msg
    }
}

function Stop-ClaudeNow([string]$reason) {
    Write-Log ("KILL: " + $reason)
    Get-Process -Name "Claude", "claude" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Show-Toast "Claude IP Guard" ("出口 IP 异常：$reason`n已强制关闭 Claude 保号，请先把 Clash 切回兔子再开。")
}

Rotate-Log
Write-Log ("guard v2 started, safe=" + (Get-SafeIP))

$failCount = 0
while ($true) {
    $sleep = $IdleInterval
    try {
        $running = Test-ClaudeRunning
        $safe = Get-SafeIP
        $cur  = Get-CurrentExitIP

        if ($cur -and ($cur -eq $safe)) {
            $failCount = 0
            if ($running) { $sleep = $BusyInterval }
        }
        elseif ($cur) {
            $failCount = 0
            if ($running) {
                Stop-ClaudeNow "出口 IP 跳变为 $cur（白名单 $safe）"
                $sleep = $BusyInterval
            } else {
                Warn-Once "当前出口 IP 是 $cur，不是白名单 $safe —— 现在别开 Claude。"
            }
        }
        else {
            $failCount++
            if ($running) {
                if ($failCount -ge $GraceTicks) {
                    Stop-ClaudeNow "代理 7897 连续 $failCount 次探测不到，防直连裸奔"
                    $failCount = 0
                } elseif ($failCount -eq 3) {
                    Warn-Once "代理 7897 探测不到出口 IP，宽限期过后会自动关闭 Claude。"
                }
                $sleep = $BusyInterval
            } else {
                if ($failCount -eq 3) { Warn-Once "代理 7897 探测不到出口 IP，先别开 Claude。" }
            }
        }
    } catch {
        Write-Log ("ERR: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $sleep
}
'@

Set-Content -Path $GuardScript -Value $body -Encoding UTF8

# 停掉旧实例
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "claude-ip-guard\.ps1" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# 注册开机自启（含崩溃重启）
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$GuardScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

# 立刻拉起
Start-Process powershell.exe -ArgumentList "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$GuardScript`"" -WindowStyle Hidden
Start-Sleep -Seconds 2

$proc = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "claude-ip-guard\.ps1" }
if ($proc) {
    Write-Host "OK Windows 端 IP 哨兵已启动 (PID $($proc.ProcessId -join ','))，白名单: $SafeIP" -ForegroundColor Green
    Write-Host "   计划任务: $TaskName (登录自启，崩溃自动重启)"
    Write-Host "   日志: $LogFile"
} else {
    Write-Host "!! 哨兵没能启动，请检查 $GuardScript" -ForegroundColor Red
    exit 1
}
