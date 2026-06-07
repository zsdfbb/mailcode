#!/usr/bin/env bash
# MailCode 安装脚本（pip 安装 + 配置初始化）
set -euo pipefail

MAILCODE_DIR="$(cd "$(dirname "$0")" && pwd)"
# 自动检测 pip --user 安装的 bin 目录
INSTALL_DIR="$(python3 -c "import site; print(site.USER_BASE + '/bin')" 2>/dev/null || echo "${HOME}/.local/bin")"
LOCAL_WHEEL=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${CYAN}[i]${NC} $*"; }

# ── 参数解析 ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)
            if [[ -z "${2:-}" ]]; then
                err "--local 需要指定 wheel 文件路径"
                exit 1
            fi
            LOCAL_WHEEL="$2"
            shift 2
            ;;
        -h|--help)
            echo "用法: bash install.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --local <wheel>  从本地 wheel 文件安装"
            echo "  -h, --help       显示此帮助"
            echo ""
            echo "示例:"
            echo "  bash install.sh                                    # 提示从 PyPI 安装或使用 --local"
            echo "  bash install.sh --local dist/mailcode-0.3.0-py3-none-any.whl"
            exit 0
            ;;
        *)
            warn "未知参数: $1 (使用 --help 查看帮助)"
            shift
            ;;
    esac
done

echo ""
echo "  MailCode 安装"
echo "  ============="
echo ""

# ── 1. 检查环境 ──
if ! command -v python3 &>/dev/null; then
    err "未找到 python3，请先安装 Python 3"
    exit 1
fi
log "Python3: $(command -v python3)"

PIP_CMD="pip"
if command -v pip3 &>/dev/null; then
    PIP_CMD="pip3"
fi
if ! command -v ${PIP_CMD} &>/dev/null; then
    err "未找到 pip，请先安装 pip: python3 -m ensurepip"
    exit 1
fi
log "pip: $(command -v ${PIP_CMD})"

# 判断是否在虚拟环境（--user 在 venv 中不可用）
IN_VENV=$(python3 -c "import sys; print(sys.prefix != sys.base_prefix)")
if [ "$IN_VENV" = "True" ]; then
    PIP_FLAGS=""
else
    # --break-system-packages 在 PEP 668 环境是必需，在非 PEP 668 环境被忽略
    PIP_FLAGS="--user --break-system-packages"
fi

# ── 2. 安装 mailcode 包 ──
if [ -n "${LOCAL_WHEEL}" ]; then
    if [ ! -f "${LOCAL_WHEEL}" ]; then
        ALT_PATH="${MAILCODE_DIR}/${LOCAL_WHEEL}"
        if [ -f "${ALT_PATH}" ]; then
            LOCAL_WHEEL="${ALT_PATH}"
        else
            err "wheel 文件不存在: ${LOCAL_WHEEL}"
            exit 1
        fi
    fi
    info "从本地 wheel 安装: ${LOCAL_WHEEL}"
    # shellcheck disable=SC2086
    if ! ${PIP_CMD} install ${PIP_FLAGS} "${LOCAL_WHEEL}" 2>&1 | sed 's/^/  /'; then
        err "pip 安装失败，请检查错误信息后重试"
        exit 1
    fi
    log "mailcode 已通过 pip 安装 (本地 wheel)"
else
    # 默认流程：先 build 出 wheel，再从 wheel 安装（与 PyPI 用户拿到的产物一致）
    info "构建 wheel（首次约 30-60 秒）: bash ${MAILCODE_DIR}/build.sh"
    if ! bash "${MAILCODE_DIR}/build.sh" 2>&1 | sed 's/^/  /'; then
        err "wheel 构建失败，请检查错误信息后重试"
        exit 1
    fi
    # 找到 build.sh 产出的 wheel
    BUILT_WHEEL=$(ls -t "${MAILCODE_DIR}"/dist/mailcode-*.whl 2>/dev/null | head -1)
    if [ -z "${BUILT_WHEEL}" ]; then
        err "build.sh 未产出 wheel: ${MAILCODE_DIR}/dist/"
        exit 1
    fi
    info "从构建产物安装: ${BUILT_WHEEL}"
    # shellcheck disable=SC2086
    if ! ${PIP_CMD} install ${PIP_FLAGS} --force-reinstall "${BUILT_WHEEL}" 2>&1 | sed 's/^/  /'; then
        err "pip 安装失败，请检查错误信息后重试"
        exit 1
    fi
    log "mailcode 已通过 pip 安装 (本地构建的 wheel)"
fi

# 确保刚安装的 mailcode 在 PATH 中（macOS 可能装到 site.USER_BASE 下）
export PATH="${INSTALL_DIR}:${PATH}"
hash -r 2>/dev/null || true

# ── 3. 初始化/升级配置 ──
mailcode config init

log "配置已就绪"
warn "请编辑配置填入邮箱和密码: ~/.config/mailcode/config.json"
echo ""

# ── 4. 创建符号链接 ~/.mailcode → MAILCODE_DIR ──
if [ ! -d "${HOME}/.mailcode" ]; then
    ln -sf "${MAILCODE_DIR}" "${HOME}/.mailcode"
    log "已创建: ~/.mailcode → ${MAILCODE_DIR}"
fi

# ── 5. 自动添加 PATH ──
if ! echo "${PATH}" | tr ':' '\n' | grep -qxF "${INSTALL_DIR}"; then
    # 自动写入 shell rc 文件
    case "${SHELL:-}" in
        *zsh*)   RC_FILE="${HOME}/.zshrc" ; LINE="export PATH=\"${INSTALL_DIR}:\${PATH}\"" ;;
        *bash*)  RC_FILE="${HOME}/.bashrc" ; LINE="export PATH=\"${INSTALL_DIR}:\${PATH}\"" ;;
        *fish*)  RC_FILE="${HOME}/.config/fish/config.fish" ; LINE="fish_add_path ${INSTALL_DIR}" ;;
        *)       RC_FILE="" ;;
    esac
    if [ -n "${RC_FILE}" ] && [ -f "${RC_FILE}" ]; then
        if [ "${SHELL##*/}" = "fish" ]; then
            if ! grep -qxF "fish_add_path ${INSTALL_DIR}" "${RC_FILE}" 2>/dev/null; then
                echo "" >> "${RC_FILE}"
                echo "# MailCode" >> "${RC_FILE}"
                echo "${LINE}" >> "${RC_FILE}"
                log "已自动添加 PATH 到 ${RC_FILE}"
                info "执行以下命令立即生效: source ${RC_FILE}"
            fi
        else
            if ! grep -qxF "export PATH=\"${INSTALL_DIR}" "${RC_FILE}" 2>/dev/null; then
                echo "" >> "${RC_FILE}"
                echo "# MailCode" >> "${RC_FILE}"
                echo "${LINE}" >> "${RC_FILE}"
                log "已自动添加 PATH 到 ${RC_FILE}"
                info "执行以下命令立即生效: source ${RC_FILE}"
            fi
        fi
    else
        warn "${INSTALL_DIR} 不在 PATH 中，请手动添加到 shell rc 文件:"
        echo "  ${LINE}"
    fi
fi

echo ""
# ── 6. 创建符号链接 ──
ln -sf "${HOME}/.config/mailcode/config.json" "${MAILCODE_DIR}/user_config.json"
log "已创建: user_config.json → ${HOME}/.config/mailcode/config.json"
ln -sf "${HOME}/.config/mailcode/test_config.json" "${MAILCODE_DIR}/test_config.json"
log "已创建: test_config.json → ${HOME}/.config/mailcode/test_config.json"

# ── 7. 初始化测试配置文件（如不存在） ──
if [ ! -f "${HOME}/.config/mailcode/test_config.json" ]; then
    if command -v mailcode &>/dev/null; then
        mailcode config init-test 2>/dev/null || true
    fi
fi

log "安装完成！"
echo ""
info "启动中继:"
echo "  mailcode serve --idle"
echo ""
