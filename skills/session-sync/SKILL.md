---
name: session-sync
description: ANS会话管理 skill（目录名 session-sync）。多号轮换 + Mac/Windows 两台电脑的会话管理：换号前后同步侧边栏、合并重复线程、只留要用的线程，以及几乎零 token 的交接。用户说「换号」「要切号了」「撞限额了」「同步会话/线程」「同步记忆」「去重线程」「清理侧边栏」「交接」「续写某个线程」「ans会话管理」「Windows 侧边栏和 Mac 不一致」时使用。不用于 ANS 业务开发本身。
---

# ANS会话管理 skill（四个号轮换 + 两台电脑，省 token 版）

## 先记住为什么会烧额度
各号的会话记录（jsonl）和记忆在本机共用，**只有侧边栏按号分开**。所以「同步」只改侧边栏条目，
用脚本做，**一个 token 都不花**。
真正烧额度的是换号后**续写大线程**：新号没有提示缓存，第一句话要把整段上下文重新算一遍。
一个线程 20–35 万 token，点开十几个就把 5 小时额度用完了。所以：
- 同步、去重、隐藏 → 只跑脚本，**禁止**读 jsonl、禁止开「续写」会话去「同步记忆」。
- 换号后只续写马上要干活的线程；大线程走交接摘要，不直接续。

## 命令（全部零 token，已内嵌 `token-budget` 额度排班子技能）
```bash
sessions                       # 看现状：顶部直接展示 Token 额度与今日排班总控条 + 38 个统一主线程表
sessions tokens                # （或直接跑 tokens）查看完整 Token 额度大盘、每日花费定额、账号接力顺位与 24h 吞金线程 TOP 10
sessions --apply               # 去重 + 本机四个号侧边栏对齐 + 反重力 1:1 镜像注入 + 刷新 TOKEN_BUDGET.md（自动备份）
sessions brief <标题片段>       # 生成交接摘要 ~/.claude/session-handoffs/<线程>.auto.md（约 2–4k token）
sessions keep <标题片段>        # 常驻：不因过期被隐藏
sessions hide <标题片段>        # 永久隐藏
```
**强关联子技能 `token-budget`（`~/.claude/skills/token-budget/SKILL.md`）**：
- 每次同步或换号时，必须同步关注 **`tokens`（或 `~/.claude/session-handoffs/TOKEN_BUDGET.md`）** 给出的每日额度规划：
  1. **防到期浪费（Use-It-Or-Lose-It）**：优先切到排班顺位 `#1`（距离周刷新最近、剩余周额度最高的闲置号，如 `号B`、`号A`），按每日建议定额消耗，绝不让周额度白白清零作废。
  2. **防超支停摆（Anti-Stoppage）**：余量 `<38%` 的账号退居替补；对 `tokens` 揪出的 `>200k` 吞金巨兽线程，强制走 `.auto.md` 交接新开（单轮立省 85%~90% Token）。
**自动模式**：`sessions auto` 由 launchd（`~/Library/LaunchAgents/com.eeo.sessions-auto.plist`）每 15 分钟跑一次：
同步四个号侧边栏，并给有新进展、上下文 >80k 的线程刷新 `.auto.md` 交接摘要，备份只留 3 天，日志在
`~/.claude/session-backups/auto.log`。用户不用盯额度：撞限额时摘要已经是最新的，直接切号即可。
查是否在跑：`launchctl print gui/$(id -u)/com.eeo.sessions-auto | grep state`。

改完侧边栏要**退出并重开 Claude app** 才显示。规则文件 `~/.claude/session-groups.json`
（merge 合并标题、keep、hide、active_days=1.5 天没动就隐藏）。备份在 `~/.claude/session-backups/`。

`sessions` 里「换号后」一列：
- **直接续**（≤80k）：新号里直接点开继续。
- **可续·偏贵**（80–200k）：真要干活再点；不急就 brief。
- **先交接**（>200k）：跑 `sessions brief`，在新号新开会话「续写：<线程名>」，第一句只说「读 <auto.md 路径> 接着做」。

## 你（Claude）被叫来做这件事时的流程
1. 跑 `sessions`，把表格要点用一两句话告诉用户（几个线程、哪些要交接）。不要读任何 jsonl。
2. 用户要「只留要用的」时，把疑似做完的线程列出来让用户勾选（AskUserQuestion，多选），勾中的 `sessions hide`。
   不确定的别替用户隐藏；过期的脚本会自动藏，`sessions keep` 能恢复。
3. `sessions --apply`，再跑一次 `sessions` 确认「无需改动」。正在运行的会话脚本不动，下次再收。
4. 用户说「要切号了」：对表里「先交接」的、且用户接下来要继续的线程跑 `sessions brief`。
   当前号还有额度、线程又很关键时，可以额外让那个线程自己写一份手写交接
   `~/.claude/session-handoffs/<线程名>.md`（≤40 行：做完什么、还剩什么、关键路径和 PR、待用户定的事）；
   auto.md 会自动指向它。
5. 提醒用户：退出重开 app → 切号（`claude-switch` 或 app 里切）→ 只点开要干活的线程。

## 「接手 X」「接手 ANS」（换号或跨 App 一句话接手）
用户在新号或跨 App（Claude ↔ 反重力 Antigravity）说「接手 <线程名>」或「接手 ANS」时：
1. **只读双端合并交接摘要，绝不读原 jsonl / transcript.jsonl**。所有 38 个统一主线程（`领域 · 主题` 格式）在 Claude 与反重力两侧边栏已 1:1 镜像对齐，共用 `~/.claude/session-handoffs/<线程名>.auto.md`（内部同时包含 `🟣 Claude Desktop 端进展` 与 `🔵 反重力端进展`，最新活跃端自动置顶；有同名手写 `.md` 一并读）。全局总表见 `~/.claude/session-handoffs/INDEX.md`。
2. 「接手 ANS」= 把 ANS 工作流线程全接过来：`ANS · 运营工作流总控` 加上所有
   `ls ~/.claude/session-handoffs/ANS\ ·\ *.auto.md`（按现有文件列，不写死清单）。
   用户点名别的线程（如「AI制版 · 制版总控」）才一起接。
3. 每个线程用 start_session（initiation user_asked，context fresh，use_worktree false）开一个会话，标题**和原线程同名**，
   prompt 只写：读 `<auto.md 路径>`（和手写 .md），5 行内说清做到哪、等用户什么，然后停，不开工、不读原 jsonl。
   一次全开，不逐个等回报；主会话最后一行列出开了哪些。
4. 单个「接手 X」不必开新会话，直接在当前会话读摘要接着做；或者直接在侧边栏点开已注入 1:1 镜像的同名线程直接续写。

## 在新号里接手大线程
新会话只读 `.auto.md`（和它指向的手写交接）。缺细节时用
`python3 ~/.claude/scripts/jsonl-digest.py <原记录> 20` 看最后 20 段，**不要整份读原 jsonl**。

## 两台电脑
- 本机（My-MacBook-Pro，Tailscale <YOUR_MAC_TAILSCALE_IP>）：号5、号6、号A、号B 的侧边栏都在这里，脚本全管。
- Windows 台式机（win-desktop，<YOUR_WIN_TAILSCALE_IP>）：它的 app 通过 SSH 在本机跑会话（号3、号4 等），
  会话记录在本机，侧边栏存在 Windows 上。同一个 `sessions.py` 在 Windows 上走「远程模式」
  （有 `%USERPROFILE%\.claude\sessions-remote.json` 就是）：每次一条 SSH 调 Mac 的 `sessions.py serve`
  拿更新时间、上下文、运行状态，交接摘要由 Mac 生成到本机 `~/.claude/session-handoffs/`；
  规则 `session-groups.json` 以 Mac 为准，Windows 上 keep/hide 也转发给 Mac。
  Windows 上安装/更新（PowerShell，一次即可，重跑=更新）：
  ```powershell
  $a=@(); $p=Get-ChildItem "$HOME\.ssh\*.pub" -EA 0 | ? {(Get-Content $_.FullName -Raw) -match 'eeo-mbp'} | select -First 1; if($p){$a='-i',($p.FullName -replace '\.pub$','')}; New-Item -ItemType Directory -Force "$HOME\.claude\scripts" | Out-Null; scp @a <your-mac-user>@<YOUR_MAC_TAILSCALE_IP>:.claude/scripts/install-sessions-win.ps1 "$HOME\.claude\scripts\"; powershell -ExecutionPolicy Bypass -File "$HOME\.claude\scripts\install-sessions-win.ps1"
  ```
  安装脚本 `~/.claude/scripts/install-sessions-win.ps1`（必须保留 UTF-8 BOM，PowerShell 5 才不乱码）：
  找 SSH 钥匙 → 拉 sessions.py → 找/装 Python → 写配置和 `sessions` 命令 → 计划任务 ClaudeSessionsAuto 每 15 分钟。
  连不上 Mac 时只按侧边栏时间去重，不新增条目，日志里记「连不上 Mac」。
- 同一个线程别在两台电脑上同时续写。

## 新号上线
新账号第一次登录 app 后，它的侧边栏目录会出现在
`~/Library/Application Support/Claude/claude-code-sessions/<账号uuid>/`。跑一次 `sessions --apply` 就会被补齐。
在 `~/.claude/scripts/sessions.py` 的 `ACCOUNT_NAMES` 里给它起个名字（可选，只影响显示）。

## 两边侧边栏一致（Windows 镜像，2026-11-01 起）
- Windows 是 SSH 到 Mac 跑会话，记录（jsonl）都在 Mac，两边**应该共用同一批线程**，不用「接手」重开。
- Windows 上 `sessions --apply` / 计划任务（每 15 分钟）会：找到真实侧边栏目录 → 向 Mac 要线程清单 → 侧边栏缺的补一条指向同一份 jsonl（沿用本机条目的 sshConfig）→ 去掉「续写：」前缀对齐标题。只管最近在用的号，旧号目录不动。
- Windows 侧边栏目录：`%LOCALAPPDATA%\Claude-Data\<包名>\claude-code-sessions\<号>\<组织>\local_*.json`（脚本自动挑有条目的那个，不是 AppData\Roaming）。
- 要在 Windows 看到新线程：先完全退出 Claude app，再 `sessions --apply`，重开。更新脚本：
  `scp -i "$HOME\.ssh\eeo_mbp_ed25519" <your-mac-user>@<YOUR_MAC_TAILSCALE_IP>:.claude/scripts/sessions.py "$HOME\.claude\scripts\sessions.py"`
- 安装时报「没找到侧边栏目录」是安装脚本的旧检查，可忽略。
- 命令别贴错机器：提示符 `PS C:\Users\lsb>` 才是 Windows；`<your-mac-user>@My-MacBook-Pro ans %` 是 Mac。
- 归档一个会话会**连带归档它发起的子会话**；去重前先看清是不是发起者，误归档用 unarchive 逐个恢复。

## 点开线程要花多少（省 token 守则）
- 同步、补条目、去重、生成摘要全是脚本，零 token；没点开的线程不花钱。
- 只有点开后第一句话要把整段上下文重算一遍（缓存没过期则便宜）：
  - ≤80k：直接点开。
  - 80–200k：真要干活再点开。
  - >200k：先读 `~/.claude/session-handoffs/<线程>.auto.md`，摘要不够再点开；或新开「续写」只读摘要（约 2–4k token，最便宜但丢细节）。
- 别逐个点开看一眼；做完的线程 `sessions hide`；同一线程接着聊别隔太久。
- 查文件格式时让用户只贴关键字段，别贴整份条目（有一大段无用 MCP 配置）。


## 🧭 四大系统 Skill 专职主线程分工表（1:1 专款专用）

| 系统 Skill | 专职主线程名称（双端 1:1 统一） | 反重力 ID | 负责范围（严禁跨线程混杂） |
| :--- | :--- | :--- | :--- |
| **`session-sync` + `agy-session-sync`** | **`基建 · 会话管理与记忆同步`** | `1d11a3ce` | 仅负责 Mac 四号 × 反重力 × Win 双号侧边栏 1:1 同步、去重、恢复与 `.auto.md` 交接 |
| **`token-budget`** | **`基建 · Token额度精算与排班`** | `0b00d69d` | 专职负责 6 号 + 反重力 5h/7d 额度监控、每日预算定额、切号顺位与 `>200k` 巨兽治理 |
| **`cc-ip-skill`** | **`基建 · 兔子代理与防封哨兵`** | `6cd33893` | 专职负责唯一权威代理「兔子 (`<YOUR_SAFE_EXIT_IP>`)」、清零 IPPeak、跳 IP 秒关保号与双机同步 |
| **`ans-skill-manager-skill`** | **`基建 · Skill中枢管理与同步`** | `082a08f4` | 专职负责 47+ 本地技能五端对齐、31,767+ 社区技能检索、原版安装与 `audit.py` 门禁 |
