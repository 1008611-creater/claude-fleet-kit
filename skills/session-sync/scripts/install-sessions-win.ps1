# 在 Windows 上装会话管理：每 15 分钟自动同步本机 Claude app 的侧边栏，不花 token。
# 这台的会话是 SSH 到 Mac 跑的，记录和交接摘要都在 Mac 上，脚本每次通过一次 SSH 问 Mac 要数据。
# 重复运行没关系，相当于更新到 Mac 上的最新版。
$ErrorActionPreference = "Stop"
$Mac = "<your-mac-user>@<YOUR_MAC_TAILSCALE_IP>"
$Dir = "$HOME\.claude\scripts"
New-Item -ItemType Directory -Force $Dir, "$HOME\.claude\session-backups" | Out-Null

Write-Host "1/5 检查 SSH 能不能免密连到 Mac"
$Key = ""
$pub = Get-ChildItem "$HOME\.ssh\*.pub" -ErrorAction SilentlyContinue |
    Where-Object { (Get-Content $_.FullName -Raw) -match "eeo-mbp" } | Select-Object -First 1
if ($pub) { $Key = $pub.FullName -replace '\.pub$', '' }
$opts = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
if ($Key) { $opts += @("-i", $Key) }
$out = & ssh @opts $Mac "echo ok" 2>&1
if ("$out" -notmatch "ok") { throw "SSH 连不上 Mac（$Mac）：$out" }

Write-Host "2/5 拉取最新脚本"
& scp @opts "${Mac}:.claude/scripts/sessions.py" "$Dir\sessions.py"
if ($LASTEXITCODE) { throw "scp 失败" }

Write-Host "3/5 找 Python"
$py = $null
foreach ($c in @("py", "python", "python3")) {
    try {
        $exe = & $c -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe.Trim())) { $py = $exe.Trim(); break }
    } catch {}
}
if (-not $py) {
    Write-Host "   没装 Python，用 winget 装 3.12"
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    $py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (-not (Test-Path $py)) { throw "Python 装完没找到，重开 PowerShell 再跑一次本脚本" }
}
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }

Write-Host "4/5 写配置和 sessions 命令"
$cfg = @{ mac_host = $Mac; ssh_key = $Key } | ConvertTo-Json
[IO.File]::WriteAllText("$HOME\.claude\sessions-remote.json", $cfg, (New-Object Text.UTF8Encoding $false))
[IO.File]::WriteAllText("$Dir\sessions.cmd", "@`"$py`" `"%USERPROFILE%\.claude\scripts\sessions.py`" %*`r`n", (New-Object Text.UTF8Encoding $false))
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($userPath -split ";" | Where-Object { $_ -eq $Dir })) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$Dir", "User")
}

Write-Host "5/5 建计划任务：每 15 分钟跑一次"
schtasks /Create /TN "ClaudeSessionsAuto" /SC MINUTE /MO 15 /TR "`"$pyw`" `"$Dir\sessions.py`" auto" /F | Out-Null
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Set-ScheduledTask -TaskName "ClaudeSessionsAuto" -Settings $set | Out-Null

$root = @("$env:APPDATA\Claude\claude-code-sessions", "$env:LOCALAPPDATA\Claude\claude-code-sessions") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $root) { Write-Warning "没找到 Claude app 的侧边栏目录，确认 Claude 桌面版登录过再重跑" }
else { Write-Host "侧边栏目录：$root" }

Write-Host "`n先跑一次，看现状（不改东西）："
& $py "$Dir\sessions.py"
Write-Host "`n装好了。计划任务会自动同步；手动用法（新开的终端才认 sessions 命令）："
Write-Host "  sessions           看现状"
Write-Host "  sessions --apply   立刻同步"
Write-Host "  sessions brief 线程名   在 Mac 上生成交接摘要"
