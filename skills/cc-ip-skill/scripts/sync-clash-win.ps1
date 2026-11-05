# 从 Mac (<YOUR_MAC_TAILSCALE_IP>) 一键同步 Clash Verge 唯一权威代理配置 (纯兔子 <YOUR_SAFE_EXIT_IP>，已移除 IPPeak) 到 Windows
$ErrorActionPreference = "Stop"
$Mac = "<your-mac-user>@<YOUR_MAC_TAILSCALE_IP>"

Write-Host "1/4 检查到 Mac ($Mac) 的 SSH 连接..." -ForegroundColor Cyan
$Key = ""
$pub = Get-ChildItem "$HOME\.ssh\*.pub" -ErrorAction SilentlyContinue |
    Where-Object { (Get-Content $_.FullName -Raw) -match "eeo-mbp" } | Select-Object -First 1
if ($pub) { $Key = $pub.FullName -replace '\.pub$', '' }
$opts = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
if ($Key) { $opts += @("-i", $Key) }

$cvDir = @(
    "$env:APPDATA\io.github.clash-verge-rev.clash-verge-rev",
    "$env:APPDATA\io.github.clash-verge-rev",
    "$HOME\.config\clash-verge-rev",
    "$HOME\.config\clash-verge"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $cvDir) {
    $cvDir = "$env:APPDATA\io.github.clash-verge-rev.clash-verge-rev"
    New-Item -ItemType Directory -Force "$cvDir\profiles" | Out-Null
}
$profDir = Join-Path $cvDir "profiles"
New-Item -ItemType Directory -Force $profDir | Out-Null

Write-Host "2/4 备份 Windows 现有 Clash Verge 配置..." -ForegroundColor Cyan
$bakDir = Join-Path $cvDir ("backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
Copy-Item -Path $profDir -Destination $bakDir -Recurse -Force
Write-Host "   已备份至: $bakDir"

Write-Host "3/4 从 Mac 拉取最新链式代理脚本、规则与完整 YAML..." -ForegroundColor Cyan
$tmpDir = Join-Path $env:TEMP "clash-sync-mac"
New-Item -ItemType Directory -Force $tmpDir | Out-Null

& scp @opts "${Mac}:~/workspace/兔子-链式代理完整版.yaml" "$tmpDir\tuzi-full.yaml"
& scp @opts "${Mac}:'~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/profiles/Script.js'" "$tmpDir\Script.js"
& scp @opts "${Mac}:'~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/profiles/rv9QmBXUMSym.yaml'" "$tmpDir\rules.yaml"

# 留一份完整版 YAML 到桌面和下载目录方便随时导入
Copy-Item "$tmpDir\tuzi-full.yaml" "$HOME\Desktop\兔子-链式代理完整版.yaml" -Force -ErrorAction SilentlyContinue
Copy-Item "$tmpDir\tuzi-full.yaml" "$HOME\Downloads\兔子-链式代理完整版.yaml" -Force -ErrorAction SilentlyContinue

Write-Host "4/4 写入 Clash Verge 配置目录 ($profDir)..." -ForegroundColor Cyan
# 更新全局 Script.js 及所有订阅绑定的 *.js 脚本
Copy-Item "$tmpDir\Script.js" (Join-Path $profDir "Script.js") -Force
Get-ChildItem -Path $profDir -Filter "*.js" -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item "$tmpDir\Script.js" $_.FullName -Force
}

# 更新所有规则增强文件 (包含 prepend: 的 yaml)
Get-ChildItem -Path $profDir -Filter "*.yaml" -ErrorAction SilentlyContinue | ForEach-Object {
    $raw = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
    if ($raw -match "Profile Enhancement Rules Template") {
        Copy-Item "$tmpDir\rules.yaml" $_.FullName -Force
    }
}

# 同时保存一份独立订阅文件到 profiles 目录
Copy-Item "$tmpDir\tuzi-full.yaml" (Join-Path $profDir "tuzi-chained.yaml") -Force

Write-Host "`n5/5 正在验证 Windows 当前代理出口 IP 是否与 Mac (兔子 <YOUR_SAFE_EXIT_IP>) 一致..." -ForegroundColor Cyan
try {
    $ipObj = curl.exe -x http://127.0.0.1:7897 -s --max-time 6 https://ipinfo.io/json | ConvertFrom-Json
    Write-Host "   Windows 代理出口: $($ipObj.ip) ($($ipObj.org), $($ipObj.region))" -ForegroundColor Green
    if ($ipObj.ip -eq "<YOUR_SAFE_EXIT_IP>") {
        Write-Host "   🎯 验证通过！Windows 与 Mac 出口 IP 已 100% 对齐为兔子 (<YOUR_SAFE_EXIT_IP>)！" -ForegroundColor Green
    } else {
        Write-Host "   ⚠️ 当前出口还未切到 <YOUR_SAFE_EXIT_IP>，请在 Clash Verge 订阅页右键卡片点『刷新/使用』使其生效！" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   ⚠️ 未检测到 127.0.0.1:7897 响应，请确认 Clash Verge 已启动并在订阅页右键点『刷新/使用』。" -ForegroundColor Yellow
}

Write-Host "`n✅ 同步完成！请在 Windows 的 Clash Verge 中确认：" -ForegroundColor Green
Write-Host "  1. 顶部/左侧切换为「规则 (Rule)」模式" -ForegroundColor Yellow
Write-Host "  2. 在「订阅 (Profiles)」页右键当前激活的订阅卡片 -> 点击「刷新 / 使用」（或直接把桌面的『兔子-链式代理完整版.yaml』拖进订阅页启用）" -ForegroundColor Yellow

Write-Host "`n6/6 正在部署并启动 Windows 端 Claude IP 哨兵 (跳 IP 自动秒关 Claude)..." -ForegroundColor Cyan
& scp @opts "${Mac}:~/.claude/scripts/install-ip-guard-win.ps1" "$tmpDir\install-ip-guard-win.ps1"
& powershell -ExecutionPolicy Bypass -File "$tmpDir\install-ip-guard-win.ps1"
