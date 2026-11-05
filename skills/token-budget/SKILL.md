---
name: token-budget
description: 全舰队多引擎 Token 额度精算、排班与 macOS 右侧常驻秒切侧边栏系统 Skill（与 session-sync / agy-session-sync / cc-ip-skill 强绑定）。100% 本地零 Token、只读安全（不触碰 Keychain 登录锁），采用「官方采样锚点 + .jsonl 实时增量流水」双引擎计算，统管四大算力池（Claude 多账号池 + 反重力双池 + ChatGPT Plus 池 + API 中转站），内置代理高并发/断连飙红监控与屏幕右侧常驻浮窗 FleetSidebar.app（10秒自动刷新 + 一键切号 + 秒拷邮箱/密码/SessionKey）。支持通过 ~/.claude/fleet-config.json 与 fleet-init 一键适配任何团队成员自己的代理节点与账号池。当用户询问「我现在还有多少 token 可以花」「今天用哪个号」「每天该花多少 token」「别花超了/别浪费了」「额度排班」「看下 token」「右边栏」或进行跨端会话管理时主动使用。
---

# Token 额度精算、排班与常驻秒切侧边栏技能（`token-budget`）

本技能是 `session-sync`（Claude 端会话管理）、`agy-session-sync`（反重力端会话管理）与 `cc-ip-skill`（防封 IP 哨兵）的**强耦合核心基建技能**。解决四大核心痛点：
1. **「官方采样锚点 + `.jsonl` 实时增量流水」双引擎精算**：解决 `Claude.app` 不点开「设置 → 使用情况」就不更新 `plan-usage-history.json` 的滞后问题，每聊一句话实时折算叠加 `5h已用%` 与 `7d周已用%`。
2. **macOS 屏幕右侧常驻算力与秒切侧边栏（`FleetSidebar.app`）**：`RunLoop.main (.common)` 10 秒全自动刷新 + 右上角金色 `[🔄 刷新]` 按钮 + 每个账号一键 `[📋 复制邮箱]`、`[🔑 复制密码]`、`[🍪 复制Key]`、`[⚡ 一键切它]`（自动防封安检 + 复制邮箱 + 弹收信台 + 唤起 Claude + 后台同步 38 条主线程）。
3. **代理高并发与断连飙红监控**：实时监测 Mihomo 内核延迟与活跃并发连接数（超过 8 条并发自动清理 >180s 僵尸连接），一旦断连或高并发爆表立刻**全框鲜红飙红报警**并提供 `[⚡清并发重连]` 一键自愈。
4. **防到期浪费（Use-It-Or-Lose-It）× 防超支停摆（Anti-Stoppage）**：按「先到期、高余量优先 + 跨引擎分流」排班，并自动揪出 `>200k` 吞金巨兽线程，一键复制省流交接口令（单轮立省 85%~90% Token）。

---

## 一、零 Token 实时精算命令与常驻侧边栏

```bash
# 1. 查看四大算力池完整 Token 额度大盘、代理状态、接力顺位与 24h 吞金线程 TOP 10
tokens
# （等效别名：sessions tokens 或 agy-sessions tokens）

# 2. 仅输出 4 行极简排班总控条（已自动内嵌在 sessions 与 agy-sessions 顶部）
tokens --banner

# 3. 强制实时测速代理节点 / 一键清理代理高并发残留连接
python3 ~/.claude/scripts/tokens.py --ping-proxy
python3 ~/.claude/scripts/tokens.py --heal-proxy

# 4. 他人复用一键初始化向导（探测并绑定自己的 Clash 节点、出口 IP 与本地账号池，重编侧边栏）
fleet-init --detect
fleet-init --proxy-label "我的专线" --proxy-node "<你的Clash节点名>" --safe-ip auto --port 7897 --apply
```

- **核心脚本与组件清单（均已入库 SSOT `skills/token-budget/scripts/`）**：
  - [`scripts/tokens.py`](scripts/tokens.py)：双引擎实时额度精算、代理并发监控与自愈引擎。
  - [`scripts/FleetSidebar.swift`](scripts/FleetSidebar.swift)：macOS 右侧常驻悬浮窗源码（编译产物 `/Applications/FleetSidebar.app`）。
  - [`scripts/fleet-init.py`](scripts/fleet-init.py)：跨团队复用一键初始化与自定义代理适配工具。
  - [`references/fleet-config.example.json`](references/fleet-config.example.json)：通用代理与账号池配置模板（实际运行读取 `~/.claude/fleet-config.json` 与 `~/.claude/accounts-vault.json`）。

---

## 二、全舰队四大算力池架构与标定基准（本机权威台账）

> 🚨 **核心台账铁律（严禁把被封死号算进可用额度，严禁漏写邮箱前缀）**：
> - **Claude 账号总账**：共 8 个号，**被封 3 个**（旧号1 `user@...`、旧号2 `acct2@...`、旧号7 `6cce6dc4`），**存活 5 个**（`号8`、`号6`、`号5`、`号4`、`号3`）。账号备注标签保存在本机 `~/.claude/accounts-vault.json`（严禁写入公开文档或 Git 仓库）。

| 算力池 | 包含账号（代号 · **邮箱前缀** · 完整标识） | 容量基准（实测标定） | 核心职责与排班定位 |
| :--- | :--- | :--- | :--- |
| **1. Claude 存活 5 号池**<br>(官方采样锚点 + `.jsonl` 实时增量叠加) | • `号8` (**`acct4@...`** · `acct4@example.com` · 新购 Pro 满血 100%)<br>• `号6` (**`acct1@...`** · `acct1@example.com` · `2c7b8bc5` · org `64f75a0e`)<br>• `号5` (**`acct2@...`** · `acct2@example.com` · `42d51a75` · org `aac78d70`)<br>• `号4` (**`acct3@...`** · `acct3@example.com` · `10b3734b` · org `62ebe9e2` · 每周二 00:00 重置)<br>• `号3` (**`号3(4279c765)...`** · `4279c765` · org `48d63142` · 每周五 00:00 重置) | 单号每周 `100 pts` ≈ **7.1 个 5h 满窗** ≈ **`$300` API-eq**；存活 5 号合计每周 **`500 pts`（~35.5 个满窗 ≈ `$1,500`/周）** | 按 `tokens` 实时余量排序轮换（优先消耗满血 `号8` 与高余量号） |
| **🚫 已封禁 Claude (3个)**<br>(严禁计入可用额度) | • `旧号1(原号B)` (**`user@...`** · Claude端10-23被封)<br>• `旧号2(原号A)` (**`acct2@...`** · 10-29被封)<br>• `旧号7` (`6cce6dc4` · org `aac33650` · 已封禁) | **`0 pts`（已封禁作废）** | 仅保留本地历史目录识别，彻底从 Claude 可用额度池剔除 |
| **2. 反重力双池 (1个号)**<br>(Antigravity) | • `反重力 1号` (**`user@...`** · `user@example.com`)<br>  ├─ **子池 A：`Gemini 池`** (`Gemini 4 Argon` / `3.8 Flash`)<br>  └─ **子池 B：`内置 Claude & GPT 池`** (`Claude Opus 5.5` / `Sonnet 5.5`) | 两个独立 5h + 7d 进度条（完全不占 Claude Desktop 的 5 号额度！） | **王牌分流池**：文案策划/打杂用 `Gemini 4 Argon`；当 Claude 桌面端紧张时，直接在反重力内切 **`Claude Opus 5.5`**！ |
| **3. ChatGPT Plus 池 (2个号)**<br>(Codex 工程主力) | • `GPT Plus 1号` (**`reitsmahornb@...`** · `511ef260`)<br>• `GPT Plus 2号` (**`madinaichali@...`** · `1cf03f0f`) | 每个号独立 5h 窗口 + 7d 周配额 + **额外 2 次重置券 (`Reset Credits`)** | **全栈工程重器**：在 Codex 中跑 `gpt-6.1-sol` / `gpt-6-astra` 负责重度全栈开发、组件重构与自动化测试 |
| **4. `mcgrox.top` 中转站**<br>(及 `krill-code` / 火山) | • **`mcgrox.top`** (2 把 API Key)<br>• **`api.krill-code.net`** (1 把 Key)<br>• **`火山Agentplan`** (1 把 Key) | **无 5h 窗口锁**，按量/中转高并发调用 | **生图与并发弹药库**：支撑 `gpt-image-2.5` 生图与批量评测 |

---

## 三、外部用户 / 团队成员如何把代理改成自己的并一键复用？

当把本套技能分享给其他朋友或同事时，他们使用的不是「兔子 (`<YOUR_SAFE_EXIT_IP>`)」，只需运行一行命令即可把**侧边栏显示名、Mihomo 节点监控、IP 防封白名单、本地 Claude 账号池**全部切换成他们自己的：

```bash
# 第一步：先自动探测他们电脑上的 Clash 节点列表、当前出口 IP 与已登录的 Claude 账号
python3 ~/.claude/scripts/fleet-init.py --detect

# 第二步：指定他们自己的代理简称（如“美西家宽”）和 Clash 节点名，一键完成配置写入、Clash 抗高并发调优与侧边栏编译启动！
python3 ~/.claude/scripts/fleet-init.py \
  --proxy-label "美西家宽" \
  --proxy-node "你的Clash节点名称" \
  --safe-ip auto \
  --port 7897 \
  --apply
```

执行后：
- `~/.claude/fleet-config.json` 与 `~/.claude/safe_ip.txt` 自动写入他们自己的节点与出口 IP；
- `~/.claude/accounts-vault.json` 自动收录他们电脑上登录过的所有 Claude 账号（无需改一行代码）；
- 屏幕右侧 `/Applications/FleetSidebar.app` 与顶部菜单栏自动显示 `🟢 美西家宽 <他们的IP> 畅通`！

---

## 🧭 五大基建系统 Skill 专职主线程分工表（1:1 专款专用）

| 系统 Skill | 专职主线程名称（双端 1:1 统一） | 反重力 ID | 负责范围（严禁跨线程混杂） |
| :--- | :--- | :--- | :--- |
| **`session-sync` + `agy-session-sync`** | **`基建 · 会话管理与记忆同步`** | `1d11a3ce` | 专职负责 Mac 多号 × 反重力 × Win 侧边栏 1:1 同步、去重、恢复与 `.auto.md` 交接 |
| **`token-budget`** | **`基建 · Token额度精算与排班`** | `0b00d69d` | 专职负责四大算力池双引擎实时监控、每日排班、`FleetSidebar.app` 右侧栏与 `>200k` 巨兽治理 |
| **`cc-ip-skill`** | **`基建 · 兔子代理与防封哨兵`** | `6cd33893` | 专职负责唯一权威代理锁死、Clash 抗高并发手术、跳 IP 秒关保号与自定义代理适配 |
| **`ans-skill-manager-skill`** | **`基建 · Skill中枢管理与同步`** | `082a08f4` | 专职负责 49 个本地技能五端软链对齐、31,767+ 社区技能检索、原版安装与 `audit.py` 门禁 |
