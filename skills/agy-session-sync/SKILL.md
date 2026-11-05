---
name: agy-session-sync
description: 反重力 (Antigravity) × Claude Desktop 双端 1:1 统一会话管理与共研交接 Skill（对齐 session-sync 体系）。实现双端侧边栏 38 个「领域 · 主题」主线程 1:1 镜像对齐、同名合并去重、过期/一次性线程自动归档隐藏，以及双端共用同一份零 token `.auto.md` 交接单。当用户说「反重力会话管理」「清理侧边栏」「同步会话/线程」「去重线程」「要切号了」「交接」「接手 X」「双端同步」时使用。
---

# 反重力 (Antigravity) × Claude Desktop 双端统一会话管理 Skill

## 核心架构（双端 1:1 统一线程 + 共用交接单 + 零 Token）
1. **双端统一「领域 · 主题」命名体系（38 个常驻主线程，两侧边栏 1:1 完全镜像）**：
   - 六大领域前缀：`ANS · `（19 条）、`AI制版 · `（7 条）、`NB小科 · `（4 条）、`Beersong · `（3 条）、`创作 · `（3 条）、`基建 · `（2 条）。
   - **⚡ 双端原生共研（7 条）**：`基建 · 会话管理与记忆同步`、`ANS · 兔兔龙 Agent 开发`、`ANS · App 端与农客松开发`、`ANS · 工作台与外呼开发`、`ANS · 模型与算力积分开发`、`ANS · 短视频与宣传片`、`NB小科 · 纸飞机实验开发`。
   - **🟣 Claude 主控 → 1:1 镜像至反重力（23 条）**：自动在反重力生成 2 步原生 SQLite 会话（第 1 轮已内置 Claude 端最新 `.auto.md` 交接单，点开侧边栏即可直接续写；一旦在反重力发消息，步数 >2 自动升级为 `⚡ 双端共研`）。
   - **🔵 反重力主控 → 1:1 镜像至 Claude 四号（8 条）**：自动在 Claude `~/.claude/projects/` 生成 6 行标准 JSONL 会话并同步至号5/号6/号A/号B 侧边栏（第 1 轮已内置反重力最新 `.auto.md` 交接单，点开即可直接续写；一旦在 Claude 发消息，行数 >6 自动升级为 `⚡ 双端共研`）。
2. **双端共用同一份交接单 `~/.claude/session-handoffs/<线程名>.auto.md`**（同步镜像至 `~/.gemini/antigravity/session-handoffs/<线程名>.auto.md`）：
   - 自动融合 `🟣 Claude Desktop 端进展` 与 `🔵 反重力 (Antigravity) 端进展` 两个区块，**按最新活跃时间戳自动将最新一端置顶**，并在顶部标注 `⚡ 最新活跃端`。
3. **内核级防回写锁（`chflags uchg`）**：
   - `language_server` 退出（`Cmd+Q`）时会调用 `fs.Path.AtomicWrite`（写 `.tmp` 后 `os.Rename`）把内存里的旧 Map 覆盖回 `agyhub_summaries_proto.pb`。`agy-sessions` 写完后自动挂上 `stat.UF_IMMUTABLE`（`uchg`）锁，彻底阻断退出时的脏内存覆盖。

## 命令速查（全部零 Token，已内嵌 `token-budget` 额度排班子技能）
```bash
tokens                               # （或 sessions tokens / agy-sessions tokens）查看全舰队 Token 额度大盘、每日花费定额、账号接力顺位与 24h 吞金线程 TOP 10
sessions                             # 查看 Claude 侧双端 38 个主线程状态（顶部自带 4 行 Token 排班总控条）
agy-sessions                         # 查看反重力侧双端 38 个主线程状态（顶部自带 4 行 Token 排班总控条）
sessions --apply                     # 一键联动执行 Claude 四号同步 + 反重力 1:1 同步与镜像注入 + 刷新全部双端交接单与 TOKEN_BUDGET.md
agy-sessions --apply                 # 单独执行反重力侧同步、1:1 镜像注入与双端交接单刷新
agy-sessions brief <标题片段或ID>     # 零 token 刷新指定线程的双端合并交接摘要
agy-sessions brief-all               # 批量刷新全部活跃主线程的双端合并交接摘要
agy-sessions keep <标题片段>          # 设为常驻保留
agy-sessions hide <标题片段>          # 设为永久归档隐藏
agy-sessions rename <原名片段> <新名> # 添加合并与规范命名规则
```
- **强关联子技能 `token-budget`（`.agents/skills/token-budget/SKILL.md`）**：
  - 双端共享额度规划大盘：`~/.claude/session-handoffs/TOKEN_BUDGET.md`（镜像于 `~/.gemini/antigravity/session-handoffs/TOKEN_BUDGET.md`）。
  - 每次做会话管理或切号时，主动向用户汇报**今日应花目标 vs 已花进度**、**今日第 1 顺位主力号（防闲置周额度清零作废）**、**5h 窗口恢复时间**，以及需要立刻用 `.auto.md` 交接新开的 `>200k` 吞金线程。
- 全局总表：`~/.claude/session-handoffs/INDEX.md`（顶部实时内嵌 Token 排班总控条，与 `~/.gemini/antigravity/session-handoffs/INDEX.md` 实时一致）。
- 按需查看反重力原记录最后 N 条对话（禁止整份读 `transcript.jsonl`）：
  ```bash
  python3 ~/.gemini/antigravity/scripts/agy-digest.py <线程ID前缀> 20
  ```

## 当用户说「接手 <线程名>」时的标准动作
1. **只读双端合并交接单 `~/.claude/session-handoffs/<线程名>.auto.md`**（若有同名手写 `.md` 一并读），**绝不整份读取原 `transcript.jsonl` 或 `.jsonl`**。
2. 看清顶部 `⚡ 最新活跃端` 标注，优先以最新活跃端的进度为准，5 行内向用户汇报上次做到哪、产出了哪些文件/PR/Artifact、当前等用户拍板什么，然后待命或按指令继续。


## 🧭 四大系统 Skill 专职主线程分工表（1:1 专款专用）

| 系统 Skill | 专职主线程名称（双端 1:1 统一） | 反重力 ID | 负责范围（严禁跨线程混杂） |
| :--- | :--- | :--- | :--- |
| **`session-sync` + `agy-session-sync`** | **`基建 · 会话管理与记忆同步`** | `1d11a3ce` | 仅负责 Mac 四号 × 反重力 × Win 双号侧边栏 1:1 同步、去重、恢复与 `.auto.md` 交接 |
| **`token-budget`** | **`基建 · Token额度精算与排班`** | `0b00d69d` | 专职负责 6 号 + 反重力 5h/7d 额度监控、每日预算定额、切号顺位与 `>200k` 巨兽治理 |
| **`cc-ip-skill`** | **`基建 · 兔子代理与防封哨兵`** | `6cd33893` | 专职负责唯一权威代理「兔子 (`<YOUR_SAFE_EXIT_IP>`)」、清零 IPPeak、跳 IP 秒关保号与双机同步 |
| **`ans-skill-manager-skill`** | **`基建 · Skill中枢管理与同步`** | `082a08f4` | 专职负责 47+ 本地技能五端对齐、31,767+ 社区技能检索、原版安装与 `audit.py` 门禁 |
