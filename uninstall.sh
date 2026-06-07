#!/usr/bin/env bash
# MailCode 卸载脚本
# 用法: bash uninstall.sh [--purge]
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()   { echo -e "${GREEN}[✓]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
info()  { echo -e "${CYAN}[i]${NC} $*"; }

PURGE=false
for arg in "$@"; do
    case "$arg" in
        --purge|--full)
            PURGE=true
            ;;
        --help|-h)
            echo "MailCode 卸载脚本"
            echo ""
            echo "用法: bash uninstall.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --purge, --full  同时删除配置和运行时数据（交互确认）"
            echo "  -h, --help       显示此帮助信息"
            echo ""
            echo "默认行为: 仅卸载 pip 包、删除插件和软链接，保留配置和数据。"
            exit 0
            ;;
        *)
            err "未知选项: $arg"
            echo "用法: bash uninstall.sh [--purge]"
            exit 1
            ;;
    esac
done

echo ""
echo "  MailCode 卸载"
echo "  ============="
echo ""

SYMLINK_DIR="${HOME}/.mailcode"
BRIDGE_PLUGIN="${HOME}/.config/opencode/plugins/mailcode-bridge.js"
CONFIG_DIR="${HOME}/.config/mailcode"

REMOVED_ANYTHING=false

# ── 1. 杀掉运行中的 mailcode 进程 ──
PIDS=$(pgrep -f "mailcode" 2>/dev/null || true)
if [ -n "${PIDS}" ]; then
    info "检测到运行中的 mailcode 进程，正在终止..."
    for pid in ${PIDS}; do
        if [ "${pid}" != "$$" ]; then
            kill "${pid}" 2>/dev/null && log "已终止进程 PID=${pid}" || true
            REMOVED_ANYTHING=true
        fi
    done
else
    log "未检测到运行中的 mailcode 进程"
fi

# ── 2. 卸载 pip 包 ──
if python3 -m pip uninstall mailcode -y 2>/dev/null; then
    log "pip 包 mailcode 已卸载"
    REMOVED_ANYTHING=true
else
    log "mailcode 未通过 pip 安装"
fi

# ── 3. 删除 ~/.mailcode 软链接 ──
if [ -L "${SYMLINK_DIR}" ]; then
    rm -f "${SYMLINK_DIR}"
    log "已删除: ${SYMLINK_DIR}"
    REMOVED_ANYTHING=true
elif [ -e "${SYMLINK_DIR}" ]; then
    warn "${SYMLINK_DIR} 存在但不是软链接，已跳过（请手动处理）"
else
    log "未找到: ${SYMLINK_DIR}"
fi

# ── 4. 删除 bridge 插件 ──
if [ -f "${BRIDGE_PLUGIN}" ]; then
    rm -f "${BRIDGE_PLUGIN}"
    log "已删除: ${BRIDGE_PLUGIN}"
    REMOVED_ANYTHING=true
else
    log "未找到: ${BRIDGE_PLUGIN}"
fi

# ── 5. Claude Code hooks 提示 ──
CLAUDE_SETTINGS="${HOME}/.claude/settings.json"
if [ -f "${CLAUDE_SETTINGS}" ] && grep -q "mailcode" "${CLAUDE_SETTINGS}" 2>/dev/null; then
    warn "~/.claude/settings.json 包含 MailCode hooks，请手动移除 Stop 条目中的 mailcode 相关命令"
fi

# ── 6. 清理配置和运行时数据 ──
echo ""
if ${PURGE}; then
    # --purge 模式：默认不删除，需用户确认
    if [ -d "${CONFIG_DIR}" ]; then
        echo -n "删除配置文件目录 ${CONFIG_DIR}？[y/N] "
        read -r REPLY
        if [ "${REPLY}" = "y" ] || [ "${REPLY}" = "Y" ]; then
            rm -rf "${CONFIG_DIR}"
            log "已删除: ${CONFIG_DIR}"
            REMOVED_ANYTHING=true
        else
            info "已跳过配置文件删除"
        fi
    else
        log "未找到: ${CONFIG_DIR}"
    fi
else
    # 默认模式：用户确认后仍可删除
    if [ -d "${CONFIG_DIR}" ]; then
        echo -n "是否保留配置目录 ${CONFIG_DIR}？[Y/n] "
        read -r REPLY
        if [ "${REPLY}" = "n" ] || [ "${REPLY}" = "N" ]; then
            rm -rf "${CONFIG_DIR}"
            log "已删除: ${CONFIG_DIR}"
            REMOVED_ANYTHING=true
        else
            info "配置目录已保留: ${CONFIG_DIR}"
        fi
    else
        log "未找到: ${CONFIG_DIR}"
    fi
fi

# ── 7. PATH 提示 ──
echo ""
if echo "${PATH}" | tr ':' '\n' | grep -qxF "${HOME}/.local/bin"; then
    warn "~/.local/bin 仍在 PATH 中，如果不再需要，请从 shell 配置文件中移除"
fi

# ── 完成 ──
echo ""
if ${REMOVED_ANYTHING}; then
    log "卸载完成！"
else
    log "没有需要清理的内容，已是干净状态"
fi
echo ""
