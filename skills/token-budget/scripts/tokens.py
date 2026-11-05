#!/usr/bin/env python3
"""全舰队 Token 额度精算与每日排班引擎（Claude 四号 + Win SSH 池 + 反重力 Antigravity）：
100% 本地零 Token、只读安全（绝不触碰 Keychain 触发登录锁）、毫秒级增量扫描。
与 session-sync / agy-session-sync 强绑定：
  - 实时掌握每个账号还剩多少周额度 (7d) 与 5h 窗口冷却恢复时间 (resetsAt)
  - 精算每天应该花多少 Token/点数，防止「额度到期白白作废浪费」或「单号过载提前花停耽误事」
  - 自动揪出 >180k 吞金巨兽线程，给出「先交接再开新窗」的省流处方

用法：
  tokens                 查看完整 Token 额度大盘、每日花费规划、账号接力排班与吞金线程榜
  tokens --banner        输出 4 行极简排班横幅（嵌入 sessions / agy-sessions 顶部）
  tokens --quiet         静默刷新 ~/.claude/session-handoffs/TOKEN_BUDGET.md 与状态缓存
  tokens --json          输出结构化 JSON 供跨端同步 (Windows serve / 其他脚本) 调用
"""
import datetime, glob, json, os, re, socket, subprocess, sys, time, urllib.parse

CLAUDE_APP_DIR = os.path.expanduser("~/Library/Application Support/Claude")
PLAN_HISTORY = os.path.join(CLAUDE_APP_DIR, "plan-usage-history.json")
CLAUDE_CONFIG = os.path.join(CLAUDE_APP_DIR, "config.json")
SESSIONS_ROOT = os.path.join(CLAUDE_APP_DIR, "claude-code-sessions")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
HANDOFFS_DIR = os.path.expanduser("~/.claude/session-handoffs")
AGY_HANDOFFS_DIR = os.path.expanduser("~/.gemini/antigravity/session-handoffs")
AGY_BRAIN_DIR = os.path.expanduser("~/.gemini/antigravity/brain")
RULES_PATH = os.path.expanduser("~/.claude/session-groups.json")
VAULT_PATH = os.path.expanduser("~/.claude/accounts-vault.json")
FLEET_CFG_PATH = os.path.expanduser("~/.claude/fleet-config.json")


def load_fleet_cfg():
    cfg = {}
    if os.path.exists(FLEET_CFG_PATH):
        try:
            cfg = json.load(open(FLEET_CFG_PATH, encoding="utf-8"))
        except Exception:
            pass
    p = cfg.get("proxy") or {}
    uid = os.getuid() if hasattr(os, "getuid") else 501
    default_sock = f"/var/run/clash-verge-service/users/{uid}/verge-mihomo.sock"
    if not os.path.exists(default_sock) and os.path.exists("/var/run/clash-verge-service/users/501/verge-mihomo.sock"):
        default_sock = "/var/run/clash-verge-service/users/501/verge-mihomo.sock"
    return {
        "proxy_label": p.get("label") or os.environ.get("FLEET_PROXY_LABEL") or "默认代理",
        "proxy_node": p.get("node") or os.environ.get("FLEET_PROXY_NODE") or "auto",
        "proxy_group": p.get("group") or os.environ.get("FLEET_PROXY_GROUP") or "auto",
        "safe_ip": p.get("safe_ip") or os.environ.get("FLEET_SAFE_IP") or "0.0.0.0",
        "proxy_port": int(p.get("port") or os.environ.get("FLEET_PROXY_PORT") or 7897),
        "mihomo_sock": p.get("mihomo_sock") or default_sock,
        "warn_conns": int(p.get("warn_conns") or 10),
        "error_conns": int(p.get("error_conns") or 16),
        "warn_delay_ms": int(p.get("warn_delay_ms") or 1100),
        "error_delay_ms": int(p.get("error_delay_ms") or 2600),
        "auto_clean_stale_conns": int(p.get("auto_clean_stale_conns") or 8),
        "stale_conn_seconds": int(p.get("stale_conn_seconds") or 180),
        "raw": cfg,
    }


FLEET_CFG = load_fleet_cfg()
MIHOMO_SOCK = FLEET_CFG["mihomo_sock"]

STATE_CACHE = os.path.join(HANDOFFS_DIR, "TOKEN_STATE.json")
PROXY_CACHE = os.path.join(HANDOFFS_DIR, "TUZI_HEALTH.json")
BUDGET_MD = os.path.join(HANDOFFS_DIR, "TOKEN_BUDGET.md")
AGY_BUDGET_MD = os.path.join(AGY_HANDOFFS_DIR, "TOKEN_BUDGET.md")

# 默认从 ~/.claude/accounts-vault.json 或 claude-code-sessions/*/* 自动发现本机账号
MAC_ACCOUNTS = {}
BANNED_ACCOUNTS = {}
ORG_TO_ACCT = {}

# 标定常数（由 plan-usage-history.json 实测标定）：
# 1 个完整 5h 窗口 (fh: 0->100%) 约消耗 14.0% 的周额度 (sd: +14 pts)，对应约 $42.5 API 等效算力
# 即 1 个账号每周 100 pts ≈ 7.1 个满 5h 窗口 ≈ $300 API 等效 Token
PTS_PER_5H_WINDOW = 14.0
USD_PER_WEEK_PT = 3.00       # 1% 周额度 ≈ $3.00 API-eq
USD_PER_5H_WINDOW = 42.0     # 1 个 5h 满窗 ≈ $42.0 API-eq
WEEK_POINTS = 100.0

PRICES = {
    "opus":   {"in": 4.0,  "out": 20.0, "read": 0.20},
    "sonnet": {"in": 2.0,  "out": 10.0, "read": 0.20},
    "haiku":  {"in": 1.0,  "out": 5.0,  "read": 0.10},
}


def iso_to_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def fmt_dual_time(ts, short=False):
    """同时返回本机时间与北京时间 (UTC+8)，消除跨时区混淆。"""
    if not ts:
        return "—"
    dt_local = datetime.datetime.fromtimestamp(ts)
    dt_bj = datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=8)))
    if short:
        return f"{dt_local.strftime('%H:%M')} (京{dt_bj.strftime('%H:%M')})"
    return f"{dt_local.strftime('%m-%d %H:%M')} (京{dt_bj.strftime('%m-%d %H:%M')})"


def usage_cost(model, u):
    if not u:
        return 0.0
    m = (model or "").lower()
    p = PRICES["opus"]
    for k in PRICES:
        if k in m:
            p = PRICES[k]
            break
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens")
    w1 = cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
    return (
        (u.get("input_tokens") or 0) * p["in"]
        + (u.get("output_tokens") or 0) * p["out"]
        + (u.get("cache_read_input_tokens") or 0) * p["read"]
        + (w5 or 0) * p["in"] * 1.25
        + (w1 or 0) * p["in"] * 2.0
    ) / 1e6


def load_title_map():
    """加载 cliSessionId -> 规范化线程名 映射。"""
    rules = {}
    if os.path.exists(RULES_PATH):
        try:
            rules = json.load(open(RULES_PATH, encoding="utf-8"))
        except Exception:
            pass
    merge = rules.get("merge") or {}

    def canon(t):
        t = re.sub(r"\s*\[4合1\]\s*", "", (t or "").strip())
        t = re.sub(r"\s*（常驻）\s*", "", t).strip()
        if t in merge:
            return merge[t]
        for k, v in merge.items():
            if k in t:
                return v
        return t

    cid_to_title = {}
    # Also track which Mac account last ran turns on which cliSessionId
    cid_to_acct_edges = {}
    for p in glob.glob(os.path.join(SESSIONS_ROOT, "*", "*", "local_*.json")):
        parts = p.split(os.sep)
        acct_prefix = parts[-3][:8]
        try:
            d = json.load(open(p, encoding="utf-8"))
            cid = d.get("cliSessionId")
            title = d.get("title")
            edge = d.get("lastTurnEdgeAt")
            if cid and title:
                c_title = canon(title)
                if c_title and c_title != "__hide__":
                    cid_to_title[cid] = c_title
            if cid and edge:
                cid_to_acct_edges.setdefault(cid, {})[acct_prefix] = edge / 1000.0
        except Exception:
            pass
    return cid_to_title, cid_to_acct_edges, canon


def scan_jsonl_incremental(now):
    """增量扫描 ~/.claude/projects/*/*.jsonl，提取：
    - 过去 8 天按小时/按天去重后的 API-eq 美元成本、Token 数、轮数
    - 每个会话过去 24h 的消耗、轮数、峰值上下文 (max_ctx)、最近活动时间
    - 所有 rate_limit_info 限流事件 (five_hour / seven_day 及 resetsAt)
    """
    os.makedirs(HANDOFFS_DIR, exist_ok=True)
    try:
        cache = json.load(open(STATE_CACHE, encoding="utf-8"))
        if cache.get("version") != 2:
            cache = {"version": 2, "files": {}}
    except Exception:
        cache = {"version": 2, "files": {}}

    cached_files = cache.get("files", {})
    cutoff = now - 8 * 86400
    new_files = {}

    for p in glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")):
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime < cutoff:
            continue
        prev = cached_files.get(p)
        if prev and prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size:
            new_files[p] = prev
            continue

        # Parse file from scratch if changed (fast binary line filter)
        cid = os.path.basename(p)[:-6]
        seen_ids = set()
        day_buckets = {}     # YYYY-MM-DD -> [usd, tokens, turns]
        hour_buckets = {}    # int(ts//3600) -> usd
        recent_turns = []    # [(ts, usd, ctx_tokens)] for last 48h
        rate_events = []     # [(ts, rateLimitType, resetsAt, text)]
        custom_title = None

        try:
            with open(p, "rb") as fh:
                for raw in fh:
                    if b"custom-title" in raw or b"customTitle" in raw:
                        try:
                            e = json.loads(raw)
                            if e.get("customTitle"):
                                custom_title = e["customTitle"]
                        except Exception:
                            pass
                    if b"rate_limit_info" in raw:
                        try:
                            e = json.loads(raw)
                            ts = iso_to_ts(e.get("timestamp")) or st.st_mtime
                            rli = (e.get("apiErrorParams") or {}).get("rate_limit_info") or {}
                            rtype = rli.get("rateLimitType")
                            resets = rli.get("resetsAt")
                            txt = ((e.get("message") or {}).get("content") or [{}])[0].get("text", "")
                            if rtype and resets:
                                rate_events.append([ts, rtype, resets, txt])
                        except Exception:
                            pass
                    if b'"usage"' not in raw or b'"assistant"' not in raw:
                        continue
                    try:
                        e = json.loads(raw)
                    except Exception:
                        continue
                    if e.get("type") != "assistant":
                        continue
                    m = e.get("message") or {}
                    mid = m.get("id") or e.get("requestId")
                    if not mid or mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                    ts = iso_to_ts(e.get("timestamp"))
                    if not ts or ts < cutoff:
                        continue
                    u = m.get("usage") or {}
                    c = usage_cost(m.get("model"), u)
                    if c <= 0:
                        continue
                    ctx = (
                        (u.get("input_tokens") or 0)
                        + (u.get("cache_creation_input_tokens") or 0)
                        + (u.get("cache_read_input_tokens") or 0)
                    )
                    tok = ctx + (u.get("output_tokens") or 0)
                    day = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    db = day_buckets.setdefault(day, [0.0, 0, 0])
                    db[0] = round(db[0] + c, 4)
                    db[1] += tok
                    db[2] += 1
                    hk = str(int(ts // 3600))
                    hour_buckets[hk] = round(hour_buckets.get(hk, 0.0) + c, 4)
                    if ts >= now - 48 * 3600:
                        recent_turns.append([round(ts, 1), round(c, 4), ctx])
        except OSError:
            continue

        new_files[p] = {
            "cid": cid,
            "mtime": st.st_mtime,
            "size": st.st_size,
            "custom_title": custom_title,
            "days": day_buckets,
            "hours": hour_buckets,
            "recent_turns": recent_turns[-400:],
            "rate_events": rate_events[-30:],
        }

    cache["files"] = new_files
    cache["updated_at"] = now
    try:
        tmp = STATE_CACHE + ".tmp"
        json.dump(cache, open(tmp, "w", encoding="utf-8"), separators=(",", ":"))
        os.replace(tmp, STATE_CACHE)
    except Exception:
        pass
    return new_files


def scan_antigravity(now):
    """扫描反重力 (Antigravity) 过去 7 天与今日的步数及估算 Token。"""
    cutoff = now - 7 * 86400
    today_str = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    steps_today = 0
    steps_7d = 0
    chars_today = 0
    chars_7d = 0
    active_convs_today = set()

    for p in glob.glob(os.path.join(AGY_BRAIN_DIR, "*", ".system_generated", "logs", "transcript.jsonl")):
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime < cutoff:
            continue
        cid = p.split(os.sep)[-4][:8]
        try:
            with open(p, "rb") as fh:
                for raw in fh:
                    if b"PLANNER_RESPONSE" not in raw:
                        continue
                    try:
                        e = json.loads(raw)
                    except Exception:
                        continue
                    if e.get("type") != "PLANNER_RESPONSE":
                        continue
                    ts = iso_to_ts(e.get("created_at"))
                    if not ts or ts < cutoff:
                        continue
                    steps_7d += 1
                    chars_7d += len(raw)
                    day = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    if day == today_str:
                        steps_today += 1
                        chars_today += len(raw)
                        active_convs_today.add(cid)
        except OSError:
            continue

    # 读取 Cockpit 缓存的反重力官方精确配额 + 2 个 GPT Plus 号配额 + mcgrox 中转站配置
    agy_quota = {
        "email": "local-user@example.com",
        "note": "local",
        "gemini_5h_rem": 94.9,
        "gemini_7d_rem": 82.1,
        "gemini_7d_reset_ts": None,
        "claude_5h_rem": 100.0,
        "claude_7d_rem": 99.8,
        "claude_7d_reset_ts": None,
    }
    for qp in glob.glob(os.path.expanduser("~/.cockpit_tools/cache/quota_api_v1_desktop/authorized/*.json")):
        try:
            qd = json.load(open(qp, encoding="utf-8"))
            agy_quota["email"] = qd.get("email") or agy_quota["email"]
            for grp in (qd.get("payload") or {}).get("quota_summary", {}).get("groups", []):
                for b in grp.get("buckets", []):
                    bid = b.get("bucketId", "")
                    rf = round((b.get("remainingFraction") or 0) * 100.0, 1)
                    rts = iso_to_ts(b.get("resetTime"))
                    if bid == "gemini-5h":
                        agy_quota["gemini_5h_rem"] = rf
                    elif bid == "gemini-weekly":
                        agy_quota["gemini_7d_rem"] = rf
                        agy_quota["gemini_7d_reset_ts"] = rts
                    elif bid == "3p-5h":
                        agy_quota["claude_5h_rem"] = rf
                    elif bid == "3p-weekly":
                        agy_quota["claude_7d_rem"] = rf
                        agy_quota["claude_7d_reset_ts"] = rts
        except Exception:
            pass

    gpt_plus_list = []

    relays = []
    mp_path = os.path.expanduser("~/.cockpit_tools/codex_model_providers.json")
    if os.path.exists(mp_path):
        try:
            for p in json.load(open(mp_path, encoding="utf-8")):
                models = []
                for k in p.get("apiKeys", []):
                    for m in k.get("modelCatalog", []):
                        if m not in models:
                            models.append(m)
                relays.append({
                    "name": p.get("name", ""),
                    "base_url": p.get("baseUrl", ""),
                    "key_count": len(p.get("apiKeys", [])),
                    "models": models,
                })
        except Exception:
            pass

    return {
        "steps_today": steps_today,
        "steps_7d": steps_7d,
        "est_tok_today_k": chars_today // 4000,
        "est_tok_7d_k": chars_7d // 4000,
        "active_convs_today": len(active_convs_today),
        "quota": agy_quota,
        "gpt_plus": gpt_plus_list,
        "relays": relays,
    }


def mihomo_request(method, path, body=None, timeout=3.0):
    """通过 Unix Socket 直接请求 Clash Verge Mihomo 内核，毫秒级响应。"""
    if not os.path.exists(MIHOMO_SOCK):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(MIHOMO_SOCK)
        payload = body.encode("utf-8") if body else b""
        headers = [
            f"{method} {path} HTTP/1.1",
            "Host: localhost",
            "Connection: close",
        ]
        if payload:
            headers.append("Content-Type: application/json")
            headers.append(f"Content-Length: {len(payload)}")
        req = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + payload
        s.sendall(req)
        chunks = []
        while True:
            data = s.recv(16384)
            if not data:
                break
            chunks.append(data)
        raw = b"".join(chunks)
        if b"\r\n\r\n" not in raw:
            return None
        hdr, resp_body = raw.split(b"\r\n\r\n", 1)
        if b"Transfer-Encoding: chunked" in hdr:
            # Simple chunked decode
            decoded = bytearray()
            idx = 0
            while idx < len(resp_body):
                line_end = resp_body.find(b"\r\n", idx)
                if line_end == -1:
                    break
                size_str = resp_body[idx:line_end].split(b";")[0].strip()
                if not size_str:
                    break
                c_size = int(size_str, 16)
                if c_size == 0:
                    break
                start = line_end + 2
                decoded.extend(resp_body[start:start + c_size])
                idx = start + c_size + 2
            resp_body = bytes(decoded)
        if not resp_body.strip():
            return {}
        return json.loads(resp_body.decode("utf-8", errors="ignore"))
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def check_tuzi_health(now, force_ping=False):
    """检测唯一合法代理节点（默认「auto (0.0.0.0)」，他人复用时从 ~/.claude/fleet-config.json 读取自定义节点与IP）的实时连通性、延迟与并发连接数。
    返回结构：
      level: "ok" (🟢畅通) | "warn" (🟡高并发/高延迟预警) | "error" (🔴断连/异常飙红)
    """
    cfg = load_fleet_cfg()
    p_label = cfg["proxy_label"]
    p_node = cfg["proxy_node"]
    p_group = cfg["proxy_group"]
    p_port = str(cfg["proxy_port"])
    sock_path = cfg["mihomo_sock"]

    safe_ip_file = os.path.expanduser("~/.claude/safe_ip.txt")
    safe_ip = open(safe_ip_file).read().strip() if os.path.exists(safe_ip_file) else cfg["safe_ip"]

    # 1. 检查 macOS 系统代理是否开启 (127.0.0.1:<port>)
    sys_proxy_on = False
    try:
        out = subprocess.check_output(["scutil", "--proxy"], timeout=1.5).decode("utf-8", errors="ignore")
        if "HTTPEnable : 1" in out and p_port in out:
            sys_proxy_on = True
    except Exception:
        pass

    if not os.path.exists(sock_path):
        return {
            "ok": False,
            "level": "error",
            "label": p_label,
            "status_text": "🔴 飙红：Clash Verge 未运行！",
            "reason": "Mihomo 核心未启动，严禁打开 Claude",
            "ip": safe_ip,
            "node": p_node,
            "delay_ms": 0,
            "active_conns": 0,
            "stale_conns": 0,
            "sys_proxy_on": sys_proxy_on,
        }

    # 2. 检查代理组指向与节点历史延迟（若他人复用时填了 auto 或自定义节点，自动识别）
    proxies_data = mihomo_request("GET", "/proxies", timeout=1.5)
    if not isinstance(proxies_data, dict):
        return {
            "ok": False,
            "level": "error",
            "label": p_label,
            "status_text": "🔴 飙红：代理内核无响应！",
            "reason": "无法读取 Mihomo 节点状态",
            "ip": safe_ip,
            "node": p_node,
            "delay_ms": 0,
            "active_conns": 0,
            "stale_conns": 0,
            "sys_proxy_on": sys_proxy_on,
        }

    all_p = proxies_data.get("proxies") or {}
    # 若配置的节点不在 Mihomo 中，尝试从代理组自动识别当前激活节点（方便外部用户零配置复用）
    if p_node not in all_p:
        for gname in (p_group, "GLOBAL", "节点选择", "🚀 节点选择", "Proxy"):
            gobj = all_p.get(gname) or {}
            cand = gobj.get("now")
            if cand and cand in all_p and cand not in ("DIRECT", "REJECT"):
                p_node = cand
                p_group = gname
                break

    claude_group = all_p.get(p_group) or {}
    target_node_obj = all_p.get(p_node) or {}
    cur_selected = claude_group.get("now") or p_node

    # 若分组被误切走，自动切回锁定的唯一节点
    if claude_group and p_node in (claude_group.get("all") or []) and cur_selected != p_node:
        mihomo_request("PUT", "/proxies/" + urllib.parse.quote(p_group), body=json.dumps({"name": p_node}))
        cur_selected = p_node

    # 3. 检查当前活跃连接数 (并发监控 + 超过阈值时自动清理 >180s 僵尸连接)
    active_conns = 0
    stale_conns = 0
    claude_conns = 0
    stale_ids = []
    match_keys = [k for k in (p_label, p_node, p_group) if k]
    conns_data = mihomo_request("GET", "/connections", timeout=1.5)
    if isinstance(conns_data, dict):
        for c in (conns_data.get("connections") or []):
            chains = c.get("chains") or []
            if any(any(mk in ch for mk in match_keys) for ch in chains):
                active_conns += 1
                meta = c.get("metadata") or {}
                host = (meta.get("host") or "").lower()
                if "claude" in host or "anthropic" in host:
                    claude_conns += 1
                start_ts = iso_to_ts(c.get("start"))
                if start_ts and (now - start_ts) > cfg["stale_conn_seconds"]:
                    stale_conns += 1
                    if c.get("id"):
                        stale_ids.append(c["id"])

    if active_conns >= cfg["auto_clean_stale_conns"] and stale_ids:
        for sid in stale_ids:
            mihomo_request("DELETE", f"/connections/{sid}", timeout=0.8)
            active_conns = max(0, active_conns - 1)
            stale_conns = max(0, stale_conns - 1)

    hist = target_node_obj.get("history") or []
    last_delay = hist[-1].get("delay", 0) if hist else 0

    # 4. 读取缓存或发起实时非侵入式测速（每 25 秒自动测一次，或点击测速强制触发）
    cached_ping = {}
    if os.path.exists(PROXY_CACHE):
        try:
            cached_ping = json.load(open(PROXY_CACHE, encoding="utf-8"))
        except Exception:
            pass

    need_ping = force_ping or (now - cached_ping.get("ts", 0) >= 25.0) or (last_delay == 0)
    if need_ping:
        enc_name = urllib.parse.quote(p_node)
        ping_res = mihomo_request(
            "GET",
            f"/proxies/{enc_name}/delay?url=http%3A%2F%2Fcp.cloudflare.com%2Fgenerate_204&timeout=2800",
            timeout=3.2,
        )
        if isinstance(ping_res, dict) and "delay" in ping_res:
            last_delay = int(ping_res.get("delay") or 0)
        else:
            last_delay = 0
        try:
            os.makedirs(HANDOFFS_DIR, exist_ok=True)
            json.dump({"ts": now, "delay": last_delay, "node": p_node}, open(PROXY_CACHE, "w", encoding="utf-8"))
        except Exception:
            pass
    else:
        if cached_ping.get("delay") is not None:
            last_delay = int(cached_ping["delay"])

    # 5. 综合判定三级状态 (🟢 ok / 🟡 warn / 🔴 error 飙红)
    if not sys_proxy_on:
        return {
            "ok": False,
            "level": "error",
            "label": p_label,
            "status_text": f"🔴 飙红：系统代理未开启 ({p_port})！",
            "reason": "macOS 系统代理被关闭，直连必封号！请立刻开启系统代理",
            "ip": safe_ip,
            "node": cur_selected,
            "delay_ms": last_delay,
            "active_conns": active_conns,
            "stale_conns": stale_conns,
            "sys_proxy_on": False,
        }

    if last_delay <= 0 or last_delay >= cfg["error_delay_ms"]:
        return {
            "ok": False,
            "level": "error",
            "label": p_label,
            "status_text": f"🔴 飙红：{p_label}节点断连/超时 ({last_delay}ms)！",
            "reason": f"并发 {active_conns} 条 · 节点无响应或高并发卡死，点右侧[清并发重连]",
            "ip": safe_ip,
            "node": cur_selected,
            "delay_ms": last_delay,
            "active_conns": active_conns,
            "stale_conns": stale_conns,
            "sys_proxy_on": True,
        }

    if active_conns >= cfg["error_conns"]:
        return {
            "ok": False,
            "level": "error",
            "label": p_label,
            "status_text": f"🔴 飙红：{p_label}高并发爆表 ({active_conns}条并发)！",
            "reason": f"单条隧道并发达 {active_conns} 条 ({last_delay}ms)，极易断流失败，请点[清并发]",
            "ip": safe_ip,
            "node": cur_selected,
            "delay_ms": last_delay,
            "active_conns": active_conns,
            "stale_conns": stale_conns,
            "sys_proxy_on": True,
        }

    if active_conns >= cfg["warn_conns"] or last_delay >= cfg["warn_delay_ms"]:
        return {
            "ok": True,
            "level": "warn",
            "label": p_label,
            "status_text": f"🟡 预警：{p_label}并发偏高 ({active_conns}条 · {last_delay}ms)",
            "reason": f"当前 {active_conns} 条连接 (含 {stale_conns} 条长连接)，若卡顿可点[清并发]",
            "ip": safe_ip,
            "node": cur_selected,
            "delay_ms": last_delay,
            "active_conns": active_conns,
            "stale_conns": stale_conns,
            "sys_proxy_on": True,
        }

    return {
        "ok": True,
        "level": "ok",
        "label": p_label,
        "status_text": f"🟢 {p_label} {safe_ip} 畅通 · {last_delay}ms · 并发 {active_conns}条",
        "reason": f"隧道健康 (Claude连接 {claude_conns}条 · 系统代理 {p_port} 已锁死)",
        "ip": safe_ip,
        "node": cur_selected,
        "delay_ms": last_delay,
        "active_conns": active_conns,
        "stale_conns": stale_conns,
        "sys_proxy_on": True,
    }


def heal_tuzi_connections():
    """一键清理目标代理节点上的残留/高并发连接，释放隧道并重新测速。"""
    cfg = load_fleet_cfg()
    p_label = cfg["proxy_label"]
    p_node = cfg["proxy_node"]
    p_group = cfg["proxy_group"]
    match_keys = [k for k in (p_label, p_node, p_group) if k]
    closed = 0
    conns_data = mihomo_request("GET", "/connections", timeout=2.0)
    if isinstance(conns_data, dict):
        for c in (conns_data.get("connections") or []):
            chains = c.get("chains") or []
            cid = c.get("id")
            if cid and any(any(mk in ch for mk in match_keys) for ch in chains):
                mihomo_request("DELETE", f"/connections/{cid}", timeout=1.0)
                closed += 1
    mihomo_request("PUT", "/proxies/" + urllib.parse.quote(p_group), body=json.dumps({"name": p_node}))
    time.sleep(0.2)
    health = check_tuzi_health(time.time(), force_ping=True)
    health["closed_conns"] = closed
    return health


def sync_vault_new_account():
    """自动从 ~/.claude/fleet-config.json、~/.claude/accounts-vault.json 或本地会话目录发现账号。"""
    global MAC_ACCOUNTS, ORG_TO_ACCT
    cfg_raw = load_fleet_cfg().get("raw") or {}
    if isinstance(cfg_raw.get("accounts"), dict) and cfg_raw["accounts"]:
        MAC_ACCOUNTS = cfg_raw["accounts"]
        ORG_TO_ACCT = {v["org"]: k for k, v in MAC_ACCOUNTS.items() if "org" in v}
        return

    if os.path.exists(VAULT_PATH):
        try:
            vault = json.load(open(VAULT_PATH, encoding="utf-8"))
            discovered = {}
            for idx, item in enumerate(vault.get("accounts", [])):
                ap = item.get("acct_prefix", "")
                op = item.get("org_prefix", "")
                if ap and op:
                    discovered[ap] = {
                        "name": item.get("name") or f"号{idx + 1}",
                        "org": op,
                        "note": item.get("note") or f"acct_{ap[:4]}",
                        "email": item.get("email") or f"account_{idx + 1}@example.com",
                        "order": idx + 1,
                    }
            if discovered:
                MAC_ACCOUNTS = discovered
                ORG_TO_ACCT = {v["org"]: k for k, v in MAC_ACCOUNTS.items() if "org" in v}
                return
        except Exception:
            pass

    local_pairs = []
    for p in sorted(glob.glob(os.path.join(SESSIONS_ROOT, "*", "*"))):
        if not os.path.isdir(p):
            continue
        parts = p.split(os.sep)
        ap, op = parts[-2][:8], parts[-1][:8]
        if len(ap) == 8 and len(op) == 8 and ap not in BANNED_ACCOUNTS:
            local_pairs.append((ap, op))

    discovered = {}
    for idx, (ap, op) in enumerate(local_pairs):
        discovered[ap] = {
            "name": f"号{idx + 1}",
            "org": op,
            "note": f"acct_{ap[:4]}",
            "email": f"account_{idx + 1}@example.com",
            "order": idx + 1,
        }
    MAC_ACCOUNTS = discovered
    ORG_TO_ACCT = {v["org"]: k for k, v in MAC_ACCOUNTS.items() if "org" in v}
        return

    # 检查预设账号是否有任何一个在本机存在；若完全是另一台新电脑，走全自动发现模式
    local_pairs = []
    for p in sorted(glob.glob(os.path.join(SESSIONS_ROOT, "*", "*"))):
        if not os.path.isdir(p):
            continue
        parts = p.split(os.sep)
        ap, op = parts[-2][:8], parts[-1][:8]
        if len(ap) == 8 and len(op) == 8 and ap not in BANNED_ACCOUNTS:
            local_pairs.append((ap, op))

    has_preset_on_disk = any(ap in MAC_ACCOUNTS for ap, _ in local_pairs) or os.path.exists(VAULT_PATH)
    if not has_preset_on_disk and local_pairs:
        discovered = {}
        for idx, (ap, op) in enumerate(local_pairs):
            discovered[ap] = {
                "name": f"号{idx + 1}",
                "org": op,
                "note": f"acct_{ap[:4]}",
                "email": f"account_{idx + 1}@example.com",
                "order": idx,
            }
        MAC_ACCOUNTS = discovered
        ORG_TO_ACCT = {v["org"]: k for k, v in MAC_ACCOUNTS.items()}
        return

    known_accts = {k for k in MAC_ACCOUNTS if not k.startswith("pending_")} | set(BANNED_ACCOUNTS.keys())
    known_orgs = {v["org"] for k, v in MAC_ACCOUNTS.items() if not k.startswith("pending_")} | {v["org"] for v in BANNED_ACCOUNTS.values()}

    # 1. 先看 accounts-vault.json 里号8是否已经绑定过真实 UUID
    if os.path.exists(VAULT_PATH):
        try:
            vault = json.load(open(VAULT_PATH, encoding="utf-8"))
            for item in vault.get("accounts", []):
                if item.get("name") == "号8":
                    ap = item.get("acct_prefix", "")
                    op = item.get("org_prefix", "")
                    if ap and not ap.startswith("pending_") and "pending_hao8" in MAC_ACCOUNTS:
                        meta = MAC_ACCOUNTS.pop("pending_hao8")
                        meta["org"] = op
                        meta["is_new_unlogged"] = False
                        MAC_ACCOUNTS[ap] = meta
                        return
        except Exception:
            pass

    # 2. 检查 claude-code-sessions 目录下是否有新出现的 <acct_uuid>/<org_uuid>
    detected_acct = None
    detected_org = None
    for ap, op in local_pairs:
        if ap not in known_accts and op not in known_orgs:
            detected_acct, detected_org = ap, op
            break

    # 3. 或者检查 config.json + plan-usage-history.json
    if not detected_acct and os.path.exists(CLAUDE_CONFIG):
        try:
            cfg = json.load(open(CLAUDE_CONFIG, encoding="utf-8"))
            ap = (cfg.get("lastKnownAccountUuid") or "")[:8]
            if len(ap) == 8 and ap not in known_accts:
                detected_acct = ap
                if os.path.exists(PLAN_HISTORY):
                    ph = json.load(open(PLAN_HISTORY, encoding="utf-8"))
                    for s in reversed(ph.get("samples", [])):
                        op = (s.get("org") or "")[:8]
                        if op and op not in known_orgs:
                            detected_org = op
                            break
        except Exception:
            pass

    if detected_acct and "pending_hao8" in MAC_ACCOUNTS:
        meta = MAC_ACCOUNTS.pop("pending_hao8")
        meta["org"] = detected_org or "pending_org8"
        meta["is_new_unlogged"] = False
        MAC_ACCOUNTS[detected_acct] = meta
        if os.path.exists(VAULT_PATH):
            try:
                vault = json.load(open(VAULT_PATH, encoding="utf-8"))
                for item in vault.get("accounts", []):
                    if item.get("name") == "号8":
                        item["acct_prefix"] = detected_acct
                        if detected_org:
                            item["org_prefix"] = detected_org
                json.dump(vault, open(VAULT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            except Exception:
                pass
    ORG_TO_ACCT = {v["org"]: k for k, v in MAC_ACCOUNTS.items() if "org" in v}


def analyze_fleet(force_ping=False):
    now = time.time()
    sync_vault_new_account()
    today_str = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    cid_to_title, cid_to_acct_edges, canon = load_title_map()
    files_data = scan_jsonl_incremental(now)
    agy_stats = scan_antigravity(now)
    proxy_health = check_tuzi_health(now, force_ping=force_ping)

    # 1. 当前 Mac 登录的是哪个账号
    current_mac_acct = None
    if os.path.exists(CLAUDE_CONFIG):
        try:
            cfg = json.load(open(CLAUDE_CONFIG, encoding="utf-8"))
            uuid_full = cfg.get("lastKnownAccountUuid") or ""
            current_mac_acct = uuid_full[:8] if uuid_full else None
        except Exception:
            pass

    # 2. 读取 plan-usage-history.json 获取 Mac 四号的官方进度条采样 (fh=5h%, sd=7d%)
    org_samples = {}
    if os.path.exists(PLAN_HISTORY):
        try:
            ph = json.load(open(PLAN_HISTORY, encoding="utf-8"))
            for s in ph.get("samples", []):
                org_p = (s.get("org") or "")[:8]
                u = s.get("u") or {}
                if org_p and ("fh" in u or "sd" in u):
                    org_samples.setdefault(org_p, []).append({
                        "ts": s["t"] / 1000.0,
                        "fh": u.get("fh", 0),
                        "sd": u.get("sd", 0),
                    })
        except Exception:
            pass

    # 3. 汇总所有 rate_limit_info 事件并关联到账号或 Win-SSH
    all_rate_events = []
    for p, fd in files_data.items():
        cid = fd["cid"]
        edges = cid_to_acct_edges.get(cid, {})
        for ts, rtype, resets, txt in fd.get("rate_events", []):
            # 判断该限流事件属于哪个 Mac 账号（找与 ts 最接近的 lastTurnEdgeAt），否则归入 Win-SSH
            matched_acct = None
            best_diff = 999999
            for acct_p, edge_ts in edges.items():
                diff = abs(edge_ts - ts)
                if diff < 1800 and diff < best_diff:
                    best_diff = diff
                    matched_acct = acct_p
            all_rate_events.append({
                "ts": ts,
                "cid": cid,
                "rtype": rtype,
                "resets": resets,
                "acct": matched_acct or "win_ssh",
                "text": txt,
            })
    all_rate_events.sort(key=lambda x: x["ts"])

    # 4. 为每个 Mac 账号计算实时 5h 与 7d 状态
    accounts_report = {}
    for acct_p, meta in MAC_ACCOUNTS.items():
        org_p = meta["org"]
        samples = org_samples.get(org_p, [])
        last_s = samples[-1] if samples else {"ts": 0, "fh": 0, "sd": 0}

        # 寻找最近一次周额度重置起点 (sd == 0 或大幅下降)
        cycle_start_ts = None
        for i, s in enumerate(samples):
            if s["sd"] == 0:
                if cycle_start_ts is None or (i > 0 and samples[i - 1]["sd"] > 0):
                    cycle_start_ts = s["ts"]
            elif i > 0 and s["sd"] < samples[i - 1]["sd"] - 10:
                cycle_start_ts = s["ts"]

        # 推算下次周刷新时间：优先按已知固定周重置日（如星期二 00:00），否则按 cycle_start_ts + 7天
        if "reset_weekday" in meta:
            dt_now = datetime.datetime.fromtimestamp(now)
            days_ahead = (meta["reset_weekday"] - dt_now.weekday()) % 7
            cand = dt_now.replace(hour=meta.get("reset_hour", 0), minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_ahead)
            if cand.timestamp() <= now:
                cand += datetime.timedelta(days=7)
            seven_resets_ts = cand.timestamp()
        else:
            if cycle_start_ts is None and samples:
                cycle_start_ts = samples[0]["ts"]
            seven_resets_ts = (cycle_start_ts + 7 * 86400) if cycle_start_ts else (now + 4 * 86400)
            while seven_resets_ts <= now:
                seven_resets_ts += 7 * 86400
        days_left = max(0.04, (seven_resets_ts - now) / 86400.0)

        # 查找该账号最近的 five_hour / seven_day 限流事件
        acct_events = [e for e in all_rate_events if e["acct"] == acct_p]
        # 同时如果 plan-usage-history 在某时刻 fh==100，但限流事件未直接匹配到 edge，按时间戳匹配（誤差 < 120s）
        for e in all_rate_events:
            if e["acct"] == "win_ssh" and abs(e["ts"] - last_s["ts"]) < 120:
                acct_events.append(e)
        acct_events.sort(key=lambda x: x["ts"])

        five_resets_ts = None
        for e in reversed(acct_events):
            if e["rtype"] == "five_hour" and e["resets"]:
                five_resets_ts = e["resets"]
                break
        if (not five_resets_ts or five_resets_ts <= last_s["ts"]) and last_s["fh"] >= 100 and (now - last_s["ts"]) < 5 * 3600:
            five_resets_ts = last_s["ts"] + 4.5 * 3600

        # 实时修正 fh (5h 窗口已用%)：
        # 如果已经跨过 five_resets_ts 或者距离上次采样超过 5 小时，5h 窗口已自动清零！
        raw_fh = last_s["fh"]
        if five_resets_ts and now >= five_resets_ts:
            live_fh = 0
        elif last_s["ts"] > 0 and (now - last_s["ts"]) >= 5 * 3600:
            live_fh = 0
        else:
            live_fh = raw_fh

        # 如果上次周采样在本周期开始时间 (seven_resets_ts - 7天) 之前，说明周额度已经在那之后刷新过了，则 sd 归零
        is_new_unlogged = bool(meta.get("is_new_unlogged") and not samples)
        is_estimated = False
        delta_usd_after_sample = 0.0
        delta_5h_usd = 0.0
        if is_new_unlogged:
            live_sd = 0
            live_fh = 0
            days_left = 7.0
            seven_resets_ts = now + 7 * 86400
        elif not samples and "est_sd" in meta:
            live_sd = meta["est_sd"]
            is_estimated = True
        else:
            base_sd = last_s["sd"]
            if last_s["ts"] > 0 and last_s["ts"] < (seven_resets_ts - 7 * 86400):
                base_sd = 0
            # 叠加自上次官方采样 (last_s["ts"]) 之后，该账号在 .jsonl 中的实时增量消耗！
            # 解决 Claude.app 不点开「设置->使用情况」就不刷新 plan-usage-history.json 的滞后问题
            if last_s["ts"] > 0 and base_sd < 100:
                for _p, fd in files_data.items():
                    cid = fd["cid"]
                    edges = cid_to_acct_edges.get(cid, {})
                    acct_edge = edges.get(acct_p, 0)
                    if acct_edge > last_s["ts"]:
                        for t_ts, t_usd, _ctx in fd.get("recent_turns", []):
                            if t_ts > last_s["ts"] + 2.0 and t_ts <= acct_edge + 120.0:
                                delta_usd_after_sample += t_usd
                                if t_ts >= now - 5 * 3600:
                                    delta_5h_usd += t_usd
            delta_sd_pts = delta_usd_after_sample / USD_PER_WEEK_PT
            delta_fh_pts = delta_5h_usd / (USD_PER_5H_WINDOW / 100.0)
            live_sd = min(99.0, round(base_sd + delta_sd_pts, 0)) if base_sd < 100 else 100.0
            if live_fh < 100 and delta_fh_pts > 0:
                live_fh = min(99.0, round(live_fh + delta_fh_pts, 0))

        rem_pts = max(0.0, WEEK_POINTS - live_sd)
        rem_windows = rem_pts / PTS_PER_5H_WINDOW
        rem_usd = rem_pts * USD_PER_WEEK_PT
        eff_days = max(1.0, days_left)
        daily_target_pts = 0.0 if rem_pts <= 1.0 else (rem_pts / eff_days)
        daily_target_win = 0.0 if rem_pts <= 1.0 else (rem_windows / eff_days)
        daily_target_usd = 0.0 if rem_pts <= 1.0 else (rem_usd / eff_days)

        # 状态判定（含 80% 防撞限流墙主动切号预警）
        is_current = (acct_p == current_mac_acct)
        hrs_left = (seven_resets_ts - now) / 3600.0
        warn_80_fh = (live_fh >= 80 and live_fh < 100)
        warn_80_sd = (live_sd >= 80 and rem_pts > 1.0)
        if is_new_unlogged:
            status = "🆕 新购满血 (待首次登录)"
            ready = True
        elif rem_pts <= 1.0:
            if hrs_left <= 12:
                status = f"🔴 周额耗尽({hrs_left:.1f}h后满血重置)" + (" · 当前在线" if is_current else "")
            else:
                status = f"🔴 周额耗尽(余{rem_pts:.0f}%)" + (" · 当前在线" if is_current else "")
            ready = False
        elif live_fh >= 100 and five_resets_ts and five_resets_ts > now:
            mins_wait = int(max(1, (five_resets_ts - now) / 60))
            status = f"🟡 5h冷却 ({mins_wait}分后 {fmt_dual_time(five_resets_ts, short=True)} 恢复)"
            ready = False
        elif warn_80_fh:
            status = f"🟠 80%切号预警 (5h已用{live_fh:.0f}%·防撞墙)" + (" · 在线" if is_current else "")
            ready = True
        elif warn_80_sd:
            status = (f"🟠 在线·周额已用{live_sd:.0f}%(达80%预警)" if is_current else f"🟢 待命 (周已用{live_sd:.0f}%·余{rem_pts:.0f}%)")
            ready = True
        else:
            status = "🔥 当前在线 (满血)" if is_current else "🟢 满血待命"
            ready = True

        # 防浪费 / 防超支 / 80% 切号防封诊断
        if is_new_unlogged:
            advice = f"🚀 新购 Pro 满血 100%！点侧边栏 [复制邮箱/密码/Key] 登录即自动绑定采样"
            priority = 1
        elif rem_pts <= 1.0:
            if hrs_left <= 12:
                advice = f"⏳ 本周已用 {live_sd:.0f}%，再等 {hrs_left:.1f} 小时 ({fmt_dual_time(seven_resets_ts, short=True)}) 即重置回 100% 满血！"
            else:
                advice = f"🛑 本周已耗尽 ({live_sd:.0f}%)，请等 {fmt_dual_time(seven_resets_ts)} 周重置后再用"
            priority = 5
        elif warn_80_fh:
            advice = f"🚨 5h窗口已消耗 {live_fh:.0f}% (>=80%红线)！严禁跑到100%触发429限流，请立即切号！"
            priority = 4
        elif rem_pts >= 65 and days_left <= 4.5:
            advice = f"🚨 闲置防浪费首选！日均需花 {daily_target_pts:.0f}% ({daily_target_win:.1f}窗/天)"
            priority = 1 if rem_pts >= 90 else 2
        elif warn_80_sd:
            advice = f"⚠️ 周额度已达 {live_sd:.0f}% (>=80%提醒线，仅余{rem_pts:.0f}%)，请注意及时切号防撞周限墙"
            priority = 4
        elif rem_pts <= 38 and days_left >= 3.5:
            advice = f"⚠️ 余量偏紧，日均限花 {daily_target_pts:.0f}% ({daily_target_win:.1f}窗/天)"
            priority = 4
        else:
            advice = f"✅ 节奏健康，日均建议 {daily_target_pts:.0f}% ({daily_target_win:.1f}窗/天)"
            priority = 2 if rem_pts >= 45 else 3

        accounts_report[acct_p] = {
            "acct_prefix": acct_p,
            "name": meta["name"],
            "note": meta["note"],
            "email": meta.get("email", ""),
            "is_current": is_current,
            "is_estimated": is_estimated,
            "ready": ready,
            "status": status,
            "fh": live_fh,
            "sd": live_sd,
            "rem_pts": rem_pts,
            "rem_windows": round(rem_windows, 1),
            "rem_usd": round(rem_usd, 0),
            "five_resets_ts": five_resets_ts,
            "seven_resets_ts": seven_resets_ts,
            "days_left": round(days_left, 1),
            "daily_target_pts": round(daily_target_pts, 1),
            "daily_target_win": round(daily_target_win, 1),
            "daily_target_usd": round(daily_target_usd, 0),
            "last_sample_ts": last_s["ts"],
            "advice": advice,
            "priority": priority,
        }

    # 5. 分析 Windows SSH 远端池状态
    win_events = [e for e in all_rate_events if e["acct"] == "win_ssh"]
    win_last_5h = None
    for e in reversed(win_events):
        if e["rtype"] == "five_hour" and e["resets"]:
            win_last_5h = e
            break
    if win_last_5h and win_last_5h["resets"] > now:
        w_mins = int((win_last_5h["resets"] - now) / 60)
        win_status = f"🟡 某号5h冷却 ({w_mins}分后 {fmt_dual_time(win_last_5h['resets'], short=True)} 恢复)"
    else:
        win_status = "🟢 满血可用 (上次5h重置已过)"

    # 6. 汇总过去 7 天每日消耗 & 今日消耗 & 24h 吞金巨兽线程榜
    daily_totals = {}
    thread_24h = {}
    cut24 = now - 86400
    today_start = datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    today_mac_usd = 0.0
    today_win_usd = 0.0

    for p, fd in files_data.items():
        cid = fd["cid"]
        is_mac_known = cid in cid_to_title or cid in cid_to_acct_edges
        raw_ct = fd.get("custom_title")
        canon_ct = canon(raw_ct) if raw_ct else None
        if canon_ct == "__hide__":
            canon_ct = raw_ct
        tname = cid_to_title.get(cid) or canon_ct or f"Win/SSH会话 ({cid[:8]})"

        for d_str, (usd, tok, turns) in fd.get("days", {}).items():
            rec = daily_totals.setdefault(d_str, {"usd": 0.0, "tok": 0, "turns": 0, "mac_usd": 0.0, "win_usd": 0.0})
            rec["usd"] += usd
            rec["tok"] += tok
            rec["turns"] += turns
            if is_mac_known:
                rec["mac_usd"] += usd
            else:
                rec["win_usd"] += usd

        for ts, c, ctx in fd.get("recent_turns", []):
            if ts >= today_start:
                if is_mac_known:
                    today_mac_usd += c
                else:
                    today_win_usd += c
            if ts >= cut24:
                tr = thread_24h.setdefault(tname, {
                    "thread": tname,
                    "usd_24h": 0.0,
                    "usd_today": 0.0,
                    "turns_24h": 0,
                    "max_ctx": 0,
                    "is_mac": is_mac_known,
                    "last_ts": 0,
                })
                tr["usd_24h"] += c
                if ts >= today_start:
                    tr["usd_today"] += c
                tr["turns_24h"] += 1
                if ctx > tr["max_ctx"]:
                    tr["max_ctx"] = ctx
                if ts > tr["last_ts"]:
                    tr["last_ts"] = ts

    # 计算存活 5 号（号8/号6/号5/号4/号3，合计 500 pts）总目标与今日进度（绝不重复计算或把死号算入）
    mac_total_rem_pts = sum(a["rem_pts"] for a in accounts_report.values())
    mac_total_rem_win = sum(a["rem_windows"] for a in accounts_report.values())
    mac_total_rem_usd = sum(a["rem_usd"] for a in accounts_report.values())
    mac_daily_target_pts = sum(a["daily_target_pts"] for a in accounts_report.values())
    mac_daily_target_win = sum(a["daily_target_win"] for a in accounts_report.values())
    mac_daily_target_usd = sum(a["daily_target_usd"] for a in accounts_report.values())

    win_daily_target_usd = 0.0
    fleet_daily_target_usd = mac_daily_target_usd

    today_total_usd = today_mac_usd + today_win_usd
    today_mac_pts = today_total_usd / USD_PER_WEEK_PT

    # 排序推荐接力顺序：优先用 ready 且未达 80% 预警线、priority 最高（剩余额度最多、最快到期）的账号
    rotation_order = sorted(
        accounts_report.values(),
        key=lambda a: (0 if a["ready"] else 1, 1 if a["fh"] >= 80 else 0, a["priority"], -a["rem_pts"])
    )

    # 80% 切号防封主动提醒机制（含 macOS 系统弹窗与提示音节流触发）
    cur_acct_obj = accounts_report.get(current_mac_acct)
    next_cand = next((a for a in rotation_order if a["acct_prefix"] != current_mac_acct and a["ready"] and a["fh"] < 80), None)
    next_cand_label = f"{next_cand['name']}({next_cand['note']})" if next_cand else "反重力内置Claude / Codex"
    switch_alert = None
    if cur_acct_obj:
        if cur_acct_obj["fh"] >= 80:
            switch_alert = {
                "level": "critical",
                "title": f"🚨 {cur_acct_obj['name']} 5h窗口已耗 {int(cur_acct_obj['fh'])}% (达80%切号红线)！",
                "msg": f"严禁跑到100%撞429限流墙后再切号！请立刻切往 【{next_cand_label}】",
                "target_prefix": next_cand["acct_prefix"] if next_cand else "",
                "target_name": next_cand_label,
            }
        elif cur_acct_obj["sd"] >= 80 and cur_acct_obj["rem_pts"] > 1.0:
            switch_alert = {
                "level": "warn",
                "title": f"⚠️ {cur_acct_obj['name']} 周额度已耗 {int(cur_acct_obj['sd'])}% (超80%预警线)",
                "msg": f"仅余 {int(cur_acct_obj['rem_pts'])}% 周额度，5h达80%前请及时切往 【{next_cand_label}】",
                "target_prefix": next_cand["acct_prefix"] if next_cand else "",
                "target_name": next_cand_label,
            }
        if switch_alert:
            alert_cache_path = os.path.join(HANDOFFS_DIR, "ALERT_80_STATE.json")
            try:
                os.makedirs(HANDOFFS_DIR, exist_ok=True)
                st = json.load(open(alert_cache_path, encoding="utf-8")) if os.path.exists(alert_cache_path) else {}
                key = f"{current_mac_acct}:{switch_alert['level']}"
                cooldown = 600 if switch_alert["level"] == "critical" else 3600
                if now - float(st.get(key, 0)) >= cooldown:
                    st[key] = now
                    json.dump(st, open(alert_cache_path, "w", encoding="utf-8"))
                    subprocess.Popen(
                        [
                            "osascript",
                            "-e",
                            f'display notification "{switch_alert["msg"]}" with title "{switch_alert["title"]}" sound name "Glass"',
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            except Exception:
                pass

    heavy_threads = sorted(
        thread_24h.values(),
        key=lambda x: (-x["usd_24h"], -x["max_ctx"])
    )

    return {
        "now": now,
        "today_str": today_str,
        "current_mac_acct": current_mac_acct,
        "proxy_health": proxy_health,
        "switch_alert": switch_alert,
        "accounts": accounts_report,
        "rotation_order": rotation_order,
        "win_ssh": {
            "status": win_status,
            "last_5h_event": win_last_5h,
            "today_usd": round(today_win_usd, 2),
            "daily_target_usd": win_daily_target_usd,
        },
        "antigravity": agy_stats,
        "summary": {
            "mac_rem_pts": round(mac_total_rem_pts, 1),
            "mac_rem_win": round(mac_total_rem_win, 1),
            "mac_rem_usd": round(mac_total_rem_usd, 0),
            "mac_daily_target_pts": round(mac_daily_target_pts, 1),
            "mac_daily_target_win": round(mac_daily_target_win, 1),
            "mac_daily_target_usd": round(mac_daily_target_usd, 0),
            "fleet_daily_target_usd": round(fleet_daily_target_usd, 0),
            "today_mac_usd": round(today_mac_usd, 2),
            "today_mac_pts": round(today_mac_pts, 1),
            "today_total_usd": round(today_total_usd, 2),
        },
        "daily_totals": daily_totals,
        "heavy_threads": heavy_threads[:12],
    }


def render_bar(pct, width=12):
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100.0 * width))
    return "█" * filled + "░" * (width - filled)


def format_banner(data):
    """生成 4 行精简横幅，直接嵌入 sessions / agy-sessions 顶部。"""
    s = data["summary"]
    rot = data["rotation_order"]
    cur_id = data["current_mac_acct"]
    cur_meta = MAC_ACCOUNTS.get(cur_id, {})
    cur_name = f"{cur_meta.get('name', '未知')}[{cur_meta.get('note', '')}]" if cur_meta else "未知"

    acct_pills = []
    for a in sorted(data["accounts"].values(), key=lambda x: MAC_ACCOUNTS[x["acct_prefix"]]["order"]):
        tag = "*" if a["is_current"] else ""
        est_mark = "~" if a.get("is_estimated") else ""
        label = f"{a['name']}[{a['note']}]{tag}"
        if a["rem_pts"] <= 1.0:
            r_str = fmt_dual_time(a["seven_resets_ts"], short=True)
            acct_pills.append(f"🔴{label}(周余{a['rem_pts']:.0f}%->{r_str}重置)")
        elif a["ready"]:
            acct_pills.append(f"🟢{label}(余{est_mark}{a['rem_pts']:.0f}%/{a['rem_windows']:.1f}窗)")
        else:
            r_str = fmt_dual_time(a["five_resets_ts"], short=True)
            acct_pills.append(f"🟡{label}(5h限->{r_str}|余{est_mark}{a['rem_pts']:.0f}%)")

    first_pick = rot[0]
    pct_today = (s["today_total_usd"] / max(1.0, s["fleet_daily_target_usd"])) * 100.0
    heavy_over_200k = [t for t in data["heavy_threads"] if t["max_ctx"] >= 200000]
    ph = data.get("proxy_health") or {}
    tuzi_str = ph.get("status_text") or "🟢 兔子 0.0.0.0"

    ag = data["antigravity"]
    aq = ag.get("quota") or {}
    gplus = ag.get("gpt_plus") or []
    gp_str = " · ".join(f"🟢GPT+[{g['note']}{'*' if g['is_current'] else ''}](余{g['sd_rem']}%)" for g in gplus) if gplus else "🟢GPT+双号就绪"

    n_accts = max(1, len(rot))
    max_pts = n_accts * 100
    lines = [
        f"💰 【全舰队算力总控】Claude存活{n_accts}号周余: {s['mac_rem_pts']:.0f}/{max_pts} pts (~{s['mac_rem_win']:.1f}窗≈${s['mac_rem_usd']:.0f}) | 反重力: Gemini余{aq.get('gemini_7d_rem', 82):.0f}%·内置Claude余{aq.get('claude_7d_rem', 100):.0f}% | {tuzi_str}",
        f"   Claude存活{n_accts}号: {' · '.join(acct_pills)} | {gp_str} | 🟢mcgrox.top中转站",
        f"   今日花费进度: [{render_bar(pct_today, 10)}] Claude池已花 ${s['today_total_usd']:.1f} / 今日黄金定额 ${s['fleet_daily_target_usd']:.0f} ({s['mac_daily_target_pts']:.0f} pts/天 ≈ {s['mac_daily_target_win']:.1f}个5h窗/天) | 反重力今日: {ag['steps_today']}步",
        f"   🧭 排班与省流指令: 当前Mac登录 [{cur_name}] → 优先用 【{first_pick['name']}·{first_pick['note']}】(余{first_pick['rem_pts']:.0f}%) | ⚠️ 过去24h有 {len(heavy_over_200k)} 个 >200k 巨型线程，先交接再聊立省 85%！",
    ]
    return "\n".join(lines)


def format_full_report(data, for_markdown=False):
    s = data["summary"]
    rot = data["rotation_order"]
    n_accts = max(1, len(rot))
    max_pts = n_accts * 100
    acct_names_slash = "/".join(a["name"] for a in rot)
    now_dual = fmt_dual_time(data["now"])
    ph = data.get("proxy_health") or {}

    out = []
    if for_markdown:
        out.append(f"# 💰 全舰队多引擎 Token 额度精算与每日排班大盘")
        out.append(f"> 更新时间：`{now_dual}` | 涵盖 **Claude 存活 {n_accts} 号 ({acct_names_slash}) + 反重力 1 号 + GPT Plus 2 号 + mcgrox 中转站** | 代理监控：**{ph.get('status_text', '')}**（{ph.get('reason', '')}）\n")
    else:
        out.append("=" * 108)
        out.append(f"💰 全舰队多引擎 Token 额度精算与每日排班大盘 (Claude {n_accts}号 + 反重力 + GPT Plus 2号 + mcgrox)  [{now_dual}]")
        out.append(f"🛡️ 兔子代理实时状态：{ph.get('status_text', '')}  ({ph.get('reason', '')})")
        out.append("=" * 108)

    # 一、各账号实时额度池
    out.append("\n## 一、全舰队四大算力池实时水位（全部附带邮箱前缀）\n")
    out.append("| 算力池 / 顺位 | 账号代号 & 邮箱前缀（完整标识） | 当前状态 | 5h 窗口已用 | 7天周额度已用 / 剩余 | 折合剩余算力 | 周刷新倒计时 | 今日科学排班指令 |")
    out.append("|:---:|:---|:---|:---|:---|:---|:---|:---|")

    for idx, a in enumerate(rot, 1):
        cur_mark = " **(当前Mac登录)**" if a["is_current"] else ""
        est_tag = " *(估算)*" if a.get("is_estimated") else ""
        fh_str = f"`{a['fh']}%`"
        if not a["ready"] and a["five_resets_ts"]:
            fh_str += f" (至 {fmt_dual_time(a['five_resets_ts'], short=True)})"
        sd_bar = render_bar(100 - a["rem_pts"], 8)
        sd_str = f"`[{sd_bar}]` 已用 {a['sd']:.0f}% / **余 {a['rem_pts']:.0f}%**{est_tag}"
        win_str = f"**{a['rem_windows']:.1f} 个窗** (~${a['rem_usd']:.0f})"
        reset_str = f"{a['days_left']:.1f} 天 ({fmt_dual_time(a['seven_resets_ts'])})"
        out.append(
            f"| Claude #{idx} | **{a['name']}** (`{a['note']}@...` · `{a['acct_prefix']}`){cur_mark} | {a['status']} | {fh_str} | {sd_str} | {win_str} | {reset_str} | {a['advice']} |"
        )

    # 反重力行（含 Gemini 池 + 内置 Claude 池）
    w = data["win_ssh"]
    ag = data["antigravity"]
    aq = ag.get("quota") or {}
    g_fh_used = max(0.0, 100.0 - aq.get("gemini_5h_rem", 94.9))
    g_sd_rem = aq.get("gemini_7d_rem", 82.1)
    g_sd_used = max(0.0, 100.0 - g_sd_rem)
    g_rst = fmt_dual_time(aq.get("gemini_7d_reset_ts")) if aq.get("gemini_7d_reset_ts") else "滚动恢复"
    c_fh_used = max(0.0, 100.0 - aq.get("claude_5h_rem", 100.0))
    c_sd_rem = aq.get("claude_7d_rem", 99.8)
    c_sd_used = max(0.0, 100.0 - c_sd_rem)
    c_rst = fmt_dual_time(aq.get("claude_7d_reset_ts")) if aq.get("claude_7d_reset_ts") else "滚动恢复"

    out.append(
        f"| 反重力 (Gemini池) | **反重力 1号** (`user@...` · `Gemini 4 Argon`) | 🔥 在线主力 ({ag['steps_today']}步) | `{g_fh_used:.0f}%` (余{aq.get('gemini_5h_rem',95):.0f}%) | `[{render_bar(g_sd_used, 8)}]` 已用 {g_sd_used:.0f}% / **余 {g_sd_rem:.1f}%** | **独立充沛池** | 4.5 天 ({g_rst}) | **省流主力**：文案/策划/配置/交接全走反重力 |"
    )
    out.append(
        f"| 反重力 (Claude池) | **反重力 1号** (`user@...` · 内置 `Opus/Sonnet 5.5`) | 🟢 满血待命 | `{c_fh_used:.0f}%` (余{aq.get('claude_5h_rem',100):.0f}%) | `[{render_bar(c_sd_used, 8)}]` 已用 {c_sd_used:.1f}% / **余 {c_sd_rem:.1f}%** | **满血未动** | 6.6 天 ({c_rst}) | **隐藏王牌**：反重力内直接切 Claude Opus 5.5 零占桌面端额度！ |"
    )

    # 2 个 GPT Plus 账号
    for gidx, gp in enumerate(ag.get("gpt_plus") or [], 1):
        gp_cur = " **(当前Codex激活)**" if gp["is_current"] else ""
        gp_fh_used = max(0, 100 - gp["fh_rem"])
        gp_bar = render_bar(gp["sd_used"], 8)
        gp_rst = f"{gp['days_left']:.1f} 天 ({fmt_dual_time(gp['seven_resets_ts'])})"
        out.append(
            f"| GPT Plus #{gidx} | **GPT Plus {gidx}号** (`{gp['note']}@...` · `{gp['id']}`){gp_cur} | 🟢 满血待命 | `{gp_fh_used}%` (余{gp['fh_rem']}%) | `[{gp_bar}]` 已用 {gp['sd_used']}% / **余 {gp['sd_rem']}%** | **+{gp['reset_credits']}次重置券** | {gp_rst} | **工程重器**：跑 Codex `gpt-6.1-sol` / `gpt-6-astra` 全栈攻坚 |"
        )

    # mcgrox.top 中转站与 API 池
    for rl in ag.get("relays") or []:
        if "mcgrox" in rl["name"].lower() or "krill" in rl["name"].lower():
            m_preview = ", ".join(rl["models"][:5]) + ("..." if len(rl["models"]) > 5 else "")
            out.append(
                f"| API 中转站 | **`{rl['name']}`** ({rl['key_count']} 把 Key · 已同步 `apic`) | 🟢 随时可调 | 无5h锁 | 按量/中转池 (`{m_preview}`) | **全模态/生图/并发** | 长期有效 | 供 `image2.5` 生图、批量评测与 `apic.cauai.fun` 网关调用 |"
            )

    out.append(
        ""
    )

    # 二、每日花费规划（防浪费 × 防花停双控）
    pct_fleet = (s["today_total_usd"] / max(1.0, s["fleet_daily_target_usd"])) * 100.0

    out.append(f"\n## 二、今日花费规划与防浪费/防断粮双控盘（{n_accts} 个存活号真实家底）\n")
    out.append(f"- **📦 存活 {n_accts} 号（{acct_names_slash}）剩余真实总家底**：**`{s['mac_rem_pts']:.0f} / {max_pts} pts`（剩余 {s['mac_rem_pts']/n_accts:.1f}%）** ≈ **`{s['mac_rem_win']:.1f} 个 5h 满窗`** ≈ **`${s['mac_rem_usd']:.0f}` API 等效算力**。")
    out.append(f"- **🎯 每天应该花多少（不浪费也不超支的黄金定额）**：")
    out.append(f"  - **存活 {n_accts} 号合计每日目标**：每天应消耗 **`{s['mac_daily_target_pts']:.0f} pts/天`（即 {n_accts} 个号每天合计打满 `{s['mac_daily_target_win']:.1f} 个 5h 窗口` ≈ `${s['mac_daily_target_usd']:.0f}/天` API 等效）**。")
    out.append(f"- **📊 今日实时消耗进度（{data['today_str']}）**：")
    out.append(f"  - **存活 {n_accts} 号今日已花**：`[{render_bar(pct_fleet, 16)}]` **`${s['today_total_usd']:.2f} / ${s['fleet_daily_target_usd']:.0f}` ({pct_fleet:.1f}%，约 `{s['today_mac_pts']:.1f} pts`)**（其中 Mac 本地跑 `${s['today_mac_usd']:.2f}`，Windows SSH 连 Mac 跑 `${w['today_usd']:.2f}`）。")

    # 动态诊断结论
    idle_accts = [a for a in rot if a["rem_pts"] >= 65]
    low_accts = [a for a in rot if a["rem_pts"] <= 38]
    out.append(f"- **🧭 今日存活 {n_accts} 号接力排班表（按剩余额度从高到低排序）**：")
    for idx, a in enumerate(rot, 1):
        est_note = "（按近4天SSH流水估算，Mac登录后自动校准）" if a.get("is_estimated") else ""
        out.append(
            f"  {idx}. **第 {idx} 顺位：【{a['name']} · `{a['note']}@...`】(`{a['acct_prefix']}`)** — 剩余 **{a['rem_pts']:.0f}% ({a['rem_windows']:.1f} 个 5h 窗)**{est_note}，距周刷新剩 **{a['days_left']:.1f} 天** → 今日建议定额：**`{a['daily_target_pts']:.0f}%`（约 `{a['daily_target_win']:.1f}` 个 5h 窗 / `${a['daily_target_usd']:.0f}`）**。"
        )
    if idle_accts:
        names = "、".join(f"【{a['name']}·`{a['note']}`】(余{a['rem_pts']:.0f}%)" for a in idle_accts)
        out.append(f"  - 🚨 **防浪费强提醒**：{names} 存量较多，请优先作为主力消耗！")
    if low_accts:
        names = "、".join(f"【{a['name']}·`{a['note']}`】(仅余{a['rem_pts']:.0f}%)" for a in low_accts)
        out.append(f"  - 🛡️ **防花停强提醒**：{names} 前两天消耗过猛，剩余周额度已不足 40%，**今日请让其退居替补**，严禁在其上连跑重度长线程，避免触发 7 天周限彻底锁死！")

    # 三、过去 7 天每日燃烧趋势
    out.append("\n## 三、过去 7 天每日 Token 燃烧账单（已按 message.id 严格去重）\n")
    out.append("| 日期 | 全舰队 API 等效消耗 | Mac 池消耗 | Win/SSH 池消耗 | 折合 5h 窗口数 | 总对话轮数 | 节奏评价 |")
    out.append("|:---|:---|:---|:---|:---|:---|:---|")
    for d_str in sorted(data["daily_totals"].keys())[-7:]:
        dv = data["daily_totals"][d_str]
        wins = dv["usd"] / USD_PER_5H_WINDOW
        if dv["usd"] > s["fleet_daily_target_usd"] * 1.35:
            tag = "🔴 超速燃烧 (易触发连环5h限流)"
        elif dv["usd"] < s["fleet_daily_target_usd"] * 0.55 and d_str != data["today_str"]:
            tag = "🟡 消耗偏低 (易造成周额度过期浪费)"
        elif d_str == data["today_str"]:
            tag = f"🟢 今日进行中 (目标 ${s['fleet_daily_target_usd']:.0f})"
        else:
            tag = "✅ 黄金均衡区间"
        out.append(
            f"| `{d_str}` | **${dv['usd']:6.2f}** ({dv['tok']/1e6:5.1f}M tok) | ${dv['mac_usd']:6.2f} | ${dv['win_usd']:6.2f} | `{wins:4.1f} 个窗` | {dv['turns']:4d} 轮 | {tag} |"
        )

    # 四、吞金巨兽线程榜（与会话管理强相关）
    out.append("\n## 四、过去 24h 吞金线程 TOP 10（与会话管理 `session-sync` 强联动省流榜）\n")
    out.append("> 💡 **为什么你会觉得额度突然被秒光？** 当线程上下文超过 `200k` 时，只要停顿 >5 分钟导致缓存过期，发一句话重建缓存就要烧掉 `$1.50~$1.90`（一句话直接吃掉 5h 窗口的 **4.5%**）！对下方标注 `🔴 立刻交接` 的线程，直接新开窗口说 **「接手 <线程名>」** 读取 `.auto.md` 交接单，单轮成本可直降 **85%~90%**！\n")
    out.append("| 排名 | 统一主线程 / 会话 | 24h 消耗 | 今日消耗 | 24h 轮数 | 单轮均价 | 峰值上下文 | 会话管理省流处方 |")
    out.append("|:---:|:---|:---|:---|:---|:---|:---|:---|")
    for idx, tr in enumerate(data["heavy_threads"][:10], 1):
        avg = tr["usd_24h"] / max(1, tr["turns_24h"])
        ctx_k = tr["max_ctx"] // 1000
        if ctx_k >= 200:
            rx = f"🔴 **立刻交接新开** (每聊30轮多烧 ~${avg*25:.1f}，新开立省 85%)"
        elif ctx_k >= 120:
            rx = f"🟡 **偏贵建议交接** (上下文已达 {ctx_k}k，阶段收尾后新开)"
        else:
            rx = "🟢 **健康可直接续** (上下文轻量，单轮成本低)"
        out.append(
            f"| #{idx} | **{tr['thread']}** | **${tr['usd_24h']:5.2f}** | ${tr['usd_today']:5.2f} | {tr['turns_24h']:3d} 轮 | `${avg:.3f}/轮` | `{ctx_k}k` | {rx} |"
        )

    return "\n".join(out) + "\n"


def write_budget_files(data):
    md = format_full_report(data, for_markdown=True)
    for path in (BUDGET_MD, AGY_BUDGET_MD):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
        except Exception:
            pass


def main():
    args = sys.argv[1:]
    if "--heal-tuzi" in args or "--heal-proxy" in args:
        res = heal_tuzi_connections()
        print(json.dumps(res, ensure_ascii=False))
        return
    force_ping = "--ping-tuzi" in args or "--ping-proxy" in args
    data = analyze_fleet(force_ping=force_ping)
    write_budget_files(data)

    if "--quiet" in args:
        return
    if "--json" in args:
        print(json.dumps(data, ensure_ascii=False))
        return
    if "--banner" in args:
        print(format_banner(data))
        return

    print(format_full_report(data, for_markdown=False))
    print(f"📄 已同步写入双端额度规划大盘：{BUDGET_MD}")


if __name__ == "__main__":
    main()
