---
name: ans-skill-manager-skill
description: ANS (AI Never Stops) 全舰队 Agent Skills 权威中枢管理与 31,767+ 社区技能检索安装系统技能（别名 ans-skill-maneger-skill，CLI 命令 ans-skills）。深度内化自 GitHub 最热门的两大开源技能管理项目：xingkongliang/skills-manager (★7,164 多 Agent 中央库与预设部署架构) 与 buzhangsan/skill-manager (31,767+ 中英双语社区技能库检索与 SVN/Git Sparse Checkout 文件夹级完整下载)。实现本机 SSOT 中央库 (~/claude-fleet-kit/skills) 与 Claude Desktop、反重力 Antigravity、ANS 工作区、Codex、Windows 台式机五端 1:1 统一收编、软链部署、防山寨原版拉取与安全审计。当用户提到「管理 skill」「安装 skill」「找个 skill」「同步 skill」「我有哪些 skill」「ans-skill-manager」「skill-manager」时必须激活本技能。
---

# `ans-skill-manager-skill` — ANS 全舰队 Agent Skills 权威中枢与社区检索安装系统技能

> **官方品牌铁律**：`ANS` 的唯一官方全称是 **`AI Never Stops`**。
> **原版基因声明（严禁山寨铁律）**：本技能完整拉取并内化自 GitHub 最热门的两大开源 Agent Skill 管理器原版仓库：
> 1. **`xingkongliang/skills-manager`（★ 7,164）**：原版规范见 [`references/upstream-xingkongliang-manage-skills.md`](references/upstream-xingkongliang-manage-skills.md)（中央库 SSOT → `adopt` 收编 → `deploy/sync` 跨 Agent 软链分发 → 预设分组）。
> 2. **`buzhangsan/skill-manager`（★ 322 + client ★ 504）**：原版规范见 [`references/upstream-buzhangsan-SKILL.md`](references/upstream-buzhangsan-SKILL.md)，原版检索与下载脚本见 [`scripts/upstream-buzhangsan-index.js`](scripts/upstream-buzhangsan-index.js)，内置 31,767+ 条中英双语社区技能库（`~/.claude/skill-manager-db/all_skills_with_cn.json.gz`）。

---

## 一、核心架构：「一个权威中央库 (SSOT) × 五端 1:1 软链镜像 × 六大业务域预设」

告别过去各工具目录（Claude、反重力、Codex、工作区）各自散落、复制漂移、一边改了另一边没生效的乱象：

| 层级 | 路径 / 位置 | 角色定位 |
| :--- | :--- | :--- |
| **🏛️ 权威中央库 (SSOT)** | `~/claude-fleet-kit/skills/` | **全机唯一实体真源**（Git 版本受控，配属 `registry/skills.json` 与 `scripts/audit.py` 门禁） |
| **🟣 Mac Claude 端** | `~/.claude/skills/` | 统一通过软链指向 SSOT，Mac 四号（号B/号A/号6/号5）实时共享 |
| **🔵 Mac 反重力端 (AGY)** | `~/.gemini/config/skills/` | 统一通过软链指向 SSOT，Antigravity 全局实时生效 |
| **🚀 ANS 主工作区** | `~/workspace/.agents/skills/` | 部署 `基建`、`ANS`、`创作` 三大域核心技能，供项目内双端调用 |
| **🟢 Mac Codex 端** | `~/.codex/skills/` | 统一通过软链指向 SSOT，供 GPT-6.1-sol 工程师调用 |
| **🖥️ Windows 远端** | `%USERPROFILE%\.claude\skills\` | 通过 `ans-skills win-sync` 从 Mac SSOT 一键镜像拉取 |

### 六大业务域分组（与 `session-sync` 38 条主线程领域 1:1 对齐）
1. **`基建`**：`session-sync`、`agy-session-sync`、`token-budget`、`cc-ip-skill`、`ans-skill-manager-skill`、`agent-reach`、`ego-browser`、`ship-speed`
2. **`ANS`**：`ans-ui-ux-baseline`、`ans-brand-film-live-tour`、`ans-hyperframes-launch-video`、`ans-p5-brush-cartoon-animation`、`ans-eternal-pet-3d-pipeline`、`oil-ui`、`oil-frontend`、`oil-tone`、`oil-cover`、`oil-visual`、`ui-ux-guide`、`beautify-github-readme`、`visual-explainer`
3. **`创作`**：`mg-styles-15`、`image2.5genskill`、`bulkgen`、`khazix-writer`、`shuorenhua`、`sprint-prompt`
4. **`NB小科`**：`nb-science-*`（7 项）、`nb-seedance-video-skill`、`nb-xiaoke-cover-prompt`、`nb-worklog-feishu-matrix`、`openlux-tripo-blender`、`xiaoke-studio`、`threejs-aaa-graphics-builder`、`threejs-qa-release`、`game-feel`、`game-polish`、`game-taste-3d`、`anatomy`
5. **`Beersong`**：`beersong-patent-bidding-skill`、`patent-disclosure-skill`

---

## 二、零 Token 终端命令速查（`ans-skills`）

已注册全局命令 `~/.local/bin/ans-skills`（源码：[`~/.claude/scripts/ans-skills.py`](file://~/.claude/scripts/ans-skills.py)）：

```bash
# 1. 查看全舰队 48 个技能的五端同步状态矩阵与业务域分类（只读零副作用）
ans-skills
# （或 ans-skills status / ans-skills list）

# 2. 一键将散落在各端的技能收编入 SSOT，并在 Claude / 反重力 / ANS / Codex 四端建立 1:1 软链 + 更新 registry/skills.json + 跑安全审计
ans-skills sync --apply

# 3. 在内置的 31,767+ 社区中文技能库中检索开源 Skill（按匹配度与 GitHub Star 排序）
ans-skills search <关键词>
# 示例：ans-skills search "nextjs"  或  ans-skills search "视频"  或  ans-skills search "patent"

# 4. 完整文件夹级拉取外部 GitHub Skill（支持整仓 URL 或 /tree/branch/subpath 子目录 Sparse Checkout）并自动部署到全端
ans-skills install <GitHub-URL-或-owner/repo>

# 5. 运行原版 buzhangsan/skill-manager Node.js CLI（如需使用原版交互式检索/SVN导出）
node ~/.claude/skill-manager-db/src/index.js search "<关键词>"

# 6. 执行 SSOT 安全与体积门禁审计（检查是否有私钥/Token 泄露或 >50MB 超大文件）
ans-skills audit

# 7. 打印 Windows 台式机一键同步全量技能的 PowerShell 口令
ans-skills win-sync
```

---

## 三、外部开源 Skill 安装与内化铁律（Agent 必遵）

1. **严禁伪造或只下单个 `SKILL.md`**：
   - 使用 `ans-skills install <url>` 时，脚本会自动通过 **Git Sparse Checkout** 或 **Shallow Clone** 将目标技能的**整个文件夹**（含 `SKILL.md`、`references/*`、`scripts/*`、`examples/*`、`assets/*`）完整拉入 `SSOT`。
2. **需要内化改造为 ANS 专属技能时**：
   - 必须先将上游原版 `SKILL.md` 与核心脚本完整保存在该技能的 `references/upstream-*.md` 和 `scripts/upstream-*` 中，再在此基础上叠加 ANS 业务逻辑与五端同步规则。
3. **自动体积净化与门禁**：
   - `ans-skills sync --apply` 在收编技能入 `SSOT` 时，会自动跳过 `node_modules`、`.git`、`out/` 以及 `>48MB` 的本地渲染视频产物（如 `.mp4` / `.wav`），确保 `personal-skill-system` Git 仓库永远轻量且 100% 通过 `audit.py` 门禁。


## 🧭 四大系统 Skill 专职主线程分工表（1:1 专款专用）

| 系统 Skill | 专职主线程名称（双端 1:1 统一） | 反重力 ID | 负责范围（严禁跨线程混杂） |
| :--- | :--- | :--- | :--- |
| **`session-sync` + `agy-session-sync`** | **`基建 · 会话管理与记忆同步`** | `1d11a3ce` | 仅负责 Mac 四号 × 反重力 × Win 双号侧边栏 1:1 同步、去重、恢复与 `.auto.md` 交接 |
| **`token-budget`** | **`基建 · Token额度精算与排班`** | `0b00d69d` | 专职负责 6 号 + 反重力 5h/7d 额度监控、每日预算定额、切号顺位与 `>200k` 巨兽治理 |
| **`cc-ip-skill`** | **`基建 · 兔子代理与防封哨兵`** | `6cd33893` | 专职负责唯一权威代理「兔子 (`<YOUR_SAFE_EXIT_IP>`)」、清零 IPPeak、跳 IP 秒关保号与双机同步 |
| **`ans-skill-manager-skill`** | **`基建 · Skill中枢管理与同步`** | `082a08f4` | 专职负责 47+ 本地技能五端对齐、31,767+ 社区技能检索、原版安装与 `audit.py` 门禁 |
