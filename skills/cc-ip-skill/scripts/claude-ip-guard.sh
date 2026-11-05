#!/usr/bin/env bash
# Claude 双机 IP 哨兵 v2.6 (分流零误杀版 · 支持 ~/.claude/fleet-config.json)
# ---------------------------------------------------------------------------
# 默认锁死「兔子 0.0.0.0 (auto)」；
# 他人复用时，自动从 ~/.claude/fleet-config.json 读取自定义代理名、节点名、端口与白名单出口 IP！
#
# v2.6 关键修复（彻底消除 Clash 分流下误把「其他流量」当成跳 IP 的误杀）：
#   1) 【严禁探测非 Claude 域名】彻底移除 checkip.amazonaws.com / icanhazip.com / ipinfo.io！
#      因为在 Clash 分流配置下，这些非 Claude 域名走的是「其他流量」(如 StepFly 68.77.201.23)，
#      一旦兔子瞬时满载超时，fallback 到 checkip 就会拿到「其他流量」的 IP 导致误杀 Claude！
#   2) 【仅探测 Claude 官方域名路由】只请求 https://claude.ai/cdn-cgi/trace 与 https://claude.com/cdn-cgi/trace，
#      确保 100% 走「GLOBAL」专属分流通道。
#   3) 【内核级锁死免误杀】当 macOS 系统代理 (7897) 开启 且 Mihomo 内核确认「GLOBAL」锁死在
#      「auto」时，若因高并发导致 trace 超时未返回，直接信任内核路由锁死状态，绝不误杀！
#   4) 【真跳 IP 零容忍】若系统代理被关、策略组节点被切走、或 claude.ai/cdn-cgi/trace 返回了非白名单 IP，
#      当场秒杀 Claude 保号。
# ---------------------------------------------------------------------------

set -u

FLEET_CFG_FILE="${FLEET_CFG_FILE:-$HOME/.claude/fleet-config.json}"
SAFE_IP_FILE="${GUARD_SAFE_IP_FILE:-$HOME/.claude/safe_ip.txt}"
LOG_FILE="${GUARD_LOG_FILE:-$HOME/.claude/ip-guard.log}"
WARN_STAMP="${GUARD_WARN_STAMP:-$HOME/.claude/.ip-guard-last-warn}"
VERIFY_CACHE="$HOME/.claude/.ip-guard-verified"
LOG_MAX_BYTES=524288

BUSY_INTERVAL="${GUARD_INTERVAL:-8}"
IDLE_INTERVAL="${GUARD_IDLE_INTERVAL:-30}"
GRACE_TICKS="${GUARD_GRACE_TICKS:-6}"

load_cfg() {
    eval "$(/usr/bin/python3 -c '
import json, os, shlex
p = os.path.expanduser("~/.claude/fleet-config.json")
cfg = {}
if os.path.exists(p):
    try:
        cfg = json.load(open(p, encoding="utf-8")).get("proxy") or {}
    except Exception:
        pass
uid = os.getuid()
sock = cfg.get("mihomo_sock") or f"/var/run/clash-verge-service/users/{uid}/verge-mihomo.sock"
if not os.path.exists(sock) and os.path.exists("/var/run/clash-verge-service/users/501/verge-mihomo.sock"):
    sock = "/var/run/clash-verge-service/users/501/verge-mihomo.sock"
label = cfg.get("label") or "默认代理"
node = cfg.get("node") or "auto"
group = cfg.get("group") or "GLOBAL"
sip = cfg.get("safe_ip") or "0.0.0.0"
port = str(cfg.get("port") or 7897)
kill_flag = "1" if cfg.get("kill_on_ip_mismatch", False) else "0"
print(f"PROXY_LABEL={shlex.quote(label)}")
print(f"PROXY_NODE={shlex.quote(node)}")
print(f"PROXY_GROUP={shlex.quote(group)}")
print(f"DEFAULT_SAFE_IP={shlex.quote(sip)}")
print(f"PROXY_PORT={shlex.quote(port)}")
print(f"MIHOMO_SOCK={shlex.quote(sock)}")
print(f"KILL_ON_MISMATCH={shlex.quote(kill_flag)}")
' 2>/dev/null)"
    PROXY_URL="${GUARD_PROXY_URL:-http://127.0.0.1:${PROXY_PORT:-7897}}"
}

load_cfg
mkdir -p "$(dirname "$SAFE_IP_FILE")" "$(dirname "$LOG_FILE")" 2>/dev/null || true
[ -f "$SAFE_IP_FILE" ] || echo "$DEFAULT_SAFE_IP" > "$SAFE_IP_FILE"

get_safe_ip() {
    tr -d ' \r\n' < "$SAFE_IP_FILE" 2>/dev/null || echo "$DEFAULT_SAFE_IP"
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

rotate_log() {
    if [ -f "$LOG_FILE" ]; then
        local sz
        sz=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
        if [ "$sz" -gt "$LOG_MAX_BYTES" ]; then
            mv "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
        fi
    fi
}

# 仅通过 Claude 官方域名探测出口 IP，确保 100% 命中 Clash 中的 Claude 专属分流规则！
# 严禁请求 checkip.amazonaws.com / icanhazip.com，否则在分流配置下会测到「其他流量」节点导致误杀！
probe_claude_exit_ip() {
    local ip=""
    for url in "https://claude.ai/cdn-cgi/trace" "https://claude.com/cdn-cgi/trace"; do
        ip=$(curl -x "$PROXY_URL" -s --max-time 8 "$url" 2>/dev/null | awk -F= '/^ip=/{print $2}' | tr -d ' \r\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)
        if [ -n "$ip" ]; then
            echo "$ip"
            return 0
        fi
    done
    echo ""
}

fetch_current_ip() {
    load_cfg
    local sys_proxy
    sys_proxy=$(scutil --proxy 2>/dev/null | grep -E "HTTPSEnable : 1|HTTPEnable : 1" || true)
    if [ -z "$sys_proxy" ]; then
        echo "SYSTEM_PROXY_OFF"
        return 0
    fi

    local mihomo_state=""
    if [ -S "$MIHOMO_SOCK" ]; then
        mihomo_state=$(curl --unix-socket "$MIHOMO_SOCK" -s --max-time 2 http://localhost/proxies 2>/dev/null | /usr/bin/python3 -c "
import sys, json
target_group = '''$PROXY_GROUP'''
target_node = '''$PROXY_NODE'''
try:
    p = json.load(sys.stdin).get('proxies', {})
    g = p.get(target_group) or p.get('🚀 默认代理 (主力兔子)') or p.get('节点选择') or p.get('GLOBAL') or {}
    now_node = g.get('now', '')
    if target_node and target_node in p:
        if now_node and now_node != target_node and now_node != target_group:
            print('NODE_CHANGED:' + now_node)
        else:
            print('NODE_LOCKED')
    else:
        print('NODE_LOCKED')
except Exception:
    pass
" 2>/dev/null || true)

        if [[ "$mihomo_state" == NODE_CHANGED:* ]]; then
            echo "$mihomo_state"
            return 0
        fi

        # 只要 Mihomo 内核确认 Claude 策略组锁死在唯一目标节点，且 1800 秒（30分钟）内已验证过一次真实出口 IP，
        # 直接返回白名单缓存，不在后台高频发起跨洋 TLS 握手抢占隧道并发！
        if [ "$mihomo_state" = "NODE_LOCKED" ] && [ -f "$VERIFY_CACHE" ]; then
            local now_ts last_ts cached_ip
            now_ts=$(date +%s)
            last_ts=$(awk '{print $1}' "$VERIFY_CACHE" 2>/dev/null || echo 0)
            cached_ip=$(awk '{print $2}' "$VERIFY_CACHE" 2>/dev/null || echo "")
            if [ -n "$cached_ip" ] && [ "$cached_ip" = "$(get_safe_ip)" ] && [ $((now_ts - last_ts)) -lt 1800 ]; then
                echo "$cached_ip"
                return 0
            fi
        fi
    fi

    local ip
    ip=$(probe_claude_exit_ip)
    if [ -n "$ip" ]; then
        if [ "$ip" = "$(get_safe_ip)" ]; then
            echo "$(date +%s) $ip" > "$VERIFY_CACHE"
        fi
        echo "$ip"
        return 0
    fi

    # 若 claude.ai/cdn-cgi/trace 因单隧道高并发瞬时超时未返回，但本地 Mihomo 内核明确处于 NODE_LOCKED 状态且系统代理开启，
    # 说明路由并未跳变（只是隧道繁忙），直接返回 SAFE_IP 避免误杀正在跑任务的 Claude！
    if [ "$mihomo_state" = "NODE_LOCKED" ]; then
        echo "$(get_safe_ip)"
        return 0
    fi
    echo ""
}

claude_is_running() {
    pgrep -f "/Applications/Claude.app/Contents/MacOS/Claude" >/dev/null 2>&1 && return 0
    pgrep -f "\.claude/remote/ccd-cli" >/dev/null 2>&1 && return 0
    pgrep -f "\.claude/remote/srv" >/dev/null 2>&1 && return 0
    pgrep -x "claude" >/dev/null 2>&1 && return 0
    return 1
}

notify() {
    local title="$1" msg="$2"
    osascript -e "display notification \"$msg\" with title \"$title\" sound name \"Basso\"" >/dev/null 2>&1 || true
}

warn_once() {
    local msg="$1"
    local now last
    now=$(date +%s)
    last=$(cat "$WARN_STAMP" 2>/dev/null || echo 0)
    if [ $((now - last)) -ge 600 ]; then
        echo "$now" > "$WARN_STAMP"
        notify "🛡️ Claude IP 哨兵 · 先别开" "$msg"
        log "⚠️ 预警: $msg"
    fi
}

kill_claude_now() {
    local reason="$1"
    if [ "${KILL_ON_MISMATCH:-1}" = "0" ] || [ "${GUARD_WARN_ONLY:-0}" = "1" ]; then
        log "⚠️ 仅告警模式: $reason"
        warn_once "Claude 出口异常（$reason），请立即检查代理！"
        return 0
    fi
    log "🚨 断路秒杀: $reason — 强制关闭 Claude 全部进程保号"
    killall -9 "Claude" >/dev/null 2>&1 || true
    pkill -9 -f "/Applications/Claude.app" >/dev/null 2>&1 || true
    pkill -9 -f "\.claude/remote/ccd-cli" >/dev/null 2>&1 || true
    pkill -9 -f "\.claude/remote/srv" >/dev/null 2>&1 || true
    pkill -9 -x "claude" >/dev/null 2>&1 || true
    notify "🛡️ Claude IP 哨兵已断路" "$reason，已自动秒关 Claude 保号！"
}

case "${1:-}" in
    --set)
        NEW_IP="${2:-$(probe_claude_exit_ip)}"
        if [ -z "$NEW_IP" ]; then
            echo "❌ 探测不到当前 Claude 路由出口 IP，请手动指定: cc-ip --set <IP>"
            exit 1
        fi
        echo "$NEW_IP" > "$SAFE_IP_FILE"
        /usr/bin/python3 -c "
import json, os
p = os.path.expanduser('~/.claude/fleet-config.json')
cfg = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
cfg.setdefault('proxy', {})['safe_ip'] = '''$NEW_IP'''
json.dump(cfg, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
" 2>/dev/null || true
        echo "$(date +%s) $NEW_IP" > "$VERIFY_CACHE"
        echo "✅ 白名单出口 IP 已更新为: $NEW_IP (已同步写入 ~/.claude/safe_ip.txt 与 fleet-config.json)"
        log "白名单更新为 $NEW_IP"
        exit 0
        ;;
    --set-node)
        NEW_NODE="${2:-}"
        NEW_LABEL="${3:-${2:-自定义代理}}"
        if [ -z "$NEW_NODE" ]; then
            echo "❌ 用法: cc-ip --set-node <Clash节点名> [侧边栏显示简称]"
            exit 1
        fi
        /usr/bin/python3 -c "
import json, os
p = os.path.expanduser('~/.claude/fleet-config.json')
cfg = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
px = cfg.setdefault('proxy', {})
px['node'] = '''$NEW_NODE'''
px['label'] = '''$NEW_LABEL'''
json.dump(cfg, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
"
        rm -f "$VERIFY_CACHE" 2>/dev/null || true
        echo "✅ 锁定节点已更新为: $NEW_NODE (显示简称: $NEW_LABEL)"
        exit 0
        ;;
    --status|--once)
        SAFE_IP=$(get_safe_ip)
        CUR_IP=$(fetch_current_ip)
        echo "🛡️ 代理简称 : $PROXY_LABEL (锁定节点: $PROXY_NODE | 策略组: $PROXY_GROUP | 端口: $PROXY_PORT)"
        echo "🛡️ 白名单 IP : $SAFE_IP"
        echo "🌐 Claude 出口: ${CUR_IP:-<探测不到>}"
        if [ "$CUR_IP" = "$SAFE_IP" ]; then
            echo "✅ Claude 专属出口 IP 100% 匹配 ($SAFE_IP)，分流隔离安全受控"
            exit 0
        elif [ -n "$CUR_IP" ]; then
            echo "🚨 出口异常: ${CUR_IP}（非白名单 ${SAFE_IP}）"
            claude_is_running && kill_claude_now "出口跳变为 ${CUR_IP}（白名单 ${SAFE_IP}）"
            exit 2
        else
            echo "⚠️ 探测不到出口 IP（代理可能未启动或超时）"
            exit 3
        fi
        ;;
esac

rotate_log
log "🛡️ 哨兵 v2.6 (分流零误杀版) 启动 | 代理: ${PROXY_LABEL} (${PROXY_GROUP} -> ${PROXY_NODE}) | 白名单 IP: $(get_safe_ip)"

FAIL_COUNT=0

while true; do
    RUNNING=0
    claude_is_running && RUNNING=1

    SAFE_IP=$(get_safe_ip)
    CUR_IP=$(fetch_current_ip)

    if [ -n "$CUR_IP" ] && [ "$CUR_IP" = "$SAFE_IP" ]; then
        FAIL_COUNT=0
        if [ "$RUNNING" -eq 1 ]; then SLEEP=$BUSY_INTERVAL; else SLEEP=$IDLE_INTERVAL; fi
    elif [ -n "$CUR_IP" ]; then
        FAIL_COUNT=0
        if [ "$RUNNING" -eq 1 ]; then
            kill_claude_now "Claude 出口异常 ${CUR_IP}（白名单 ${SAFE_IP}）"
            SLEEP=$BUSY_INTERVAL
        else
            warn_once "当前 Claude 出口异常 ($CUR_IP != $SAFE_IP)，先把代理切回 $PROXY_NODE 再开 Claude。"
            SLEEP=$IDLE_INTERVAL
        fi
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        if [ "$RUNNING" -eq 1 ]; then
            if [ "$FAIL_COUNT" -ge "$GRACE_TICKS" ]; then
                kill_claude_now "代理 ${PROXY_PORT} 连续 ${FAIL_COUNT} 次探测不到，防直连裸奔"
                FAIL_COUNT=0
            elif [ "$FAIL_COUNT" -eq 3 ]; then
                warn_once "代理 ${PROXY_PORT} 探测不到出口 IP，宽限期过后会自动关闭 Claude。"
            fi
            SLEEP=$BUSY_INTERVAL
        else
            if [ "$FAIL_COUNT" -eq 3 ]; then
                warn_once "代理 ${PROXY_PORT} 探测不到出口 IP，先别开 Claude。"
            fi
            SLEEP=$IDLE_INTERVAL
        fi
    fi

    sleep "$SLEEP"
done
