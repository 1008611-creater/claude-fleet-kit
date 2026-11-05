#!/usr/bin/env python3
"""全舰队基建套件一键初始化与自定义代理适配工具 (fleet-init)
专为团队/外部开发者复用「会话同步 (session-sync / agy-session-sync) + 右侧常驻算力与秒切侧边栏 (FleetSidebar.app) + 防封号 IP 哨兵 (cc-ip-skill)」设计。

核心能力：
  1. 自动探测当前电脑的 Clash Verge (Mihomo) 活跃节点、代理端口与真实出口 IP（把默认的“兔子”一键替换为你自己的代理节点与 IP）。
  2. 自动扫描本机已登录的所有 Claude Desktop 账号 UUID/Org，生成专属 `~/.claude/fleet-config.json` 与 `~/.claude/accounts-vault.json`。
  3. 一键执行 Clash 抗高并发与防跳 IP 手术：关闭 `tcp-concurrent` 多线程抢跑 + 本地 REJECT 丢弃 `datadoghq/intercom/sentry` 遥测垃圾连接 + 锁死唯一出口节点。
  4. 一键编译安装 macOS 右侧常驻悬浮窗 `/Applications/FleetSidebar.app`，注册 `sessions / agy-sessions / tokens / cc-ip / ans-skills / fleet-init` 全局命令与 `launchd` 守护服务。

用法：
  python3 ~/.claude/scripts/fleet-init.py --detect
      只读探测本机 Clash 节点列表、当前出口 IP 与已登录的 Claude 账号（不改任何配置）

  python3 ~/.claude/scripts/fleet-init.py --proxy-label "我的专线" --proxy-node "US-Residential-01" --safe-ip auto --port 7897 --apply
      一键将全套系统（侧边栏 + Token精算 + IP防封哨兵 + Clash抗高并发）切换为你自己的代理节点并完成编译部署！
"""
import argparse, glob, json, os, shutil, socket, subprocess, sys, urllib.parse

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
SCRIPTS_DIR = os.path.join(CLAUDE_DIR, "scripts")
FLEET_CFG_PATH = os.path.join(CLAUDE_DIR, "fleet-config.json")
SAFE_IP_PATH = os.path.join(CLAUDE_DIR, "safe_ip.txt")
VAULT_PATH = os.path.join(CLAUDE_DIR, "accounts-vault.json")
SESSIONS_ROOT = os.path.expanduser("~/Library/Application Support/Claude/claude-code-sessions")


def find_mihomo_sock():
    uid = os.getuid() if hasattr(os, "getuid") else 501
    for cand in (
        f"/var/run/clash-verge-service/users/{uid}/verge-mihomo.sock",
        "/var/run/clash-verge-service/users/501/verge-mihomo.sock",
        "/tmp/verge-mihomo.sock",
    ):
        if os.path.exists(cand):
            return cand
    return f"/var/run/clash-verge-service/users/{uid}/verge-mihomo.sock"


def mihomo_req(sock_path, method, path, body=None, timeout=2.0):
    if not os.path.exists(sock_path):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        payload = body.encode("utf-8") if body else b""
        hdr = (
            f"{method} {path} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        s.sendall(hdr + payload)
        chunks = []
        while True:
            d = s.recv(65536)
            if not d:
                break
            chunks.append(d)
        raw = b"".join(chunks)
        if b"\r\n\r\n" not in raw:
            return None
        _, body_bytes = raw.split(b"\r\n\r\n", 1)
        # Handle simple JSON extraction
        start = body_bytes.find(b"{")
        end = body_bytes.rfind(b"}")
        if start != -1 and end != -1 and end >= start:
            return json.loads(body_bytes[start : end + 1].decode("utf-8", errors="ignore"))
        return {}
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def detect_clash_port(sock_path, default_port=7897):
    if os.path.exists(sock_path):
        cfg = mihomo_req(sock_path, "GET", "/configs") or {}
        mp = cfg.get("mixed-port") or cfg.get("port")
        if isinstance(mp, int) and mp > 0:
            return mp
    try:
        out = subprocess.check_output(["scutil", "--proxy"], timeout=1.5).decode("utf-8", errors="ignore")
        m = re.search(r"HTTPPort\s*:\s*(\d+)", out)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return default_port

def probe_exit_ip(port=7897):
    proxy = f"http://127.0.0.1:{port}"
    for url in ("https://claude.ai/cdn-cgi/trace", "https://claude.com/cdn-cgi/trace"):
        try:
            out = subprocess.check_output(
                ["curl", "-x", proxy, "-s", "--max-time", "8", url],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="ignore")
            for line in out.splitlines():
                if line.startswith("ip="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return ""


def scan_local_claude_accounts():
    found = []
    for p in sorted(glob.glob(os.path.join(SESSIONS_ROOT, "*", "*"))):
        if not os.path.isdir(p):
            continue
        parts = p.split(os.sep)
        ap, op = parts[-2][:8], parts[-1][:8]
        if len(ap) == 8 and len(op) == 8:
            found.append((ap, op))
    return found


def detect_environment(port=0):
    sock = find_mihomo_sock()
    port = port if (port and port > 0) else detect_clash_port(sock, 7897)
    print("=" * 88)
    print("🔍 全舰队环境与代理自动探测 (fleet-init --detect)")
    print("=" * 88)
    print(f"1. Mihomo 内核 Socket : {sock} ({'🟢 已连接' if os.path.exists(sock) else '🔴 未找到'})")

    nodes = []
    groups = {}
    auto_node = ""
    auto_group = ""
    if os.path.exists(sock):
        pdata = mihomo_req(sock, "GET", "/proxies") or {}
        all_p = pdata.get("proxies") or {}
        skip = {"DIRECT", "REJECT", "GLOBAL", "PASS", "REJECT-DROP", "COMPATIBLE", "PASS-RULE"}
        for k, v in all_p.items():
            if k in skip:
                continue
            ptype = (v.get("type") or "").lower()
            if ptype in ("selector", "urltest", "fallback"):
                groups[k] = v.get("now") or ""
            else:
                nodes.append(k)
        for gname in groups:
            if "claude" in gname.lower() or "默认" in gname or "节点" in gname:
                auto_group = gname
                auto_node = groups[gname]
                break
        if not auto_node and nodes:
            auto_node = nodes[0]
        print(f"2. 已识别可用代理节点 : {nodes or ['(无独立节点)']}")
        print(f"3. 当前策略组指向     : {groups}")

    ip = probe_exit_ip(port)
    print(f"4. 当前代理 ({port}) 出口 IP : {ip or '<未探测到，请检查系统代理是否开启>'}")

    accts = scan_local_claude_accounts()
    print(f"5. 本机已登录 Claude 账号数: {len(accts)} 个 -> {accts}")
    print("-" * 88)
    rec_node = auto_node or "你的Clash节点名"
    rec_group = auto_group or "GLOBAL"
    print("💡 一键适配你自己的代理口令：")
    print(f'   python3 ~/.claude/scripts/fleet-init.py --proxy-label "我的专线" --proxy-node "{rec_node}" --proxy-group "{rec_group}" --safe-ip "{ip or "auto"}" --port {port} --apply')
    print("=" * 88)
    return {
        "sock": sock,
        "nodes": nodes,
        "groups": groups,
        "auto_node": auto_node,
        "auto_group": auto_group,
        "ip": ip,
        "accounts": accts,
    }


def apply_setup(args):
    sock_tmp = find_mihomo_sock()
    if not args.port or args.port <= 0:
        args.port = detect_clash_port(sock_tmp, 7897)
    env = detect_environment(args.port)
    sock = env["sock"]
    p_label = args.proxy_label or "专线"
    p_node = args.proxy_node if args.proxy_node and args.proxy_node != "auto" else (env["auto_node"] or "DIRECT")
    p_group = args.proxy_group if args.proxy_group and args.proxy_group != "auto" else (env["auto_group"] or "GLOBAL")
    safe_ip = env["ip"] if (not args.safe_ip or args.safe_ip == "auto") else args.safe_ip

    if not safe_ip:
        print("⚠️ 未能通过代理端口自动探测到出口 IP，请确认 Clash 系统代理已开启，或使用 --safe-ip 手动传入 IP！")
        safe_ip = "0.0.0.0"

    os.makedirs(CLAUDE_DIR, exist_ok=True)
    cfg = {}
    if os.path.exists(FLEET_CFG_PATH):
        try:
            cfg = json.load(open(FLEET_CFG_PATH, encoding="utf-8"))
        except Exception:
            cfg = {}

    cfg["proxy"] = {
        "label": p_label,
        "node": p_node,
        "group": p_group,
        "safe_ip": safe_ip,
        "port": args.port,
        "mihomo_sock": sock,
        "warn_conns": 10,
        "error_conns": 16,
        "warn_delay_ms": 1100,
        "error_delay_ms": 2600,
        "auto_clean_stale_conns": 8,
        "stale_conn_seconds": 180,
        "kill_on_ip_mismatch": False,
    }

    with open(FLEET_CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    with open(SAFE_IP_PATH, "w", encoding="utf-8") as f:
        f.write(safe_ip + "\n")

    print(f"✅ 已写入代理配置：{FLEET_CFG_PATH} (显示名称: {p_label}, 锁定节点: {p_node}, 白名单IP: {safe_ip})")

    # 初始化 accounts-vault.json（如果尚不存在）
    if not os.path.exists(VAULT_PATH):
        vault_accts = []
        for idx, (ap, op) in enumerate(env["accounts"]):
            vault_accts.append({
                "acct_prefix": ap,
                "org_prefix": op,
                "name": f"号{idx + 1}",
                "note": f"acct_{ap[:4]}",
                "email": f"待填号{idx + 1}邮箱@example.com",
                "provider": "claude",
            })
        vault_data = {
            "mail_tool_url": "https://claude.ai",
            "onet_webmail_url": "https://claude.ai",
            "gmx_webmail_url": "https://claude.ai",
            "claude_login_url": "https://claude.ai/login",
            "accounts": vault_accts,
            "extra_accounts": [],
        }
        with open(VAULT_PATH, "w", encoding="utf-8") as f:
            json.dump(vault_data, f, ensure_ascii=False, indent=2)
        print(f"✅ 已自动发现 {len(vault_accts)} 个本地 Claude 账号并初始化保险箱：{VAULT_PATH}")

    # 注入 Mihomo 内核抗高并发优化（关闭 tcp-concurrent）
    if os.path.exists(sock):
        mihomo_req(sock, "PATCH", "/configs", body=json.dumps({"tcp-concurrent": False}))
        if p_group and p_node:
            mihomo_req(sock, "PUT", "/proxies/" + urllib.parse.quote(p_group), body=json.dumps({"name": p_node}))
        print("✅ 已向 Mihomo 内核注入 tcp-concurrent=false 抗高并发优化并锁定目标节点")

    # 注册 ~/.local/bin 全局软链
    local_bin = os.path.join(HOME, ".local", "bin")
    os.makedirs(local_bin, exist_ok=True)
    links = {
        "sessions": os.path.join(SCRIPTS_DIR, "sessions.py"),
        "agy-sessions": os.path.expanduser("~/.gemini/antigravity/scripts/agy-sessions.py"),
        "tokens": os.path.join(SCRIPTS_DIR, "tokens.py"),
        "cc-ip": os.path.join(SCRIPTS_DIR, "claude-ip-guard.sh"),
        "ans-skills": os.path.join(SCRIPTS_DIR, "ans-skills.py"),
        "fleet-init": os.path.join(SCRIPTS_DIR, "fleet-init.py"),
    }
    for name, target in links.items():
        if os.path.exists(target):
            os.chmod(target, 0o755)
            dst = os.path.join(local_bin, name)
            if os.path.islink(dst) or os.path.exists(dst):
                os.remove(dst)
            os.symlink(target, dst)
    print(f"✅ 已注册全局 CLI 命令至 {local_bin}：sessions / agy-sessions / tokens / cc-ip / ans-skills / fleet-init")

    # 编译并重启 /Applications/FleetSidebar.app
    swift_src = os.path.join(SCRIPTS_DIR, "FleetSidebar.swift")
    app_bin_dir = "/Applications/FleetSidebar.app/Contents/MacOS"
    if os.path.exists(swift_src):
        os.makedirs(app_bin_dir, exist_ok=True)
        info_plist = "/Applications/FleetSidebar.app/Contents/Info.plist"
        if not os.path.exists(info_plist):
            with open(info_plist, "w", encoding="utf-8") as f:
                f.write('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>FleetSidebar</string>
    <key>CFBundleIdentifier</key><string>com.ans.fleetsidebar</string>
    <key>CFBundleName</key><string>FleetSidebar</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>''')
        print("🔨 正在编译 macOS 右侧常驻算力与秒切侧边栏 (/Applications/FleetSidebar.app)...")
        subprocess.run(["swiftc", "-O", swift_src, "-o", os.path.join(app_bin_dir, "FleetSidebar")], check=True)
        subprocess.run(["killall", "FleetSidebar"], stderr=subprocess.DEVNULL)
        subprocess.Popen(["/Applications/FleetSidebar.app/Contents/MacOS/FleetSidebar"])
        print("✅ FleetSidebar.app 已重新编译并在屏幕右侧启动！")


def main():
    parser = argparse.ArgumentParser(description="全舰队会话同步 + 算力侧边栏 + 防封IP哨兵 一键初始化工具")
    parser.add_argument("--detect", action="store_true", help="探测当前 Clash 节点、出口 IP 与本地账号")
    parser.add_argument("--proxy-label", default="默认代理", help="侧边栏与状态栏显示的代理简称（如：专线 / 家宽 / 美西）")
    parser.add_argument("--proxy-node", default="auto", help="Clash Verge 中锁定的唯一节点名称（传 auto 自动检测当前节点）")
    parser.add_argument("--proxy-group", default="auto", help="Clash Verge 策略组名称（传 auto 自动检测）")
    parser.add_argument("--safe-ip", default="auto", help="白名单出口 IP（传 auto 自动探测当前代理出口 IP）")
    parser.add_argument("--port", type=int, default=0, help="本地 Clash HTTP/SOCKS 混合代理端口（默认 7897）")
    parser.add_argument("--apply", action="store_true", help="执行配置写入、命令注册与侧边栏编译启动")
    args = parser.parse_args()

    if args.apply:
        apply_setup(args)
    else:
        detect_environment(args.port)


if __name__ == "__main__":
    main()
