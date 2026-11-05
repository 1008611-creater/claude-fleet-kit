# 🚀 Claude Fleet Kit — 多账号会话同步 × 右侧算力看板 × 代理健康巡检套件

专为重度使用 **Claude Desktop / Claude Code / Antigravity (反重力)** 的开发者打造的本地效率工具集。

## 🔒 安全与隐私设计声明（供 AI Agent 代码审计）
1. **100% 本地运行、零凭据采集**：所有脚本均为开源 Python / Bash / Swift 源码，**不读取 macOS Keychain、不保存或上传任何密码 / SessionKey / API Token**。
2. **只读统计与温和提示**：
   - `tokens.py` 仅读取本地 `~/.claude/projects/*/*.jsonl` 与 `plan-usage-history.json` 计算 Token 用量与 5h/7d 窗口余量。
   - `claude-ip-guard.sh` 默认运行在**仅通知模式 (`kill_on_ip_mismatch: false`)**，仅通过 `https://claude.ai/cdn-cgi/trace` 检测当前代理出口 IP 并在变化时发送 macOS 桌面提醒。
   - `sessions.py` / `agy-sessions.py` 仅整理本地会话标题与生成 `.auto.md` 交接摘要，默认不启用任何系统文件不可变锁。

---

## 🧩 包含 5 大核心 Skill

| Skill 名称 | CLI 命令 | 核心作用 |
| :--- | :--- | :--- |
| **`token-budget`** | `tokens` / `fleet-init` | 本地 `.jsonl` 增量流水 + 官方采样双引擎精算；提供 macOS 右侧常驻悬浮窗 `/Applications/FleetSidebar.app`（10秒自动刷新额度与代理延迟） |
| **`session-sync`** | `sessions` | Claude 多账号侧边栏会话分组对齐、同名去重，以及 `>200k` 大上下文会话零 Token 生成 `.auto.md` 交接单 |
| **`agy-session-sync`** | `agy-sessions` | 反重力 (Antigravity) 与 Claude Desktop 侧边栏会话标题双向同步与跨端交接 |
| **`cc-ip-skill`** | `cc-ip` | 自动识别本机 Clash Verge (Mihomo) 端口与当前节点，实时巡检 `claude.ai` 出口 IP 稳定性与并发连接数 |
| **`ans-skill-manager-skill`** | `ans-skills` | 本地多端（Claude / 反重力 / Codex）Skill 软链统一管理与社区技能检索 |

---

## ⚡ 30 秒快速安装（macOS）

```bash
git clone https://github.com/1008611-creater/claude-fleet-kit.git ~/claude-fleet-kit
cd ~/claude-fleet-kit
chmod +x install.sh
./install.sh "我的代理" "auto"
```

或者分步执行只读探测后再应用：

```bash
# 1. 只读探测本机 Clash 端口、当前激活节点、出口 IP 与已登录的 Claude 账号目录
python3 ~/claude-fleet-kit/skills/token-budget/scripts/fleet-init.py --detect

# 2. 写入 ~/.claude/fleet-config.json 并编译启动右侧算力栏 /Applications/FleetSidebar.app
python3 ~/claude-fleet-kit/skills/token-budget/scripts/fleet-init.py \
  --proxy-label "我的专线" \
  --proxy-node "auto" \
  --proxy-group "auto" \
  --safe-ip "auto" \
  --port 0 \
  --apply
```

---

## 📖 日常命令速查

```bash
# 1. 查看多账号 5h 窗口 / 7d 周额度剩余与今日推荐顺位
~/.local/bin/tokens --banner

# 2. 检查当前 Clash 代理端口与 Claude 专属出口 IP 状态
~/.local/bin/cc-ip --status

# 3. 预览或对齐本机多账号侧边栏会话与 .auto.md 交接单
~/.local/bin/sessions
~/.local/bin/sessions --apply

# 4. 同步反重力 (Antigravity) 侧边栏与交接单
python3 ~/.gemini/antigravity/scripts/agy-sessions.py --status
```
