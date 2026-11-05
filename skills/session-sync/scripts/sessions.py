#!/usr/bin/env python3
"""桌面 app 会话管理（Claude Desktop × 反重力 Antigravity 双端 1:1 统一共研版）：
去重、同步各号侧边栏、跨 App 1:1 侧边栏镜像注入、双端共用零 token 交接单。全程不调用模型，不花额度。

用法：
  sessions                  看现状：双端 38 个统一主线程的续写成本、跨端协同状态、各号计划改动（不改任何东西）
  sessions --apply          执行 Claude 四号同步 + 反重力 1:1 镜像注入 + 刷新双端合并交接单（先备份）
  sessions brief <标题片段>  零 token 生成/刷新双端合并交接摘要 ~/.claude/session-handoffs/<线程>.auto.md
  sessions brief-all        批量刷新全部活跃线程的双端合并交接摘要
  sessions keep <标题片段>   以后一直保留这个线程（不因过期被隐藏）
  sessions hide <标题片段>   以后一直隐藏这个线程
改完要退出并重开 Claude app / Antigravity app 才生效。
"""
import calendar, copy, datetime, glob, json, os, re, shutil, subprocess, sys, time, uuid


def sessions_root():
    if sys.platform == "win32":
        cands = []
        for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands.append(os.path.join(base, "Claude", "claude-code-sessions"))
        local = os.environ.get("LOCALAPPDATA") or ""
        cands += glob.glob(os.path.join(local, "Claude-Data", "*", "claude-code-sessions"))
        cands += glob.glob(os.path.join(local, "Claude-3p", "claude-code-sessions"))
        cands += glob.glob(os.path.join(local, "Packages", "*Claude*", "LocalCache", "Roaming", "Claude", "claude-code-sessions"))
        live = [c for c in cands if glob.glob(os.path.join(c, "*", "*", "local_*.json"))]
        if live:
            return max(live, key=lambda c: max(os.path.getmtime(f) for f in glob.glob(os.path.join(c, "*", "*", "local_*.json"))))
        return next((c for c in cands if os.path.isdir(c)), cands[0] if cands else "")
    return os.path.expanduser("~/Library/Application Support/Claude/claude-code-sessions")


ROOT = sessions_root()
PROJECTS = os.path.expanduser("~/.claude/projects")
RULES = os.path.expanduser("~/.claude/session-groups.json")
HANDOFFS = os.path.expanduser("~/.claude/session-handoffs")
AGY_HANDOFFS = os.path.expanduser("~/.gemini/antigravity/session-handoffs")
CLAUDE_MANIFEST_PATH = os.path.join(HANDOFFS, "CLAUDE_MANIFEST.json")
AGY_MANIFEST_PATH = os.path.join(AGY_HANDOFFS, "AGY_MANIFEST.json")

REMOTE_CFG = os.path.expanduser("~/.claude/sessions-remote.json")
REMOTE = json.load(open(REMOTE_CFG, encoding="utf-8")) if os.path.exists(REMOTE_CFG) else None
REMOTE_RUNNING = set()
MIRROR = {}
AUTO_BRIEF = False
def _load_account_names():
    p = os.path.expanduser("~/.claude/accounts-vault.json")
    if os.path.exists(p):
        try:
            v = json.load(open(p, encoding="utf-8"))
            return {a["acct_prefix"]: f'{a.get("name","号")}[{a.get("note","")}]' for a in v.get("accounts", []) if a.get("acct_prefix")}
        except Exception:
            pass
    return {}
ACCOUNT_NAMES = _load_account_names()
BANNED_PREFIXES = set()
ENV_FIELDS = ("envScopeId", "startedFromEnvironmentId", "remoteMcpServersConfig")
DROP_FIELDS = (
    "postTurnSummary", "postTurnSummaryFor", "lastAssistantUuid", "turnWrapUp", "promptAppendSnapshot",
    "toolSurfaceSnapshot", "latestUserFrameAt", "lastTurnEdgeAt", "lastFocusedAt", "interruptedAwaitingPerson",
)
CHEAP_K, PRICEY_K = 80, 180
V3_CUTOFF_MS = 1793788000000  # 2026-11-04 04:26 之后创建的带 fleetOwnerAccount 标记的单号原生会话才视为未跨号污染


def mirror_cli_for(g, acc_prefix="global"):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claude-mirror-cli-v3:{acc_prefix}:{g}"))


def acct_handoff_cli_for(acc_prefix, g, epoch_tag="seed"):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claude-acct-handoff-v3:{acc_prefix}:{g}:{epoch_tag}"))


def cwd_to_project_slug(cwd):
    return re.sub(r"[^a-zA-Z0-9-]", "-", cwd or os.path.expanduser("~"))


def SafeFilename(name):
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip()


def is_seed_jsonl(path):
    """判断一个 .jsonl 是否仍处于未发新消息的『交接种子/镜像种子』状态（即使被 Claude Desktop 预热写入了 atis-latch/mode/cost-state）。"""
    if not path or not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) > 256 * 1024:
            return False
        user_msgs = []
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"type": "user"' in line or '"type":"user"' in line:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("type") == "user" and not d.get("isSidechain"):
                        c = (d.get("message") or {}).get("content")
                        txt = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                        user_msgs.append(txt)
                        if len(user_msgs) > 1:
                            return False
        if len(user_msgs) == 1 and (
            "【双端共研同步" in user_msgs[0] or "【跨号防封安全交接" in user_msgs[0]
        ):
            return True
    except Exception:
        pass
    return False


def jsonl_taint_reason(path, k=None):
    """检测一个原生会话是否触发了限流(5h/7d)、401/403 认证错误、或上下文已超过安全红线 PRICEY_K (180k)。"""
    if not path or not os.path.exists(path):
        return None
    if k is None:
        k = context_k(path)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 64 * 1024))
            tail = f.read().decode("utf-8", errors="ignore")
        for l in reversed(tail.splitlines()[-15:]):
            low = l.lower()
            if "oauth access token has been revoked" in low or "account_banned" in low or "authentication_error" in low:
                return "401污染熔断"
            if "hit your 5-hour limit" in low or "hit your 7-day limit" in low or "rate_limit_error" in low or "you've hit your" in low:
                return "5h限流熔断"
    except Exception:
        pass
    if (k or 0) > PRICEY_K:
        return f"超{PRICEY_K}k熔断({k}k)"
    return None


def ensure_acct_handoff_jsonl(acc_prefix, g, cwd, mtime, epoch_tag="seed", is_agy_only=False):
    """为指定账号 (acc_prefix) 与线程 (g) 生成/刷新专属隔离的 ~2k 交接种子 .jsonl，彻底阻断跨账号共用同一 cliSessionId！"""
    cli_id = mirror_cli_for(g, acc_prefix) if is_agy_only else acct_handoff_cli_for(acc_prefix, g, epoch_tag)
    cwd = cwd or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    slug = cwd_to_project_slug(cwd)
    pdir = os.path.join(PROJECTS, slug)
    os.makedirs(pdir, exist_ok=True)
    jpath = os.path.join(pdir, f"{cli_id}.jsonl")

    # 如果用户已经在该账号的这个种子会话里发送过真实新消息（不再是纯种子），绝不覆盖！
    if os.path.exists(jpath) and not is_seed_jsonl(jpath):
        return cli_id, jpath, False

    safe_g = SafeFilename(g)
    h_path = os.path.join(HANDOFFS, f"{safe_g}.auto.md")
    if not os.path.exists(h_path):
        h_path = os.path.join(AGY_HANDOFFS, f"{safe_g}.auto.md")
    handoff_text = (
        open(h_path, encoding="utf-8", errors="ignore").read()
        if os.path.exists(h_path)
        else f"# 双端共研交接单：{g}\n已建立账号专属安全交接会话，可直接在此续写。"
    )

    # 若用户在种子文件里已经继续聊过（>6行），绝不覆盖！若仍是 6 行初始种子但交接单内容已升级，则自动刷新种子内容
    if os.path.exists(jpath):
        try:
            existing_lines = open(jpath, encoding="utf-8", errors="ignore").read().splitlines()
            if len(existing_lines) > 6:
                return cli_id, jpath, False
            if len(existing_lines) == 6 and handoff_text[:180] in existing_lines[3]:
                return cli_id, jpath, False
        except Exception:
            pass

    iso_ts = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.100Z")
    seed_key = f"{acc_prefix}:{g}:{epoch_tag}"
    user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claude-seed-user:{seed_key}"))
    asst_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claude-seed-asst:{seed_key}"))
    prompt_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claude-seed-prompt:{seed_key}"))
    if is_agy_only:
        user_prompt = f"【双端共研同步】请加载反重力 (Antigravity) 端「{g}」的最新交接摘要并准备在 Claude 端共同开发。"
    else:
        user_prompt = (
            f"【跨号防封安全交接 · 三大开源引擎防跑偏压缩版】已为您自动隔离原高危/跨号会话上下文（严防多账号共用同一 SessionID 触发风控封号，并融合 davila7/session-handoff ★19.3k + affaan-m/strategic-compact ★38.8k + REMvisual/claude-handoff 深度上下文挖掘协议自动省流 90%）。"
            f"以下是「{g}」的完整高保真交接单（.auto.md），请直接在此窗口继续输入下一条指令："
        )
    resume_guardrail = (
        "\n\n---\n"
        "## 🛡️ 接手防跑偏四步铁律（Resume Verification Protocol — 源自 `davila7/session-handoff` × `REMvisual/claude-handoff`）\n"
        "1. **锚定北极星与红线（Zero-Drift Goal & Guardrails）**：动工前必须先核对上方「🎯 原始立项北极星」与「🧭 用户纠偏与偏好红线」，严禁偏离既定架构方向或违背用户已口头纠正的偏好。\n"
        "2. **以磁盘与 Git 物理真源为准（Ground Truth First）**：相信上方「🧬 Git 与工作区实时物理真源」及「🛠️ 会话真实修改文件账本」，修改前先 `Read` 对应文件的最新代码，严禁凭脑补覆盖已有改动。\n"
        "3. **严禁重复踩坑（No Re-trying Failed Approaches）**：上方「⚠️ 已踩坑与报错记录」中列出的失败路径已排除，直接沿最新成功路径继续推进。\n"
        "4. **按需深潜历史原文（On-Demand Raw Digest）**：若需核查旧会话某次具体函数 diff 或长日志，直接运行交接单顶部的 `jsonl-digest.py` / `agy-digest.py` 命令按需调阅。"
    )
    if "接手防跑偏四步铁律" not in handoff_text:
        handoff_text = handoff_text.rstrip() + resume_guardrail + "\n"
    est_tok = max(1000, len(handoff_text.encode("utf-8")) // 2)

    records = [
        {"type": "queue-operation", "operation": "enqueue", "timestamp": iso_ts, "sessionId": cli_id, "content": user_prompt},
        {"type": "queue-operation", "operation": "dequeue", "timestamp": iso_ts, "sessionId": cli_id},
        {
            "parentUuid": None,
            "isSidechain": False,
            "promptId": prompt_id,
            "type": "user",
            "message": {"role": "user", "content": user_prompt},
            "uuid": user_uuid,
            "timestamp": iso_ts,
            "permissionMode": "auto",
            "promptSource": "sdk",
            "turnOrigin": "sdk",
            "turnPosition": {"promptIndex": 0, "turnIndex": 1},
            "userType": "external",
            "entrypoint": "sdk-cli",
            "cwd": cwd,
            "sessionId": cli_id,
            "version": "2.1.317",
            "gitBranch": "HEAD",
        },
        {
            "parentUuid": user_uuid,
            "isSidechain": False,
            "type": "assistant",
            "uuid": asst_uuid,
            "timestamp": iso_ts,
            "cwd": cwd,
            "sessionId": cli_id,
            "version": "2.1.317",
            "gitBranch": "HEAD",
            "message": {
                "model": "claude-opus-5-5",
                "id": f"msg_{cli_id[:16].replace('-', '')}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": handoff_text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": est_tok,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 500,
                },
            },
        },
        {"type": "last-prompt", "lastPrompt": user_prompt, "leafUuid": asst_uuid, "sessionId": cli_id},
        {
            "type": "cost-state",
            "sessionId": cli_id,
            "totalCostUSD": 0,
            "totalAPIDuration": 500,
            "totalAPIDurationWithoutRetries": 500,
            "totalToolDuration": 0,
            "totalLinesAdded": 0,
            "totalLinesRemoved": 0,
            "totalDuration": 500,
            "startTime": int(mtime * 1000),
            "modelUsage": {},
            "hasUnknownModelCost": False,
        },
    ]
    with open(jpath, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.utime(jpath, (mtime, mtime))
    return cli_id, jpath, True


def ensure_agy_mirror_jsonl(g, cwd, mtime, acc_prefix="global"):
    """兼容旧调用入口：生成账号隔离的 AGY 镜像 .jsonl。"""
    _, jpath, created = ensure_acct_handoff_jsonl(acc_prefix, g, cwd, mtime, epoch_tag="agy", is_agy_only=True)
    return jpath, created


def mac(cmd, data=None):
    remote = "/usr/bin/python3 ~/.claude/scripts/sessions.py " + cmd
    env = None
    if REMOTE["mac_host"] == "local":
        argv = ["sh", "-c", remote]
        env = {**os.environ, "HOME": os.environ.get("SESSIONS_MAC_HOME", os.environ["HOME"])}
    else:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if REMOTE.get("ssh_key"):
            argv += ["-i", REMOTE["ssh_key"]]
        argv += [REMOTE["mac_host"], remote]
    r = subprocess.run(argv, input=data, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180,
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
    if r.returncode:
        raise RuntimeError("连不上 Mac：" + (r.stderr or r.stdout).strip()[-300:])
    return r.stdout


def load_rules():
    rules = {"merge": {}, "keep": [], "hide": [], "active_days": 1.5}
    if REMOTE:
        try:
            text = mac("rules")
            open(RULES, "w", encoding="utf-8").write(text)
        except Exception:
            pass
    if os.path.exists(RULES):
        rules.update(json.load(open(RULES, encoding="utf-8")))
    return rules


def save_rules(rules):
    json.dump(rules, open(RULES, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def group_name(title, rules):
    t = (title or "").strip()
    cleaned = re.sub(r"^\s*续写\s*[:：·]\s*", "", t).strip()
    merge_map = rules.get("merge", {})
    if cleaned in merge_map:
        return merge_map[cleaned]
    if t in merge_map:
        return merge_map[t]
    for piece, name in merge_map.items():
        if piece and (piece in cleaned or piece in t):
            return name
    return cleaned or "(无标题)"


def jsonl_info(cli_id, g=None):
    files = glob.glob(f"{PROJECTS}/*/{cli_id}.jsonl") if cli_id else []
    if not files:
        return None
    f = files[0]
    is_mirror = is_seed_jsonl(f)
    return {"path": f, "mtime": os.path.getmtime(f), "size": os.path.getsize(f), "is_mirror": is_mirror}


def entry_mtime(e, info):
    if info:
        return info["mtime"]
    return (e.get("lastActivityAt") or e.get("createdAt") or 0) / 1000


def context_k(path):
    size = os.path.getsize(path)
    with open(path, "rb") as h:
        h.seek(size - min(size, 4 * 1024 * 1024))
        lines = h.read().split(b"\n")
    for line in reversed(lines):
        if b'"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        msg = d.get("message") or {}
        u = msg.get("usage")
        if not u or d.get("isSidechain") or msg.get("model") == "<synthetic>":
            continue
        total = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        if total:
            return total // 1000
    return None


def cost_tag(k):
    if k is None:
        return "?"
    if k <= CHEAP_K:
        return "直接续"
    if k <= PRICEY_K:
        return "可续·偏贵"
    return "先交接"


def info_k(info):
    if not info:
        return None
    return info["k"] if "k" in info else context_k(info["path"])


def running_ids():
    if REMOTE:
        return REMOTE_RUNNING
    out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    return set(re.findall(r"(?:--resume|--session-id)[ =]([0-9a-f-]{36})", out))


def load_agy_manifest():
    if os.path.exists(AGY_MANIFEST_PATH):
        try:
            return json.load(open(AGY_MANIFEST_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_claude_manifest(report):
    os.makedirs(HANDOFFS, exist_ok=True)
    manifest = {}
    for item in report:
        g, cli, mt, k, active, _, is_mirror, cwd = item[:8]
        if not active or is_mirror:
            continue
        manifest[g] = {
            "cli": cli,
            "mtime": mt,
            "k": k,
            "cwd": cwd or os.path.expanduser("~"),
        }
    with open(CLAUDE_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def scan(rules):
    accounts = {}
    for d in glob.glob(os.path.join(ROOT, "*", "*", "")):
        acc = os.path.basename(os.path.dirname(os.path.normpath(d)))
        if acc[:8] in BANNED_PREFIXES:
            continue
        files = glob.glob(os.path.join(d, "local_*.json"))
        has_sched = os.path.exists(os.path.join(d, "scheduled-tasks.json"))
        if not files and not has_sched:
            continue
        entries = []
        for f in files:
            try:
                e = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            e["_file"] = f
            entries.append(e)
        prev = accounts.get(acc)
        if not prev or len(entries) > len(prev["entries"]):
            accounts[acc] = {"dir": d, "entries": entries}
    if REMOTE and len(accounts) > 1:
        act = max(accounts, key=lambda k: max((e.get("lastActivityAt") or 0) for e in accounts[k]["entries"]))
        accounts = {act: accounts[act]}
    groups = {}
    for acc, a in accounts.items():
        for e in a["entries"]:
            title = e.get("title") or ""
            if any(h in title for h in rules["hide"]):
                g = "__hide__"
            else:
                g = group_name(title, rules)
                if any(h == g for h in rules["hide"]):
                    g = "__hide__"
            info = None if REMOTE else jsonl_info(e.get("cliSessionId", ""), g=g)
            groups.setdefault(g, []).append((acc, e, info))
    if REMOTE:
        groups = ask_mac(groups)
    return accounts, groups


def recap_of(e):
    r = e.get("postTurnSummary")
    if isinstance(r, dict):
        r = r.get("recap") or r.get("text") or json.dumps(r, ensure_ascii=False)
    return r


def ask_mac(groups):
    req = {"auto_brief": AUTO_BRIEF, "mirror": True, "groups": {g: [{"cli": e.get("cliSessionId"), "cwd": e.get("cwd"), "recap": recap_of(e)}
                                                 for _, e, _ in members] for g, members in groups.items() if g != "__hide__"}}
    try:
        res = json.loads(mac("serve", json.dumps(req)))
    except Exception as err:
        print(f"{err}\n只按侧边栏时间去重，不新增条目。")
        return groups
    REMOTE_RUNNING.update(res["running"])
    MIRROR.update(res.get("mirror") or {})
    if res.get("token_banner"):
        print(res["token_banner"] + "\n")
    for g, dst in res["briefed"].items():
        print(f"已写 {dst}（Mac 上）")
    return {g: [(acc, e, res["info"].get(e.get("cliSessionId"))) for acc, e, _ in members] for g, members in groups.items()}


def serve():
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    # Windows 请求同步时，先将 Mac 中枢上的反重力与 Claude 四号同步到最新态，确保返回给 Windows 的 38 条线程与交接单始终权威最新
    if req.get("mirror"):
        agy_script = os.path.expanduser("~/.gemini/antigravity/scripts/agy-sessions.py")
        if os.path.exists(agy_script):
            try:
                subprocess.run([sys.executable, agy_script, "auto"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            except Exception:
                pass
        try:
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                brief_all(quiet=True)
                sync(True)
        except Exception:
            pass
    rules = load_rules()
    cutoff = time.time() - rules["active_days"] * 86400
    info, briefed = {}, {}
    for g, items in req["groups"].items():
        found = [(it, jsonl_info(it["cli"], g=g)) for it in items]
        found = [(it, i) for it, i in found if i]
        if not found:
            continue
        it, latest = max(found, key=lambda x: (not x[1].get("is_mirror"), x[1]["mtime"]))
        latest["k"] = context_k(latest["path"])
        for it2, i in found:
            info[it2["cli"]] = {"mtime": i["mtime"], "k": latest["k"] if it2["cli"] == it["cli"] else None, "is_mirror": i.get("is_mirror", False)}
        if not latest.get("is_mirror") and (
            g in req.get("force", []) or (req.get("auto_brief") and latest["mtime"] >= cutoff and (latest["k"] or 0) > CHEAP_K
                                          and not fresh_brief(g, latest["mtime"]))
        ):
            recap = next((x["recap"] for x in items if x.get("recap")), None)
            briefed[g] = write_brief(g, latest["path"], it.get("cwd") or "", recap, quiet=True)
    mirror = {}
    if req.get("mirror"):
        _, mg = scan(rules)
        for g, members in mg.items():
            if g == "__hide__":
                continue
            latest = pick_latest(members)
            if not latest[2] or not (latest[2]["mtime"] >= cutoff or any(k in g for k in rules["keep"])):
                continue
            m = {k: latest[1].get(k) for k in ("cliSessionId", "cwd", "originCwd", "createdAt", "model", "effort", "permissionMode")}
            mirror[g] = dict(m, path=latest[2]["path"], mtime=latest[2]["mtime"])
    tok_banner = ""
    tok_script = os.path.expanduser("~/.claude/scripts/tokens.py")
    if os.path.exists(tok_script):
        try:
            r = subprocess.run([sys.executable, tok_script, "--banner"], capture_output=True, text=True, timeout=15)
            tok_banner = r.stdout.strip()
        except Exception:
            pass
    sys.stdout.write(json.dumps({"info": info, "running": sorted(running_ids()), "briefed": briefed, "mirror": mirror, "token_banner": tok_banner}))


def fresh_brief(g, mtime):
    dst = os.path.join(HANDOFFS, f"{SafeFilename(g)}.auto.md")
    return os.path.exists(dst) and os.path.getmtime(dst) >= mtime


def pick_latest(members):
    return max(members, key=lambda m: (m[2] is not None and not m[2].get("is_mirror"), m[2] is not None, entry_mtime(m[1], m[2])))


def acc_name(acc):
    ap = acc[:8]
    if ap in ACCOUNT_NAMES:
        return ACCOUNT_NAMES[ap]
    vp = os.path.expanduser("~/.claude/accounts-vault.json")
    if os.path.exists(vp):
        try:
            v = json.load(open(vp, encoding="utf-8"))
            for it in v.get("accounts", []):
                if it.get("acct_prefix") == ap:
                    return f"{it.get('name', '号')}[{it.get('note', ap[:4])}]"
                if it.get("acct_prefix") == "pending_hao8":
                    return f"号8[acct4·{ap[:4]}]"
        except Exception:
            pass
    return f"账号[{ap[:6]}]"


def write_entry(e, **changes):
    d = {k: v for k, v in e.items() if k != "_file"}
    d.update(changes)
    json.dump(d, open(e["_file"], "w", encoding="utf-8"), ensure_ascii=False)


def sync(apply):
    rules = load_rules()
    accounts, groups = scan(rules)
    agy_manifest = {} if REMOTE else load_agy_manifest()
    running = running_ids()
    cutoff = time.time() - rules["active_days"] * 86400

    # 父会话防连坐保护
    protected_sids = set()
    for a in accounts.values():
        for e in a["entries"]:
            if not e.get("isArchived"):
                psid = (e.get("spawnedFrom") or {}).get("sessionId")
                if psid:
                    protected_sids.add(psid)

    plan, report = [], []
    for g, members in groups.items():
        if g == "__hide__":
            for acc, e, _ in members:
                if not e.get("isArchived") and e.get("cliSessionId") not in running and e.get("sessionId") not in protected_sids:
                    plan.append((acc, "archive", e))
            continue
        latest = pick_latest(members)
        cli = latest[1].get("cliSessionId") or ""
        is_mirror = bool(latest[2] and latest[2].get("is_mirror"))
        mt = entry_mtime(latest[1], latest[2])
        cwd = latest[1].get("cwd") or os.path.expanduser("~")
        if is_mirror and g in agy_manifest:
            mt = float(agy_manifest[g].get("mtime") or mt)
            cwd = agy_manifest[g].get("cwd") or cwd
        keep_rule = any(k in g for k in rules["keep"]) or any(m[1].get("sessionId") in protected_sids and not m[1].get("isArchived") for m in members)
        active = mt >= cutoff or keep_rule or cli in running
        raw_k = info_k(latest[2])

        # v3.0 防封保号核心：识别该原生 cliSessionId 是否属于单一干净账号，以及是否触发了 5h限流/401/超180k 熔断
        owner_acc = None
        taint_reason = None
        if not REMOTE and latest[2] and not is_mirror:
            holders = [m for m in members if m[1].get("cliSessionId") == cli and not m[1].get("isArchived")]
            holder_accs = {m[0][:8] for m in holders}
            if len(holder_accs) == 1:
                cand = next(iter(holder_accs))
                if any(m[1].get("fleetOwnerAccount") == cand for m in holders) or (latest[1].get("createdAt") or 0) >= V3_CUTOFF_MS:
                    owner_acc = cand
            taint_reason = jsonl_taint_reason(latest[2]["path"], raw_k)
            if active and (owner_acc is None or taint_reason or len(accounts) > 1) and not fresh_brief(g, mt):
                recap = next((recap_of(m[1]) for m in members if recap_of(m[1])), None)
                write_brief(g, latest[2]["path"], cwd, recap, quiet=True)

        any_kept_raw = False
        for acc, a in accounts.items():
            ap = acc[:8]
            mine = [m for m in members if m[0] == acc]
            if REMOTE:
                target_cli = cli
                can_keep_raw = True
            else:
                can_keep_raw = (not is_mirror) and (ap == owner_acc) and (not taint_reason or cli in running)
                if can_keep_raw:
                    target_cli = cli
                    any_kept_raw = True
                else:
                    is_agy_only = bool(is_mirror and g in agy_manifest and not latest[2])
                    seed_epoch = "agy" if is_agy_only else (cli if not is_mirror else "seed")
                    target_cli = mirror_cli_for(g, ap) if is_agy_only else acct_handoff_cli_for(ap, g, seed_epoch)
                    if active and apply:
                        ensure_acct_handoff_jsonl(ap, g, cwd, mt, epoch_tag=seed_epoch, is_agy_only=is_agy_only)

            keep = next((m for m in mine if m[1].get("cliSessionId") == target_cli), None) if active else None
            for _, e, _ in mine:
                if (keep is None or e is not keep[1]) and not e.get("isArchived") and e.get("cliSessionId") not in running and e.get("sessionId") not in protected_sids:
                    plan.append((acc, "archive", e))
            if keep:
                if keep[1].get("isArchived") or keep[1].get("title") != g or keep[1].get("fleetOwnerAccount") != ap:
                    plan.append((acc, "show", keep[1], g, ap))
            elif active and (latest[2] or (is_mirror and g in agy_manifest)):
                src = dict(latest[1])
                src["cliSessionId"] = target_cli
                src["cwd"] = cwd
                src["fleetOwnerAccount"] = ap
                plan.append((acc, "add", src, g, mt, ap))

        eff_k = raw_k if any_kept_raw else 2
        if REMOTE:
            status_note = cost_tag(raw_k)
        elif any_kept_raw:
            status_note = f"🟢{acc_name(owner_acc)[:2]}独享 / 他号2k隔离"
        elif taint_reason:
            status_note = f"🛡️2k已隔离({taint_reason})"
        elif not is_mirror and raw_k and raw_k > 2:
            status_note = f"🛡️2k已隔离(原{raw_k}k)"
        else:
            status_note = "🛡️2k独立种子"
        report.append((g, cli or "", mt, eff_k, active, keep_rule, is_mirror, cwd, status_note))

    # 检查反重力端有、而 Claude 端还没有的线程 -> 在全部 4 个本地账号侧边栏中 1:1 账号隔离镜像注入
    if not REMOTE and agy_manifest and accounts:
        for ag, ainfo in agy_manifest.items():
            if ag in groups or any(h in ag or h == ag for h in rules["hide"]):
                continue
            mt = float(ainfo.get("mtime") or time.time())
            cwd = ainfo.get("cwd") or os.path.expanduser("~")
            if not os.path.isdir(cwd):
                cwd = os.path.expanduser("~")
            for acc in accounts:
                ap = acc[:8]
                mcli = mirror_cli_for(ag, ap)
                if apply:
                    ensure_acct_handoff_jsonl(ap, ag, cwd, mt, epoch_tag="agy", is_agy_only=True)
                src = {
                    "cliSessionId": mcli,
                    "cwd": cwd,
                    "originCwd": cwd,
                    "gitAnchorsFolderRealpath": cwd,
                    "createdAt": int(mt * 1000),
                    "model": "claude-opus-5-5",
                    "effort": "medium",
                    "permissionMode": "auto",
                    "fleetOwnerAccount": ap,
                }
                plan.append((acc, "add", src, ag, mt, ap))
            report.append((ag, mirror_cli_for(ag, "global"), mt, 2, True, True, True, cwd, "🛡️2k独立种子"))

    # Windows 远程镜像补齐
    if REMOTE and MIRROR and accounts:
        acc0 = max(accounts, key=lambda a: max((e.get("lastActivityAt") or 0) for e in accounts[a]["entries"]))
        for g, m in MIRROR.items():
            if g in groups or any(k in g for k in rules["hide"]):
                continue
            src = {k: v for k, v in m.items() if k not in ("path", "mtime") and v is not None}
            src["sshRemoteTranscriptPath"] = m["path"]
            plan.append((acc0, "add", src, g, m["mtime"], acc0[:8]))
            report.append((g, m["cliSessionId"] or "", m["mtime"], None, True, False, False, m.get("cwd") or "", "直接续"))

    if not REMOTE:
        save_claude_manifest(report)
        tok_script = os.path.expanduser("~/.claude/scripts/tokens.py")
        if os.path.exists(tok_script):
            try:
                r = subprocess.run([sys.executable, tok_script, "--banner"], capture_output=True, text=True, timeout=15)
                if r.stdout.strip():
                    print(r.stdout.strip() + "\n")
            except Exception:
                pass

    report.sort(key=lambda r: -r[2])
    print(f"{'双端统一主线程 (领域 · 主题)':<28} {'双端状态':<12} {'更新时间':<12} {'当前窗口':>7}  {'跨号防封与省流状态':<20}")
    print("-" * 88)
    for item in report:
        g, cli, mt, k, active, keep_rule, is_mirror, _, status_note = item
        if not active:
            continue
        ks = f"{k}k" if k is not None else "?"
        if is_mirror:
            role = "AGY镜像"
        elif g in agy_manifest:
            role = "⚡双端共研"
        else:
            role = "Claude主控"
        print(f"{g[:27]:<28} {role:<12} {time.strftime('%m-%d %H:%M', time.localtime(mt)):<12} {ks:>7}  {status_note}")
    stale = [r[0] for r in report if not r[4]]
    if stale:
        print(f"\n过期隐藏（超过 {rules['active_days']} 天没动，{len(stale)} 个）：" + "、".join(stale[:12]) + (" …" if len(stale) > 12 else ""))
        print("  要留哪个：sessions keep <标题片段>")
    counts = {}
    for p in plan:
        key = (acc_name(p[0]), p[1])
        counts[key] = counts.get(key, 0) + 1
    names = dict(add="隔离交接/新增", archive="隐藏旧高危窗", show="恢复/加固")
    print("\n计划：", "，".join(f"{a} {names[act]} {n}" for (a, act), n in sorted(counts.items())) or "无需改动 (全部账号已完成独立 SessionID 隔离)")
    if not apply:
        print("（只看不改。确认后跑 sessions --apply）")
        return report
    if not plan:
        return report

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for acc, a in accounts.items():
        shutil.copytree(a["dir"], os.path.expanduser(f"~/.claude/session-backups/sessions-{stamp}/{acc[:8]}"))
    for p in plan:
        acc, action = p[0], p[1]
        a = accounts[acc]
        if action == "archive":
            write_entry(p[2], isArchived=True)
        elif action == "show":
            ap = p[4] if len(p) > 4 else acc[:8]
            write_entry(p[2], isArchived=False, title=p[3], titleSource="user", fleetOwnerAccount=ap)
        elif action == "add":
            src, g, mt = p[2], p[3], p[4]
            ap = p[5] if len(p) > 5 else acc[:8]
            all_Pool = a["entries"] or [e for acc2 in accounts.values() for e in acc2["entries"]]
            tpl = max(all_Pool, key=lambda e: e.get("lastActivityAt") or 0) if all_Pool else {}
            d = {k: v for k, v in src.items() if k != "_file" and k not in DROP_FIELDS}
            for k in ENV_FIELDS:
                d[k] = copy.deepcopy(tpl.get(k))
            if tpl.get("sshConfig"):
                d["sshConfig"] = copy.deepcopy(tpl["sshConfig"])
            sid = "local_" + str(uuid.uuid4())
            ts = int(mt * 1000)
            d.update(
                sessionId=sid,
                title=g,
                titleSource="user",
                isArchived=False,
                createdAt=d.get("createdAt") or ts,
                lastActivityAt=ts,
                fleetOwnerAccount=ap,
            )
            json.dump(d, open(os.path.join(a["dir"], sid + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"已改完，备份在 ~/.claude/session-backups/sessions-{stamp}/。退出并重开 Claude app 生效。")
    return report


NOISE = ("<system-reminder>", "<local-command", "<command-", "<task-notification", "<bash-", "[Image", "[Request interrupted",
         "Base directory for this skill", "(Re-invocation", "Caveat:", "This session is being continued", "<side-sessions-event>", "<ci-monitor-event>",
         "【双端共研同步", "【跨号防封安全交接", "# 双端共研交接单：")


def local_time(ts):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))))
    except Exception:
        return ts[5:16]


def texts_of(d):
    c = (d.get("message") or {}).get("content")
    parts = [c] if isinstance(c, str) else [b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text"]
    return [x.strip() for x in parts if x.strip() and not x.lstrip().startswith(NOISE) and "<!-- CLAUDE_SECTION_START -->" not in x and "<!-- AGY_SECTION_START -->" not in x]


def sanitize_section_body(body):
    if not body:
        return ""
    body = re.sub(r"<!-- (?:CLAUDE|AGY)_SECTION_(?:START|END) -->\n?", "", body)
    body = re.sub(r"<!-- (?:MTIME|K): \d+ -->\n?", "", body)
    body = re.sub(r"^# 双端共研交接单：.*$", "### 历史交接单引用", body, flags=re.MULTILINE)
    return body.strip()


def extract_section_block(text, side):
    pattern = rf"<!-- {side}_SECTION_START -->\s*<!-- MTIME: (\d+) -->\s*<!-- K: (\d+) -->\n(.*?)\n<!-- {side}_SECTION_END -->"
    m = re.search(pattern, text or "", flags=re.DOTALL)
    if not m:
        return None
    return {"mtime": int(m.group(1)), "k": int(m.group(2)), "body": sanitize_section_body(m.group(3))}


def write_merged_handoff(g, side, section_body, mtime, k):
    safe_g = SafeFilename(g)
    os.makedirs(HANDOFFS, exist_ok=True)
    os.makedirs(AGY_HANDOFFS, exist_ok=True)
    c_dst = os.path.join(HANDOFFS, f"{safe_g}.auto.md")
    a_dst = os.path.join(AGY_HANDOFFS, f"{safe_g}.auto.md")

    existing = ""
    if os.path.exists(c_dst):
        existing = open(c_dst, encoding="utf-8", errors="ignore").read()
    elif os.path.exists(a_dst):
        existing = open(a_dst, encoding="utf-8", errors="ignore").read()

    c_blk = extract_section_block(existing, "CLAUDE")
    a_blk = extract_section_block(existing, "AGY")
    cur_blk = {"mtime": int(mtime or time.time()), "k": int(k or 0), "body": sanitize_section_body(section_body)}
    if side == "CLAUDE":
        c_blk = cur_blk
    else:
        a_blk = cur_blk

    blocks = []
    if c_blk:
        blocks.append(("CLAUDE", "Claude Desktop", c_blk))
    if a_blk:
        blocks.append(("AGY", "反重力 Antigravity", a_blk))
    blocks.sort(key=lambda x: x[2]["mtime"], reverse=True)

    now_str = time.strftime("%Y-%m-%d %H:%M")
    header = [
        f"# 双端共研交接单：{g}",
        f"- **最后自动刷新**：{now_str}（零 token 提取，Claude × 反重力双端共用）",
    ]
    if len(blocks) == 2:
        b0, b1 = blocks[0], blocks[1]
        t0 = time.strftime("%m-%d %H:%M", time.localtime(b0[2]["mtime"]))
        t1 = time.strftime("%m-%d %H:%M", time.localtime(b1[2]["mtime"]))
        header.append(f"- **⚡ 最新活跃端**：**{b0[1]}**（{t0}，上下文 {b0[2]['k']}k） | **协同端**：**{b1[1]}**（{t1}，上下文 {b1[2]['k']}k）")
    elif len(blocks) == 1:
        b0 = blocks[0]
        t0 = time.strftime("%m-%d %H:%M", time.localtime(b0[2]["mtime"]))
        header.append(f"- **⚡ 当前主控端**：**{b0[1]}**（{t0}，上下文 {b0[2]['k']}k） | **另一端**：已 1:1 镜像同步待命")
    header.append(f"- **跨端接手口令**：在任意一端直接发 `读 ~/.claude/session-handoffs/{safe_g}.auto.md 接着做`")
    header.append("")

    manual = os.path.join(HANDOFFS, f"{safe_g}.md")
    if os.path.exists(manual):
        header += [
            f"## 📌 手写交接补充（{time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(manual)))}）",
            f"见 `{manual}`，优先读它。",
            "",
        ]

    body_parts = []
    for s_code, _, blk in blocks:
        body_parts.append(
            f"<!-- {s_code}_SECTION_START -->\n"
            f"<!-- MTIME: {blk['mtime']} -->\n"
            f"<!-- K: {blk['k']} -->\n"
            f"{blk['body']}\n"
            f"<!-- {s_code}_SECTION_END -->"
        )

    full_text = "\n".join(header) + "\n\n".join(body_parts) + "\n"
    with open(c_dst, "w", encoding="utf-8") as f:
        f.write(full_text)
    with open(a_dst, "w", encoding="utf-8") as f:
        f.write(full_text)
    return c_dst, full_text


def find_raw_jsonl_for_group(g, members):
    """优先找未压缩的真实长会话 jsonl；若当前全是 6 行交接种子，则从既有 .auto.md 回溯原始长会话 jsonl 路径。"""
    latest = pick_latest(members)
    if latest[2] and not latest[2].get("is_mirror"):
        return latest[2]["path"], latest[1].get("cwd", "")
    # 在同组所有成员中找非镜像且最新的真实 jsonl
    real_members = [m for m in members if m[2] and not m[2].get("is_mirror")]
    if real_members:
        best = max(real_members, key=lambda m: m[2]["mtime"])
        return best[2]["path"], best[1].get("cwd", "")
    # 从既有 .auto.md 中提取「- **原记录**：`...`」
    safe_g = SafeFilename(g)
    h_path = os.path.join(HANDOFFS, f"{safe_g}.auto.md")
    if os.path.exists(h_path):
        txt = open(h_path, encoding="utf-8", errors="ignore").read()
        m_p = re.search(r"## 🟣 Claude Desktop 端进展.*?- \*\*原记录\*\*：`([^`]+\.jsonl)`", txt, flags=re.DOTALL)
        if m_p:
            orig_p = m_p.group(1)
            if os.path.exists(orig_p) and sum(1 for _ in open(orig_p, encoding="utf-8", errors="ignore")) > 6:
                return orig_p, latest[1].get("cwd", "")
            # 检查是否已迁入 banned-accounts-archive
            base_name = os.path.basename(orig_p)
            for cand in glob.glob(os.path.expanduser(f"~/.claude/session-backups/banned-accounts-archive/**/{base_name}"), recursive=True):
                if os.path.exists(cand) and sum(1 for _ in open(cand, encoding="utf-8", errors="ignore")) > 6:
                    return cand, latest[1].get("cwd", "")
    return None, ""


def brief(piece):
    rules = load_rules()
    _, groups = scan(rules)
    hits = [piece] if piece in groups else [g for g in groups if g != "__hide__" and piece in g]
    if len(hits) != 1:
        print("没找到唯一的线程，候选：" + ("、".join(hits) if hits else "无"))
        return
    g = hits[0]
    members = groups[g]
    if REMOTE:
        req = {"force": [g], "groups": {g: [{"cli": e.get("cliSessionId"), "cwd": e.get("cwd"), "recap": recap_of(e)} for _, e, _ in members]}}
        dst = json.loads(mac("serve", json.dumps(req)))["briefed"].get(g)
        print(f"已写 {dst}（Mac 上）\n新号里新开会话，标题「续写：{g}」，第一句：读 {dst} 接着做" if dst else "Mac 上找不到这个线程的记录。")
        return
    raw_path, cwd = find_raw_jsonl_for_group(g, members)
    if not raw_path:
        print("该线程在 Claude 端为反重力镜像种子（直接读取交接单即可）。")
        return
    recap = next((recap_of(m[1]) for m in members if recap_of(m[1])), None)
    write_brief(g, raw_path, cwd, recap)


def brief_all(quiet=False):
    rules = load_rules()
    _, groups = scan(rules)
    cnt = 0
    for g, members in groups.items():
        if g == "__hide__":
            continue
        raw_path, cwd = find_raw_jsonl_for_group(g, members)
        if not raw_path:
            continue
        if any(not m[1].get("isArchived") for m in members):
            recap = next((recap_of(m[1]) for m in members if recap_of(m[1])), None)
            write_brief(g, raw_path, cwd, recap, quiet=True)
            cnt += 1
    if not quiet:
        print(f"已批量刷新 {cnt} 个 Claude 原生主线程的双端交接单。")


DIRECTION_KEYWORDS = (
    "不对", "不要", "严禁", "必须", "改用", "改成", "别", "方向", "重点", "注意",
    "只用", "禁止", "错了", "有问题", "为什么", "红线", "铁律", "不能", "优先", "先做",
    "停", "重来", "不是这个", "记住", "以后", "核心", "目标", "要求"
)


def extract_git_ground_truth(cwd):
    """源自 davila7/session-handoff/scripts/create_handoff.py：零 token 提取工作区 Git 分支、最近 Commit 与未提交改动。"""
    if not cwd or not os.path.isdir(cwd):
        return []
    out = []
    try:
        br = subprocess.run(["git", "branch", "--show-current"], cwd=cwd, capture_output=True, text=True, timeout=3)
        branch = (br.stdout or "").strip()
        if not branch:
            return []
        lg = subprocess.run(["git", "log", "--oneline", "-5"], cwd=cwd, capture_output=True, text=True, timeout=3)
        commits = [x.strip() for x in (lg.stdout or "").splitlines() if x.strip()]
        st = subprocess.run(["git", "status", "-s"], cwd=cwd, capture_output=True, text=True, timeout=3)
        dirty = [x.rstrip() for x in (st.stdout or "").splitlines() if x.strip()]
        out.append("### 🧬 Git 与工作区实时物理真源（源自 `davila7/session-handoff`）")
        out.append(f"- **当前分支**：`{branch}` | **未提交改动数**：`{len(dirty)}` 个文件")
        if commits:
            out.append("- **最近 5 次 Git Commit（新→旧）**：")
            for c in commits[:5]:
                out.append(f"  - `{c}`")
        if dirty:
            out.append("- **当前工作区未提交/修改中文件（Top 12）**：")
            for d_line in dirty[:12]:
                out.append(f"  - `{d_line}`")
        out.append("")
    except Exception:
        pass
    return out


def write_brief(g, path, cwd, recap, quiet=False):
    users, claude = [], []
    files_written = {}   # path -> {"op": "Write/Edit", "count": int, "last_ts": str}
    ref_docs = []        # list of .md / plan / skill docs touched or read
    bash_cmds = []       # list of (ts, cmd)
    gotchas = []         # list of (ts, err_snippet)
    latest_todos = []    # latest TodoWrite items

    for line in open(path, encoding="utf-8", errors="ignore"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("isSidechain") or d.get("isMeta") or d.get("type") not in ("user", "assistant"):
            continue
        ts = local_time(d.get("timestamp", ""))
        for x in texts_of(d):
            (users if d["type"] == "user" else claude).append((ts, x))

        # 深度解析 AST / Tool-Use 与 Tool-Result（融合 REMvisual/claude-handoff + affaan-m/strategic-compact）
        msg_content = (d.get("message") or {}).get("content")
        if isinstance(msg_content, list):
            for blk in msg_content:
                if not isinstance(blk, dict):
                    continue
                btype = blk.get("type")
                if btype == "tool_use":
                    tname = blk.get("name") or ""
                    tinp = blk.get("input") or {}
                    if not isinstance(tinp, dict):
                        continue
                    fpath = tinp.get("file_path") or tinp.get("path") or tinp.get("notebook_path") or ""
                    if tname in ("Write", "Edit", "MultiEdit", "NotebookEdit", "write_file", "edit_file") and fpath:
                        info = files_written.setdefault(fpath, {"op": tname, "count": 0, "last_ts": ts})
                        if tname in ("Write", "write_file"):
                            info["op"] = "Write(新建/覆写)"
                        elif info["op"] == tname:
                            info["op"] = "Edit(增量修改)"
                        info["count"] += 1
                        info["last_ts"] = ts
                        if fpath.endswith(".md") and fpath not in ref_docs:
                            ref_docs.append(fpath)
                    elif tname in ("Read", "read_file") and fpath and fpath.endswith(".md"):
                        if any(k in fpath.lower() for k in ("skill", "plan", "prd", "design", "handoff", "readme", "claude", "agents", "memory")):
                            if fpath not in ref_docs:
                                ref_docs.append(fpath)
                    elif tname in ("Bash", "run_command"):
                        cmd_str = (tinp.get("command") or tinp.get("CommandLine") or "").strip()
                        if cmd_str and not cmd_str.startswith(("ls ", "pwd", "echo ")):
                            one_cmd = " ".join(cmd_str.split())[:220]
                            bash_cmds.append((ts, one_cmd))
                    elif tname == "TodoWrite" and isinstance(tinp.get("todos"), list):
                        latest_todos = tinp.get("todos")
                elif btype == "tool_result" and blk.get("is_error"):
                    err_c = blk.get("content")
                    if isinstance(err_c, list):
                        err_c = " ".join(x.get("text", "") for x in err_c if isinstance(x, dict))
                    err_str = " ".join(str(err_c or "").split())
                    if err_str and "Request interrupted" not in err_str and "sibling tool call errored" not in err_str:
                        gotchas.append((ts, err_str[:260]))

    k = context_k(path) or 0
    mt = os.path.getmtime(path)
    mt_str = time.strftime("%m-%d %H:%M", time.localtime(mt))

    # 计算 davila7/session-handoff 质量分
    quality_score = 70
    if users:
        quality_score += 10
    if files_written or bash_cmds:
        quality_score += 10
    if claude:
        quality_score += 10

    sec = [
        f"## 🟣 Claude Desktop 端进展（更新时间：{mt_str} | 原上下文：{k}k | 交接质量分：{quality_score}/100 [READY]）",
        f"- **原记录**：`{path}`（共 `{len(users)}` 轮用户指令，已修改 `{len(files_written)}` 个文件）",
        f"- **工作目录**：`{cwd}`",
        f"- **按需查细节**：`python3 ~/.claude/scripts/jsonl-digest.py {path} 25`（严禁整份读 jsonl）",
        "",
    ]
    if recap:
        sec += ["### 📋 Claude 端 · 官方会话摘要（Recap）", str(recap)[:1500], ""]

    # 1. 原始立项北极星（The Goal — 保留最初 2 条指令，严防长会话丢失初心）
    if len(users) > 10:
        sec += ["### 🎯 1. 原始立项北极星（The Goal — 会话首轮立项初心，严防跑偏）"]
        for t, x in users[:2]:
            sec.append(f"- **{t}（首轮锚点）**：{x[:600].replace(chr(10), ' ')}")
        sec.append("")

    # 2. 用户纠偏与偏好红线（User Feedback & Guardrails — 源自 REMvisual/claude-handoff 必填项）
    guardrails = []
    seen_g = set()
    for t, x in users[:-3]:
        if any(kw in x for kw in DIRECTION_KEYWORDS):
            clean_x = x[:500].replace(chr(10), " ")
            if clean_x[:80] not in seen_g:
                seen_g.add(clean_x[:80])
                guardrails.append((t, clean_x))
    if guardrails:
        sec += ["### 🧭 2. 用户核心方向纠偏与红线偏好（User Feedback & Guardrails — 必读防跑偏）"]
        for t, gx in guardrails[-8:]:
            sec.append(f"- **{t}**：{gx}")
        sec.append("")

    # 3. Git 与工作区实时物理真源（源自 davila7/session-handoff）
    sec += extract_git_ground_truth(cwd)

    # 4. 会话真实修改文件账本与关联规范文档（源自 affaan-m/strategic-compact What Must Survive）
    if files_written:
        sec += ["### 🛠️ 3. 会话内真实修改/新建文件账本（Files Changed Ledger）"]
        sorted_files = sorted(files_written.items(), key=lambda kv: kv[1]["last_ts"], reverse=True)
        for fpath, info in sorted_files[:20]:
            sec.append(f"- `{fpath}` — **{info['op']}**（共 {info['count']} 次，最后更新 {info['last_ts']}）")
        sec.append("")

    if ref_docs:
        sec += ["### 📚 4. 关联架构/规范/Skill 文档索引（Reference Documents）"]
        for rdoc in ref_docs[-12:]:
            sec.append(f"- `{rdoc}`")
        sec.append("")

    if latest_todos:
        sec += ["### ✅ 5. 会话内 Todo 任务进度表（Active Task State）"]
        for td in latest_todos[:12]:
            if isinstance(td, dict):
                st_icon = "✅" if td.get("status") == "completed" else ("🔄" if td.get("status") == "in_progress" else "⏳")
                sec.append(f"- {st_icon} [{td.get('status', 'pending')}] {td.get('content', '')}")
        sec.append("")

    # 5. 已执行的验证命令与已踩坑报错记录（What We Tried & Gotchas — 严防重复试错）
    if bash_cmds:
        sec += ["### ⚡ 6. 最近关键执行与测试命令（Key Commands Executed）"]
        seen_c = set()
        dedup_cmds = []
        for ts_c, cmd_s in reversed(bash_cmds):
            if cmd_s not in seen_c:
                seen_c.add(cmd_s)
                dedup_cmds.append((ts_c, cmd_s))
            if len(dedup_cmds) >= 6:
                break
        for ts_c, cmd_s in reversed(dedup_cmds):
            sec.append(f"- {ts_c}：`{cmd_s}`")
        sec.append("")

    if gotchas:
        sec += ["### ⚠️ 7. 已踩坑与报错记录（Failed Approaches & Gotchas — 严禁重复试错）"]
        for ts_e, err_s in gotchas[-5:]:
            sec.append(f"- {ts_e}：`{err_s}`")
        sec.append("")

    # 6. 用户最近 12 条指令（最后 3 条保留 1500 字完整上下文，零截断防指令丢失）
    sec += ["### 📌 8. Claude 端 · 用户最近 12 条指令（旧→新，最后 3 条完整保留）"]
    recent_users = users[-12:]
    for idx, (t, x) in enumerate(recent_users):
        if idx >= len(recent_users) - 3:
            sec.append(f"- **{t}（最新关键指令）**：\n  {x[:1500]}")
        else:
            sec.append(f"- {t}：{x[:450].replace(chr(10), ' ')}")

    # 7. 最后 6 段关键回复与下一步计划（每段保留 1800 字高密度技术细节）
    sec += ["", "### 🚀 9. Claude 端 · 最后 6 段关键交付与下一步结论（旧→新）"]
    for t, x in claude[-6:]:
        sec += [f"#### {t}", x[:1800], ""]

    dst, text = write_merged_handoff(g, "CLAUDE", "\n".join(sec), mt, k)
    if quiet:
        return dst
    print(f"已写双端合并交接单：{dst}（约 {len(text) // 2 // 1000 + 1}k token，Claude 原线程 {k}k，质量分 {quality_score}/100）")
    print(f"双端任意一侧直接发：读 {dst} 接着做")
    return dst


def auto():
    import contextlib, io
    log = os.path.expanduser("~/.claude/session-backups/auto.log")
    global AUTO_BRIEF
    AUTO_BRIEF = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if not REMOTE:
            brief_all(quiet=True)
        report = sync(True)
    for d in glob.glob(os.path.expanduser("~/.claude/session-backups/sessions-*")):
        if os.path.getmtime(d) < time.time() - 3 * 86400:
            shutil.rmtree(d, ignore_errors=True)
    now = time.strftime("%m-%d %H:%M ")
    lines = []
    for l in buf.getvalue().splitlines():
        if l.startswith("已改完"):
            lines.append(now + "侧边栏已同步")
        elif l.startswith("已写"):
            lines.append(now + "刷新交接：" + os.path.basename(l.split("（")[0][3:].replace("\\", "/")).replace(".auto.md", ""))
        elif l.startswith("连不上"):
            lines.append(now + l)
    if lines:
        with open(log, "a", encoding="utf-8") as h:
            h.write("\n".join(lines) + "\n")
    agy_script = os.path.expanduser("~/.gemini/antigravity/scripts/agy-sessions.py")
    if os.path.exists(agy_script):
        try:
            subprocess.run([sys.executable, agy_script, "auto"], timeout=45)
        except Exception:
            pass


def edit_list(which, piece):
    rules = load_rules()
    other = "hide" if which == "keep" else "keep"
    rules[other] = [x for x in rules.get(other, []) if x != piece]
    if piece not in rules.setdefault(which, []):
        rules[which].append(piece)
    if REMOTE:
        print(mac(f"{which} '{piece}'").strip())
        return
    save_rules(rules)
    print(f"已记下：{'常驻' if which == 'keep' else '隐藏'}「{piece}」。跑 sessions 看效果，sessions --apply 生效。")


def prep_switch():
    """净室无串号切号预备：
    1) 终止全部残留 claude --resume CLI 子进程（防止旧号后台子进程在新号登录时触发 acct:旧号|orgId:新号 混合 OAuth 请求自爆）
    2) 提取全部活跃线程 .auto.md 交接单，并执行各账号独立 SessionID 隔离同步
    3) 清理 config.json 中残留的 oauth:tokenCache 缓存
    """
    subprocess.run(["pkill", "-f", "claude.app/Contents/MacOS/claude"], check=False)
    if not REMOTE:
        brief_all(quiet=True)
    sync(True)
    cfg_path = os.path.expanduser("~/Library/Application Support/Claude/config.json")
    if os.path.exists(cfg_path):
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
            if "oauth:tokenCache" in cfg:
                cfg.pop("oauth:tokenCache", None)
                json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except Exception:
            pass
    print("🛡️ 已完成净室切号隔离：已杀灭旧号残留 CLI 子进程 + 清空旧 OAuth 令牌缓存 + 全账号独立 2k 交接种子已就绪！")


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        os.makedirs(os.path.expanduser("~/.claude/session-backups"), exist_ok=True)
        sys.stderr = open(os.path.expanduser("~/.claude/session-backups/auto.err"), "a", encoding="utf-8")
    elif hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    global REMOTE
    args = sys.argv[1:]
    if args[:1] in (["serve"], ["rules"]):
        REMOTE = None
    if args[:1] == ["serve"]:
        serve()
    elif args[:1] == ["rules"]:
        sys.stdout.write(json.dumps(load_rules(), ensure_ascii=False))
    elif args[:1] in (["tokens"], ["token"], ["budget"]):
        if REMOTE:
            print(mac("tokens " + " ".join(args[1:])).strip())
        else:
            tok_script = os.path.expanduser("~/.claude/scripts/tokens.py")
            subprocess.run([sys.executable, tok_script] + args[1:])
    elif args[:1] == ["auto"]:
        auto()
    elif "--prep-switch" in args or args[:1] == ["prep-switch"]:
        prep_switch()
    elif args[:1] == ["brief"] and len(args) > 1:
        brief(" ".join(args[1:]))
    elif args[:1] == ["brief-all"]:
        brief_all(quiet=False)
    elif args[:1] in (["keep"], ["hide"]) and len(args) > 1:
        edit_list(args[0], " ".join(args[1:]))
    else:
        apply_flag = "--apply" in args
        if apply_flag and not REMOTE:
            brief_all(quiet=True)
        sync(apply_flag)
        if apply_flag and not REMOTE and "--no-agy" not in args:
            agy_script = os.path.expanduser("~/.gemini/antigravity/scripts/agy-sessions.py")
            if os.path.exists(agy_script):
                print("\n>>> 正在联动执行反重力 (Antigravity) 端 1:1 同步与镜像注入...")
                subprocess.run([sys.executable, agy_script, "--apply"])


if __name__ == "__main__":
    main()
