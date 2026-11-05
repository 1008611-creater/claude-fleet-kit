---
name: cc-ip-skill
description: Claude Desktop / Claude Code 双机（Mac + Windows）唯一权威代理锁定、Clash 抗高并发防断流调优与跳 IP 秒关保号系统技能（cc-ip-skill v2.5）。本机默认固化「只用兔子 (<YOUR_SAFE_EXIT_IP>)、彻底弃用 IPPeak、关闭 tcp-concurrent 多线程抢跑、本地 REJECT 丢弃 datadoghq/intercom/sentry 遥测垃圾连接、跳 IP 零容忍秒杀 Claude、断网 6 次宽限防误杀」；同时支持他人复用时通过 fleet-init 或 cc-ip --set-node 一键切换为他们自己的代理节点与白名单 IP。当用户提到「防封」「IP 哨兵」「换代理」「跳 IP」「高并发失败」「同步代理到 Windows」「检查出口 IP」「号被封了」「cc-ip」或配置 Clash Verge / Claude 网络环境时必须激活本技能。
---

# `cc-ip-skill` — Claude 唯一权威代理锁定、抗高并发调优与跳 IP 秒关保号系统技能 (v2.5)

本技能沉淀自「号7被封（`401 OAuth access token has been revoked`）」与「住宅代理高并发 TLS 握手洪峰断流」实战复盘，与 `session-sync`、`agy-session-sync`、`token-budget`、`ans-skill-manager-skill` 并列为**全舰队五大底层基建系统技能**。

---

## 一、核心防封与抗高并发五大权威铁律

1. **唯一权威国外代理铁律（物理级移除备胎防漂移）**：
   - **本机权威配置**：唯一合法节点为 **`兔子-1ne0scsd`（`proxy.example.com:5117`，VLESS Reality，固定出口 IP `<YOUR_SAFE_EXIT_IP>`）**，彻底删除 `IPPeak` 及任何 `fallback` / `url-test` 自动切换组。
   - **外部用户/团队成员复用配置**：通过 `~/.claude/fleet-config.json`（或运行 `cc-ip --set-node "<节点名>" "<简称>" && cc-ip --set`）锁定他们自己的**唯一固定住宅/专线节点与出口 IP**，同样遵循「策略组内只放唯一节点、严禁配置 fallback 备胎节点」铁律，物理杜绝网络波动时自动跳 IP 封号！
2. **三项抗高并发拥塞手术（解决住宅代理多窗口/多 Subagent 卡死，零跳 IP 风险）**：
   - **① 关闭 `tcp-concurrent: false`（禁止多线程 TCP 抢跑）**：避免 Mihomo 内核对每次请求并发发起 2~3 条冗余 TCP 握手把单条 VLESS/住宅隧道冲垮。
   - **② 本地直接丢弃（`REJECT`）Claude 后台垃圾遥测连接**：将 `datadoghq.com`、`browser-intake-datadoghq.com`、`intercom.io`、`intercomcdn.com`、`sentry.io` 设为本地 `REJECT`（内核直接丢弃，不走外部节点、不暴露任何 IP），立砍 50% 无用背景并发连接！
   - **③ 自动清理 >180 秒僵尸连接 + 侧边栏飙红报警**：当节点活跃并发连接 `≥8 条` 时自动清理挂死超过 180 秒的陈旧连接；并发 `≥16 条` 或测速超时 (`0ms` / `≥2600ms`) 时，右侧侧边栏 `FleetSidebar.app` 全框鲜红飙红报警，支持一键点击 **`[⚡清并发重连]`**。
3. **双机同源 1:1 出口对齐铁律（严禁异地 IP 撕裂）**：
   - Mac 与 Windows 登录同一账号池或 SSH 协同跑会话时，**双机出口 IP 必须 100% 同步为同一个白名单 IP**。
4. **哨兵 v2.5 判罚逻辑：「双层零误杀核验 × 跳 IP 零容忍秒杀 × 断网宽限防误杀」**：
   - **双层核验**：1ms 读取本地 Mihomo Unix Socket 核验系统代理与节点锁定状态 + 每 300s 周期通过 `https://claude.ai/cdn-cgi/trace` 复核真实公网出口 IP。
   - **情形 0（出口 IP == 白名单 IP）**：安全放行（运行中每 `8s` 巡检，闲置每 `30s` 巡检）。
   - **情形 A（系统代理被关、节点被切走、或拿到非白名单 IP）**：**0 容忍当场秒杀** `Claude` 全家桶并弹窗告警。
   - **情形 B（瞬时断网/休眠唤醒探测不到 IP）**：第 3 次（~24s）弹窗预警，连续第 6 次（~48s）才断路强杀防直连裸奔。
5. **新号上线冷启动三红线（联动 `session-sync` & `token-budget`）**：
   - 上线前先跑 `cc-ip --status` 确认出口 IP 匹配（或点侧边栏 `[⚡ 一键切它]` 自动执行前置防封安检）；
   - 新号首日**严禁直接点开 `>200k` 老线程**（必须走 `.auto.md` 交接新开 `<5k` 会话）；
   - 新号首小时严禁并发拉起多个 Subagent 狂刷请求。

---

## 二、日常速查与自定义代理切换命令（零 Token）

```bash
# 1. 查看当前代理简称、锁定节点、白名单 IP 与真实出口状态
cc-ip --status

# 2. 【他人复用 / 更换代理】切换为你自己的 Clash 节点名称与侧边栏显示简称，并自动探测锁定新出口 IP：
cc-ip --set-node "你的Clash节点名称" "你的代理简称(如:美西家宽)"
cc-ip --set
# （或者手动指定白名单 IP：cc-ip --set 12.34.56.78）

# 3. 或者使用全舰队一键初始化工具（同时完成 Clash 抗高并发注入 + 账号自动发现 + 侧边栏重编）：
fleet-init --proxy-label "美西家宽" --proxy-node "你的Clash节点名称" --safe-ip auto --port 7897 --apply

# 4. 查看哨兵实时巡检日志
tail -n 30 ~/.claude/ip-guard.log
```

---

## 三、核心文件与双机守护清单

- **通用配置文件（SSOT）**：`~/.claude/fleet-config.json` 与 `~/.claude/safe_ip.txt`
- **Mac 哨兵主脚本（v2.5）**：[`scripts/claude-ip-guard.sh`](scripts/claude-ip-guard.sh)（软链：`~/.local/bin/cc-ip`）
- **Windows 端一键同步与 IP 哨兵安装器**：[`scripts/sync-clash-win.ps1`](scripts/sync-clash-win.ps1)、[`scripts/install-ip-guard-win.ps1`](scripts/install-ip-guard-win.ps1)

---

## 🧭 五大基建系统 Skill 专职主线程分工表（1:1 专款专用）

| 系统 Skill | 专职主线程名称（双端 1:1 统一） | 反重力 ID | 负责范围（严禁跨线程混杂） |
| :--- | :--- | :--- | :--- |
| **`session-sync` + `agy-session-sync`** | **`基建 · 会话管理与记忆同步`** | `1d11a3ce` | 仅负责 Mac 多号 × 反重力 × Win 侧边栏 1:1 同步、去重、恢复与 `.auto.md` 交接 |
| **`token-budget`** | **`基建 · Token额度精算与排班`** | `0b00d69d` | 专职负责四大算力池双引擎实时监控、每日排班、`FleetSidebar.app` 右侧栏与 `>200k` 巨兽治理 |
| **`cc-ip-skill`** | **`基建 · 兔子代理与防封哨兵`** | `6cd33893` | 专职负责唯一权威代理锁死、Clash 抗高并发手术、跳 IP 秒关保号与自定义代理适配 |
| **`ans-skill-manager-skill`** | **`基建 · Skill中枢管理与同步`** | `082a08f4` | 专职负责 49 个本地技能五端软链对齐、31,767+ 社区技能检索、原版安装与 `audit.py` 门禁 |
