#!/usr/bin/env bash
# claude-fleet-kit 一键安装与初始化脚本 (macOS)
# 纯本地脚本：自动发现本机 Claude 账号与 Clash 代理端口，编译右侧算力看板 FleetSidebar.app
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PROXY_LABEL="${1:-auto}"
PROXY_NODE="${2:-auto}"

echo "================================================================================"
echo "🚀 正在部署 Claude Fleet Kit（多号会话同步 × 右侧算力看板 × 代理连通性巡检）"
echo "================================================================================"

mkdir -p ~/.claude/scripts ~/.gemini/antigravity/scripts ~/.local/bin ~/.claude/skills ~/.gemini/config/skills

cp -f "$REPO_DIR"/skills/token-budget/scripts/{tokens.py,FleetSidebar.swift,fleet-init.py} ~/.claude/scripts/
cp -f "$REPO_DIR"/skills/cc-ip-skill/scripts/claude-ip-guard.sh ~/.claude/scripts/
cp -f "$REPO_DIR"/skills/session-sync/scripts/{sessions.py,jsonl-digest.py,install-sessions-win.ps1} ~/.claude/scripts/
cp -f "$REPO_DIR"/skills/ans-skill-manager-skill/scripts/ans-skills.py ~/.claude/scripts/
cp -f "$REPO_DIR"/skills/agy-session-sync/scripts/{agy-sessions.py,agy-digest.py} ~/.gemini/antigravity/scripts/

chmod +x ~/.claude/scripts/*.py ~/.claude/scripts/*.sh ~/.gemini/antigravity/scripts/*.py

for s in session-sync agy-session-sync token-budget cc-ip-skill ans-skill-manager-skill; do
    ln -snf "$REPO_DIR/skills/$s" "$HOME/.claude/skills/$s"
    ln -snf "$REPO_DIR/skills/$s" "$HOME/.gemini/config/skills/$s"
done

python3 ~/.claude/scripts/fleet-init.py \
  --proxy-label "${PROXY_LABEL}" \
  --proxy-node "${PROXY_NODE}" \
  --proxy-group "auto" \
  --safe-ip "auto" \
  --port 0 \
  --apply

echo "================================================================================"
echo "✅ 部署完成！常用命令（已挂载至 ~/.local/bin）："
echo "   • tokens --banner   查看多账号 5h/7d 剩余额度与今日推荐顺位"
echo "   • sessions          查看与对齐多账号侧边栏会话列表"
echo "   • cc-ip --status    检查当前 Clash 代理节点与出口 IP"
echo "================================================================================"
